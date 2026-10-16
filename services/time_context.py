"""Time-of-day context (day / twilight / astronomical night) from the sun's
position at the configured observer location.

The single source of truth for the ``is_astronomical_night`` feature, used
both when building calibration JSON (training data, via
``ui/controllers/dev_mode_utils.py``) and at inference time (``MLService``), so
the roof and sky models are fed the same flag their training data carries.
Lives in services/ so services can use it without importing up into
ui/controllers.

Night is decided from the sun's elevation at the instant asked about, not from
astral's dawn/dusk event times. The event-based version this replaces went
wrong two ways at once: astral clamps every event to the calendar date in the
requested zone, so for a western-hemisphere site in UTC the "sunset" for a
date came *before* that date's sunrise, and ``twilight()`` was handed plain
ints where it compares against ``SunDirection`` enum members, so both calls
took the setting branch (and raised at high latitude). The upshot was a flag
that read "night" for most of the afternoon or all day, whatever the host's
timezone. Elevation has no calendar date to clamp and no direction to get
wrong, and stays well-defined at latitudes where the sun never reaches -18°.

That history matters for the models shipped in ``ml/models``: the flag in
their training JSON was constant, so they never learned a night/day
distinction from it. Inference now feeds them the true flag, which is closer
to their training than the 20:00–06:00 clock was, but training and inference
only genuinely agree once the calibration JSON is re-labelled
(``scripts/dev/allsky/backfill_calibration.py --force-time``) and the models
retrained on it.
"""
from datetime import datetime, timedelta, timezone
import threading
from typing import Optional, Tuple

try:
    from astral import Observer
    from astral.sun import dawn, dusk, elevation, noon, sunrise, sunset
    ASTRAL_AVAILABLE = True
except ImportError:
    ASTRAL_AVAILABLE = False

from services.logger import app_logger
from services.moon import get_configured_location

# Geometric sun elevation thresholds, degrees, compared against the
# unrefracted elevation so they mean the same thing as astral's event times:
# sunrise/sunset is the centre at -0.833° (refraction plus the upper limb),
# civil twilight ends at -6°, astronomical night (no sky glow) starts at -18°.
HORIZON_ELEVATION = -0.833
CIVIL_TWILIGHT_ELEVATION = -6.0
ASTRONOMICAL_NIGHT_ELEVATION = -18.0

_SUN_TIMES_CACHE: dict = {}
_SUN_TIMES_LOCK = threading.Lock()
_SUN_TIMES_MAX_ENTRIES = 4


def compute_time_context(now: Optional[datetime] = None,
                         location: Optional[Tuple] = None) -> dict:
    """Time-of-day context for the ML feature set and calibration JSON.

    Args:
        now: The instant to describe. Naive means host-local wall time (what
            ``datetime.now()`` returns); tz-aware is used as given. Default: now.
        location: ``(latitude, longitude, name)``; read from the weather config
            when omitted. Callers that run per frame should read it once and
            pass it in — loading the config each time is the expensive part.

    Returns a dict with ``hour``, ``minute``, ``period`` (day / twilight /
    night), ``detailed_period``, ``is_daylight``, ``is_astronomical_night``,
    ``calculation_method`` and, when astral did the work, ``location`` and
    ``sun_times`` (UTC ISO strings).
    """
    now = _as_aware(now or datetime.now())
    if location is None:
        location = get_configured_location()
    lat, lon, location_name = location

    if ASTRAL_AVAILABLE and lat is not None and lon is not None:
        try:
            return _astral_time_context(now, lat, lon, location_name)
        except Exception as e:
            app_logger.warning(f"Astral calculation failed, using fallback: {e}")
    return _simple_time_context(now)


def _as_aware(now: datetime) -> datetime:
    return now if now.tzinfo is not None else now.astimezone()


