"""
Shared gate for sky-observation-dependent features.

Used by star detection and the all-sky overlay: both only make sense when
the sun is below civil twilight and (if ML roof detection is running) the
roof is actually open.
"""
import threading
from datetime import datetime, timezone

from .logger import app_logger

_CACHE_KEY = '_observing_window'

# Set on a frame's metadata by a caller that is processing a capture it has
# already processed (a reprocess after a settings change). Such a run starts
# from fresh metadata, so the per-frame cache above cannot recognise it — and
# counting it again would let ONE misread frame confirm itself as two.
SAME_CAPTURE_KEY = '_same_capture_reprocessed'

# Consecutive frames the roof must read Closed before sky features switch off.
# The roof classifier's known failure is the overexposed or otherwise unusual
# frame — exactly what an exposure change produces — and acting on the raw
# per-frame verdict blanked the whole all-sky overlay for that frame. The two
# other consumers of the same verdict (the ASCOM safety file and the roof
# alert) already wait for a second frame; this gate was the odd one out.
# A real closure costs one frame of delay, on a frame with no stars in it.
ROOF_CLOSED_CONFIRM_FRAMES = 2


class _RoofClosedStreak:
    """Counts consecutive Closed verdicts, one per frame."""

    def __init__(self):
        self._count = 0
        self._lock = threading.Lock()

    def observe(self, closed: bool) -> int:
        with self._lock:
            self._count = self._count + 1 if closed else 0
            return self._count

    def current(self) -> int:
        with self._lock:
            return self._count

    def reset(self) -> None:
        with self._lock:
            self._count = 0


_roof_streak = _RoofClosedStreak()


def reset_roof_gate() -> None:
    """Forget the Closed streak (tests)."""
    _roof_streak.reset()


def is_observing_window(config, metadata, feature="feature"):
    """Return True when it is safe to run sky-dependent image analysis.

    Primary gate: sun must be below civil twilight (-6°). Requires
    weather.latitude and weather.longitude to be configured.

    Secondary gate: if ML is enabled, the roof has been predicted Closed on
    ROOF_CLOSED_CONFIRM_FRAMES consecutive frames, and
    ml_models.roof_gates_sky_features is True (default), skip. Rigs with no
    roof at all (e.g. open-air all-sky cameras) can set that flag False to
    keep sky-condition ML while dropping the roof-based suppression.

    Falls through (returns True) if location is not configured or astral
    is unavailable, so features degrade gracefully.

    Result is cached on ``metadata`` so multiple callers in a single frame
    (e.g. star detection + all-sky overlay) share one astral computation —
    and so the Closed streak advances once per frame, not once per caller.

    Args:
        config: Application config dict.
        metadata: Frame metadata dict. May contain 'ROOF_STATUS' populated
            by the ML service ("Open (95%)" / "Closed (98%)" / "N/A").
        feature: Short label used in debug log messages.
    """
    cached = metadata.get(_CACHE_KEY)
    if cached is not None:
        return cached

    result = _evaluate(config, metadata, feature)
    metadata[_CACHE_KEY] = result
    return result


def _evaluate(config, metadata, feature):
    weather_cfg = config.get('weather', {})
    lat = weather_cfg.get('latitude', '')
    lon = weather_cfg.get('longitude', '')

    if lat and lon:
        try:
            from astral import LocationInfo
            from astral.sun import elevation

            loc = LocationInfo(latitude=float(lat), longitude=float(lon))
            sun_alt = elevation(loc.observer, dateandtime=datetime.now(tz=timezone.utc))
            if sun_alt > -6.0:
                app_logger.debug(
                    f"{feature} suppressed: sun elevation {sun_alt:.1f}° "
                    f"(above civil twilight -6°)"
                )
                return False
        except Exception as e:
            app_logger.debug(f"Sun elevation check failed, allowing {feature}: {e}")

    ml_config = config.get('ml_models', {})
    if ml_config.get('enabled', False) and ml_config.get('roof_gates_sky_features', True):
        roof_status = metadata.get('ROOF_STATUS')
        if roof_status is None or metadata.get(SAME_CAPTURE_KEY):
            # No new evidence, so the standing verdict applies and the count
            # is left alone. SAME_CAPTURE_KEY: the streak already includes
            # this capture. No ROOF_STATUS at all: this caller never ran ML on
            # its metadata (ML always writes the key, 'N/A' included) - Watch
            # mode renders the overlay from a second, roof-blind dict for the
            # frame the processor has just judged. Reading that as "not
            # Closed" reset the count on every frame, so nothing in Watch mode
            # could ever be suppressed.
            roof_status = roof_status or 'no verdict on this call'
            streak = _roof_streak.current()
        else:
            streak = _roof_streak.observe(roof_status.startswith('Closed'))
        if streak >= ROOF_CLOSED_CONFIRM_FRAMES:
            app_logger.debug(f"{feature} suppressed: ML roof status '{roof_status}'")
            return False
        if streak:
            app_logger.debug(
                f"{feature}: ML roof status '{roof_status}' on one frame — "
                f"waiting for a second before suppressing")

    return True
