"""
Capture-schedule window maths — lets Scheduled Capture follow the timelapse
recording window instead of a second, hand-maintained pair of HH:MM times.

With ``scheduled_window_source == "timelapse"`` the camera is gated on the
timelapse window (``services.timelapse_window``) widened by
``scheduled_window_margin_min`` at each end, so the camera has time to
reconnect and settle its auto-exposure before the first frame is recorded.

Pure schedule maths — no Qt, no camera, no I/O beyond logging.
"""
from datetime import date, datetime, timedelta
from threading import Lock
from typing import Optional, Tuple

from . import timelapse_window
from .logger import app_logger

SOURCE_FIXED = "fixed"
SOURCE_TIMELAPSE = "timelapse"
DEFAULT_MARGIN_MINUTES = 15

_MAX_MARGIN_MINUTES = 180
_MAX_CACHE_ENTRIES = 8

_DEFAULT_START = "16:00"
_DEFAULT_END = "09:00"

# The camera loop asks whether it may capture on every tick; the roof mode
# warning would otherwise be emitted several times a minute forever.
_roof_warning_logged = False

Window = Optional[Tuple[datetime, datetime]]


def margin_minutes(config) -> int:
    """Configured margin, clamped to 0..180; non-numeric falls back to default."""
    raw = config.get('scheduled_window_margin_min', DEFAULT_MARGIN_MINUTES)
    try:
        minutes = int(float(raw))
    except (TypeError, ValueError):
        minutes = DEFAULT_MARGIN_MINUTES
    return max(0, min(_MAX_MARGIN_MINUTES, minutes))


def timelapse_window_config(config) -> dict:
    """The timelapse section with the sun-window coordinates injected.

    Mirrors TimelapseController._get_timelapse_config: there is no separate
    timelapse coordinate UI, the sun window always uses the global weather
    location, and a falsy value must become None (sun_window tests `is None`).
    """
    cfg = dict(config.get('timelapse', {}) or {})
    weather = config.get('weather', {}) or {}
    cfg['sun_latitude'] = weather.get('latitude') or None
    cfg['sun_longitude'] = weather.get('longitude') or None
    return cfg


def _parse_hhmm(value, fallback: str) -> Tuple[int, int]:
    try:
        hour, minute = (int(part) for part in str(value).split(':'))
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError(value)
    except (TypeError, ValueError):
        hour, minute = (int(part) for part in fallback.split(':'))
    return hour, minute


def fixed_scheduled_window(config, day: date) -> Tuple[datetime, datetime]:
    """scheduled_start_time → scheduled_end_time anchored on `day`, no margin."""
    start_h, start_m = _parse_hhmm(config.get('scheduled_start_time', _DEFAULT_START), _DEFAULT_START)
    end_h, end_m = _parse_hhmm(config.get('scheduled_end_time', _DEFAULT_END), _DEFAULT_END)

    midnight = datetime.combine(day, datetime.min.time())
    start = midnight + timedelta(hours=start_h, minutes=start_m)
    end = midnight + timedelta(hours=end_h, minutes=end_m)
    if end <= start:
        end += timedelta(days=1)
    return start, end


def _warn_roof_once() -> None:
    global _roof_warning_logged
    if _roof_warning_logged:
        return
    _roof_warning_logged = True
    app_logger.warning(
        "Scheduled capture: timelapse is in roof mode, which depends on live ML "
        "roof state and cannot be scheduled ahead — using the fixed capture times instead."
    )


def capture_window_for_day(config, day: date, *, tzinfo=None) -> Window:
    """The capture window anchored on `day`, as naive local datetimes.

    Returns None when nothing should be gated (timelapse window_mode 'always').
    """
    if config.get('scheduled_window_source', SOURCE_FIXED) != SOURCE_TIMELAPSE:
        return fixed_scheduled_window(config, day)

    tl_cfg = timelapse_window_config(config)
    mode = tl_cfg.get('window_mode', 'sun')

    if mode == 'always':
        return None
    if mode == 'roof':
        _warn_roof_once()
        return fixed_scheduled_window(config, day)

    start, end = timelapse_window.window_for_day(tl_cfg, day, tzinfo=tzinfo)
    margin = timedelta(minutes=margin_minutes(config))
    return start - margin, end + margin