def _astral_time_context(now: datetime, lat: float, lon: float,
                         location_name: str) -> dict:
    observer = Observer(latitude=lat, longitude=lon)
    elev = elevation(observer, now, with_refraction=False)
    rising = elevation(observer, now + timedelta(minutes=10), with_refraction=False) > elev
    sun_times = _sun_times_for_day(observer, now, lon)

    period = _period_from_elevation(elev)
    detailed_period = _detailed_period(now, elev, rising, sun_times)

    return {
        'hour': now.hour,
        'minute': now.minute,
        'period': period,
        'detailed_period': detailed_period,
        'is_daylight': period == 'day',
        'is_astronomical_night': elev < ASTRONOMICAL_NIGHT_ELEVATION,
        'location': {
            'name': location_name,
            'latitude': lat,
            'longitude': lon,
        },
        'sun_times': {
            key: (value.astimezone(timezone.utc).isoformat() if value else None)
            for key, value in sun_times.items()
        },
        'calculation_method': 'astral',
    }


def _period_from_elevation(elev: float) -> str:
    if elev >= HORIZON_ELEVATION:
        return 'day'
    if elev >= CIVIL_TWILIGHT_ELEVATION:
        return 'twilight'
    return 'night'


def _detailed_period(now: datetime, elev: float, rising: bool, sun_times: dict) -> str:
    if elev < CIVIL_TWILIGHT_ELEVATION:
        return 'night'
    if elev < HORIZON_ELEVATION:
        return 'dawn' if rising else 'dusk'
    noon = sun_times.get('noon')
    sunset = sun_times.get('sunset')
    if noon is not None and now < noon:
        return 'morning'
    if sunset is not None and now >= sunset - timedelta(hours=2):
        return 'evening'
    return 'afternoon'


def _sun_times_for_day(observer, now: datetime, lon: float) -> dict:
    """astral's dawn/sunrise/noon/sunset/dusk for the observer's solar day
    containing ``now``, cached per day.

    Asked in the site's mean-solar zone (UTC + longitude/15 h), so the five
    events land on one calendar date in order regardless of the host's own
    zone — a UK host watching a Texas site would otherwise get that date's
    sunset before its sunrise. The zone is only used to pick the date; the
    times themselves are absolute.
    """
    solar_zone = timezone(timedelta(hours=round(lon / 15.0)))
    day = now.astimezone(solar_zone).date()
    key = (round(observer.latitude, 4), round(observer.longitude, 4), day)
    with _SUN_TIMES_LOCK:
        hit = _SUN_TIMES_CACHE.get(key)
    if hit is not None:
        return hit
    times = {}
    for name, event in (('dawn', dawn), ('sunrise', sunrise), ('noon', noon),
                        ('sunset', sunset), ('dusk', dusk)):
        try:
            times[name] = event(observer, day, tzinfo=solar_zone)
        except ValueError as e:
            times[name] = None
            # That event never happens on this day (polar day/night, or a
            # summer night at ~60° that never gets as dark as civil dusk).
            # Each is independent; the classification above only needs noon
            # and sunset, and degrades without them.
            app_logger.debug(f"Time context: no {name} for {day}: {e}")
    with _SUN_TIMES_LOCK:
        if len(_SUN_TIMES_CACHE) >= _SUN_TIMES_MAX_ENTRIES:
            _SUN_TIMES_CACHE.clear()
        _SUN_TIMES_CACHE[key] = times
    return times


def _hour_to_detailed_period(hour: int) -> str:
    if 5 <= hour < 8:
        return 'dawn'
    elif 8 <= hour < 12:
        return 'morning'
    elif 12 <= hour < 17:
        return 'afternoon'
    elif 17 <= hour < 20:
        return 'evening'
    elif 20 <= hour < 22:
        return 'dusk'
    return 'night'


def _simple_time_context(now: datetime) -> dict:
    """Clock-only fallback for when astral is missing or no location is set.

    The night band (22:00–05:00) is the one the training data used when it
    fell back, so a rig with no location keeps the same feature it always had.
    """
    hour = now.hour

    if 6 <= hour < 18:
        period = 'day'
    elif 18 <= hour < 21 or 5 <= hour < 6:
        period = 'twilight'
    else:
        period = 'night'

    return {
        'hour': hour,
        'minute': now.minute,
        'period': period,
        'detailed_period': _hour_to_detailed_period(hour),
        'is_daylight': 6 <= hour < 20,
        'is_astronomical_night': hour >= 22 or hour < 5,
        'calculation_method': 'simple_hour_based',
    }
