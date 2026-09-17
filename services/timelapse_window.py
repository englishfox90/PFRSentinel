"""
Recording-window schedule maths for the timelapse writer.

Pure functions over the ``timelapse`` config section: given a calendar day and
the configured window mode, return the (start, end) datetimes of that day's
recording window. Extracted from ``timelapse_writer`` so the writer keeps to
session/process management; ``WindowCache`` exists because the sun modes run
astral's solar geometry, which used to be recomputed twice per captured frame.

astral clamps sunset/sunrise/dusk/dawn to the calendar date expressed in the
``tzinfo`` it is given, which defaults to UTC. Asking for a UTC date therefore
picks the wrong night away from the prime meridian — at UTC-7 local dusk falls
on the next UTC date, so the previous evening's dusk came back and the window
never closed at dawn (issue #14). Every call here passes the *local* zone so
``day`` means the local calendar date.
"""
from datetime import datetime, date, timedelta
from typing import Tuple

from .logger import app_logger


def to_local_naive(dt: datetime, tzinfo=None) -> datetime:
    """Convert a tz-aware datetime to naive wall-clock in ``tzinfo``.

    astral returns tz-aware datetimes; the rest of the writer compares against
    datetime.now(), which is naive LOCAL. Stripping tzinfo without converting
    (the old bug) shifted the window by the host's UTC offset — hours wrong off
    the prime meridian. ``tzinfo=None`` means the system zone, since
    ``astimezone(None)`` converts to local.
    """
    return dt.astimezone(tzinfo).replace(tzinfo=None)


def _local_tzinfo(tzinfo=None):
    return tzinfo if tzinfo is not None else datetime.now().astimezone().tzinfo


def window_for_day(config: dict, day: date, tzinfo=None) -> Tuple[datetime, datetime]:
    """
    Return (window_start, window_end) for the given day.

    For overnight windows (e.g. 18:00 → 06:00) the window_end is on the
    following day. The current time is tested against windows anchored on both
    today and yesterday so sessions started yesterday are still considered
    active.
    """
    mode = config.get('window_mode', 'sun')

    if mode == 'always':
        # Full day: midnight to next midnight
        start = datetime.combine(day, datetime.min.time())
        end = datetime.combine(day + timedelta(days=1), datetime.min.time())
        return start, end

    if mode == 'fixed':
        return fixed_window(config, day)

    # Default: sun-based
    return sun_window(config, day, tzinfo)


def fixed_window(config: dict, day: date) -> Tuple[datetime, datetime]:
    """Parse fixed HH:MM start/end into datetimes, handling midnight crossing."""
    def parse_time(s: str, fallback: str) -> datetime:
        try:
            h, m = map(int, s.split(':'))
        except Exception:
            h, m = map(int, fallback.split(':'))
        return datetime.combine(day, datetime.strptime(f"{h}:{m}", "%H:%M").time())

    start = parse_time(config.get('fixed_start', '18:00'), '18:00')
    end = parse_time(config.get('fixed_end', '06:00'), '06:00')

    # If end is earlier than start, it crosses midnight → add a day
    if end <= start:
        end = end + timedelta(days=1)

    return start, end


def sun_window(config: dict, day: date, tzinfo=None) -> Tuple[datetime, datetime]:
    """Calculate the night window for ``day`` using the astral library.

    ``day`` is a LOCAL calendar date and the returned datetimes are naive
    wall-clock in ``tzinfo`` (the system zone when None). Falls back to the
    fixed window when there is no location or the sun never reaches the depth.
    """
    try:
        return strict_sun_window(config, day, tzinfo)
    except ImportError:
        app_logger.warning("Timelapse: astral not available, falling back to fixed window")
        return fixed_window(config, day)
    except Exception as e:
        # astral raises ValueError when the sun never reaches the depression
        # (high-latitude summer) — a fixed window is better than no timelapse.
        app_logger.warning(f"Timelapse: sun window error ({e}), falling back to fixed window")
        return fixed_window(config, day)


def strict_sun_window(config: dict, day: date, tzinfo=None) -> Tuple[datetime, datetime]:
    """sun_window() without the fixed-window fallback: raises instead.

    For callers that must tell the user *why* the fixed times are in force
    (the Timelapse panel's window forecast) rather than log it.
    """
    from astral import LocationInfo
    from astral.sun import sunset, sunrise, dusk, dawn

    lat = config.get('sun_latitude')
    lon = config.get('sun_longitude')
    if lat is None or lon is None:
        raise ValueError("No coordinates configured for sun mode")

    loc = LocationInfo(latitude=float(lat), longitude=float(lon))
    tz = _local_tzinfo(tzinfo)
    sun_mode = config.get('sun_mode', 'astronomical')
    tomorrow = day + timedelta(days=1)

    if sun_mode == 'sunset_sunrise':
        start = sunset(loc.observer, date=day, tzinfo=tz)
        end = sunrise(loc.observer, date=tomorrow, tzinfo=tz)
    else:
        depression = {'civil': 6, 'nautical': 12}.get(sun_mode, 18)
        start = dusk(loc.observer, date=day, depression=depression, tzinfo=tz)
        end = dawn(loc.observer, date=tomorrow, depression=depression, tzinfo=tz)

    # In production tzinfo is None: the clamp above used the fixed offset in
    # effect now, while astimezone(None) converts with the real zone, so a
    # DST change on the night itself still lands on the right hour.
    return to_local_naive(start, tzinfo), to_local_naive(end, tzinfo)


class WindowCache:
    """Memoize window_for_day() per calendar day and window config.

    The window check runs on the frame-delivery thread for every captured
    frame, and in sun mode it evaluated astral's solar geometry twice per frame
    (today's window, then yesterday's). The result only changes when the day or
    the window settings change, so key on exactly that: a config edit or a
    coordinate change produces a different key and recomputes.

    Not internally synchronised — TimelapseWriter only ever calls it with its
    own lock held.
    """

    _MAX_ENTRIES = 8

    def __init__(self):
        self._cache = {}

    @staticmethod
    def _key(config: dict, day: date) -> tuple:
        return (
            day,
            config.get('window_mode', 'sun'),
            config.get('sun_mode', 'astronomical'),
            config.get('sun_latitude'),
            config.get('sun_longitude'),
            config.get('fixed_start', '18:00'),
            config.get('fixed_end', '06:00'),
        )

    def window_for_day(self, config: dict, day: date) -> Tuple[datetime, datetime]:
        key = self._key(config, day)
        hit = self._cache.get(key)
        if hit is not None:
            return hit
        window = window_for_day(config, day)
        # Only ever holds today's/yesterday's windows for the live config; a
        # wholesale clear beats tracking LRU order for a two-entry working set.
        if len(self._cache) >= self._MAX_ENTRIES:
            self._cache.clear()
        self._cache[key] = window
        return window