class CaptureWindowGate:
    """'May the camera capture right now?' for the capture loop.

    Holds the live config object and re-reads it on every call, so edits made
    in the Timelapse panel take effect without re-wiring the capture thread.
    Called from the capture worker and from the GUI thread (for captions), so
    the per-day cache is lock-guarded.
    """

    def __init__(self, config, *, tzinfo=None):
        self._config = config
        self._tzinfo = tzinfo
        self._cache = {}
        self._lock = Lock()

    def _cache_key(self, day: date) -> tuple:
        tl_cfg = self._config.get('timelapse', {}) or {}
        weather = self._config.get('weather', {}) or {}
        return (
            day,
            self._config.get('scheduled_window_source', SOURCE_FIXED),
            self._config.get('scheduled_start_time', _DEFAULT_START),
            self._config.get('scheduled_end_time', _DEFAULT_END),
            margin_minutes(self._config),
            tl_cfg.get('window_mode', 'sun'),
            tl_cfg.get('sun_mode', 'astronomical'),
            tl_cfg.get('fixed_start', '18:00'),
            tl_cfg.get('fixed_end', '06:00'),
            weather.get('latitude') or None,
            weather.get('longitude') or None,
        )

    def window_for_day(self, day: date) -> Window:
        key = self._cache_key(day)
        with self._lock:
            if key in self._cache:
                return self._cache[key]
        window = capture_window_for_day(self._config, day, tzinfo=self._tzinfo)
        with self._lock:
            # Working set is today's and yesterday's window for the live
            # config; a wholesale clear beats tracking LRU order.
            if len(self._cache) >= _MAX_CACHE_ENTRIES:
                self._cache.clear()
            self._cache[key] = window
        return window

    def _containing_window(self, now: datetime) -> Tuple[bool, Window]:
        """(found, window): the window containing `now`, else today's.

        Overnight windows cross midnight, so yesterday's anchor may still
        apply; and the margin can pull a window that starts just after
        midnight back into the previous evening, so tomorrow's anchor is
        probed too — otherwise the camera would wake at 00:00 with no
        settle time and the API would publish the previous night's span.
        """
        today = self.window_for_day(now.date())
        if today is None:
            return True, None
        for offset in (0, -1, 1):
            window = today if offset == 0 else self.window_for_day(now.date() + timedelta(days=offset))
            if window and window[0] <= now <= window[1]:
                return True, window
        return False, today

    def __call__(self, now: Optional[datetime] = None) -> bool:
        now = now or datetime.now()
        try:
            return self._containing_window(now)[0]
        except Exception as e:
            app_logger.debug(f"Capture schedule: window check error ({e}), allowing capture")
            return True

    def active_window(self, now: Optional[datetime] = None) -> Window:
        """The window in force at `now`, as naive local datetimes (None = always on)."""
        return self._active_window(now or datetime.now())

    def _active_window(self, now: datetime) -> Window:
        return self._containing_window(now)[1]

    def describe(self, now: Optional[datetime] = None) -> str:
        """Human-readable label of the window in force at `now`, for logs/UI."""
        now = now or datetime.now()
        try:
            window = self._active_window(now)
        except Exception as e:
            app_logger.debug(f"Capture schedule: window describe error ({e})")
            return "capture window unavailable"

        if window is None:
            return "timelapse window (always on)"

        span = f"{window[0].strftime('%H:%M')} - {window[1].strftime('%H:%M')}"
        if self._config.get('scheduled_window_source', SOURCE_FIXED) != SOURCE_TIMELAPSE:
            return span

        tl_mode = (self._config.get('timelapse', {}) or {}).get('window_mode', 'sun')
        if tl_mode == 'roof':
            return f"fixed {span} (timelapse roof mode can't be scheduled)"

        margin = margin_minutes(self._config)
        if margin:
            return f"timelapse window ±{margin} min ({span})"
        return f"timelapse window ({span})"


def gate_for_config(config) -> Optional[CaptureWindowGate]:
    """A gate for the camera, or None when the fixed HH:MM schedule applies.

    None is not "no schedule": the fixed source deliberately keeps the legacy
    ``camera_utils`` check, which is battle-tested and end-exclusive.
    """
    if config.get('scheduled_window_source', SOURCE_FIXED) != SOURCE_TIMELAPSE:
        return None
    return CaptureWindowGate(config)


def describe_capture_window(config, now: Optional[datetime] = None) -> str:
    """One-shot version of CaptureWindowGate.describe() for UI captions."""
    return CaptureWindowGate(config).describe(now)
