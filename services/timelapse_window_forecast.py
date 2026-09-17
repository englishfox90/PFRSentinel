"""
Projected timelapse recording window, for showing the user ahead of time.

Answers "when will the timelapse record?" from the ``timelapse`` config
section alone (issue #14 asked for estimated start/end times when picking a
twilight depth). It mirrors the writer's window test — anchors on yesterday
and today, inclusive at both ends — so the forecast agrees with what
``TimelapseWriter._is_in_window`` will actually do.

Unlike ``timelapse_window.sun_window`` it never logs: it is polled every few
seconds for the Status card, and it reports a fixed-time fallback as a note
for the user instead of a warning in the log.

Pure maths — no Qt, no I/O.
"""
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Optional, Tuple

from . import timelapse_window

ACTIVE = "active"
UPCOMING = "upcoming"
ALWAYS = "always"
ROOF = "roof"
UNAVAILABLE = "unavailable"

_SUN_EVENT_NAMES = {
    'astronomical': "Astronomical twilight",
    'nautical': "Nautical twilight",
    'civil': "Civil twilight",
    'sunset_sunrise': "Sunset",
}

_NO_LOCATION_NOTE = (
    "No location in Weather settings, so the fixed times are used instead."
)

_BAD_LOCATION_NOTE = (
    "The Weather settings location isn't a valid latitude/longitude, so the fixed times are used instead."
)
_SUN_ERROR_NOTE = (
    "Sun times couldn't be worked out for this location, so the fixed times are used instead."
)


def _valid_location(lat, lon) -> bool:
    try:
        return -90 <= float(lat) <= 90 and -180 <= float(lon) <= 180
    except (TypeError, ValueError):
        return False


@dataclass(frozen=True)
class WindowForecast:
    """The recording window in force now, or the next one to open.

    ``start``/``end`` are naive local wall-clock and are None for the states
    that have no schedulable window (always, roof, unavailable).
    """
    state: str
    start: Optional[datetime] = None
    end: Optional[datetime] = None
    note: str = ""


def _window_for_day(tl_cfg: dict, day: date, tzinfo) -> Tuple[Tuple[datetime, datetime], str]:
    """(window, fallback note) for ``day``; the note is empty for a real sun window."""
    if tl_cfg.get('window_mode', 'sun') == 'fixed':
        return timelapse_window.fixed_window(tl_cfg, day), ""

    if tl_cfg.get('sun_latitude') is None or tl_cfg.get('sun_longitude') is None:
        return timelapse_window.fixed_window(tl_cfg, day), _NO_LOCATION_NOTE
    # Checked first because astral reports a polar night and an impossible
    # latitude with the same bare ValueError (a maths domain error).
    if not _valid_location(tl_cfg.get('sun_latitude'), tl_cfg.get('sun_longitude')):
        return timelapse_window.fixed_window(tl_cfg, day), _BAD_LOCATION_NOTE
    try:
        return timelapse_window.strict_sun_window(tl_cfg, day, tzinfo), ""
    except ImportError:
        return timelapse_window.fixed_window(tl_cfg, day), (
            "Sun times are unavailable (astral not installed), so the fixed times are used instead."
        )
    except ValueError:
        event = _SUN_EVENT_NAMES.get(tl_cfg.get('sun_mode', 'astronomical'), "Twilight")
        return timelapse_window.fixed_window(tl_cfg, day), (
            f"{event} doesn't happen that night at this latitude, so the fixed times are used instead."
        )
    except Exception:
        return timelapse_window.fixed_window(tl_cfg, day), _SUN_ERROR_NOTE


def forecast_window(tl_cfg: dict, now: Optional[datetime] = None, *, tzinfo=None) -> WindowForecast:
    """Project the recording window for the timelapse config at ``now``.

    ``tl_cfg`` is the timelapse section with ``sun_latitude``/``sun_longitude``
    injected, as the controller hands it to the writer.
    """
    now = now or datetime.now()
    mode = tl_cfg.get('window_mode', 'sun')
    if mode == 'always':
        return WindowForecast(ALWAYS)
    if mode == 'roof':
        return WindowForecast(ROOF)

    try:
        # Same order as the writer, so a 24 h fixed window picks the same anchor.
        for offset in (0, -1):
            (start, end), note = _window_for_day(tl_cfg, now.date() + timedelta(days=offset), tzinfo)
            if start <= now <= end:
                return WindowForecast(ACTIVE, start, end, note)

        # A window anchored today can already be over (a fixed 01:00 → 05:00
        # seen at 07:00), so the next one may be tomorrow's.
        for offset in (0, 1, 2):
            (start, end), note = _window_for_day(tl_cfg, now.date() + timedelta(days=offset), tzinfo)
            if start > now:
                return WindowForecast(UPCOMING, start, end, note)
    except Exception:
        pass
    return WindowForecast(UNAVAILABLE)


def _elapsed(earlier: datetime, later: datetime, tzinfo=None) -> timedelta:
    """Real time between two naive local wall-clock datetimes.

    Plain subtraction is an hour out on a clock-change night; going through
    UTC counts the hour the clocks skip or repeat.
    """
    def to_utc(dt: datetime) -> datetime:
        aware = dt.replace(tzinfo=tzinfo) if tzinfo is not None else dt.astimezone()
        return aware.astimezone(timezone.utc)
    return to_utc(later) - to_utc(earlier)


def _format_duration(delta: timedelta) -> str:
    minutes = max(0, int(delta.total_seconds() // 60))
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h {minutes:02d}m"
    return f"{minutes}m"


def _day_word(when: datetime, now: datetime) -> str:
    days = (when.date() - now.date()).days
    if days == 0:
        return "today"
    if days == 1:
        return "tomorrow"
    return when.strftime("%a")


def describe_forecast(
    forecast: WindowForecast,
    now: Optional[datetime] = None,
    *,
    enabled: bool = True,
    recording: bool = False,
    tzinfo=None,
) -> str:
    """One to three lines for the Timelapse Status card.

    An open window with nothing recording gets a hint, because the timelapse
    only records frames that capture delivers — the confusion behind #14.
    """
    now = now or datetime.now()

    if forecast.state == ALWAYS:
        return "Window: always on, so every captured frame is recorded"
    if forecast.state == ROOF:
        return "Window: follows the roof state, so it can't be projected ahead"
    if forecast.state == UNAVAILABLE:
        return "Window: couldn't be worked out from the current settings"

    span = (
        f"{forecast.start:%H:%M} → {forecast.end:%H:%M} "
        f"({_format_duration(_elapsed(forecast.start, forecast.end, tzinfo))})"
    )
    if forecast.state == ACTIVE:
        text = f"Window open: {span}  ·  closes in {_format_duration(_elapsed(now, forecast.end, tzinfo))}"
        if enabled and not recording:
            text += "\nRecording starts with the next captured frame, so capture must be running."
    else:
        text = (
            f"Next window: {_day_word(forecast.start, now)} {span}  ·  "
            f"opens in {_format_duration(_elapsed(now, forecast.start, tzinfo))}"
        )
    if forecast.note:
        text += f"\n{forecast.note}"
    return text
