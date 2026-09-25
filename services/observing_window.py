"""
Shared gate for sky-observation-dependent features.

Used by star detection, the all-sky overlay and the calibration feed: all
three only make sense when the frame plausibly shows a night sky with stars
in it. The rules, in order (the first that fires wins):

1. the sun is above civil twilight;
2. ML reports the frame is sensor noise only;
3. the exposure is under ``allsky_overlay.min_exposure_s``;
4. the ML roof classifier has read Closed on consecutive frames;
5. the roof reads Open (or ML is off) but the frame carries no star
   evidence on consecutive frames — a lit roof or an overcast sky — until
   two frames in a row show plenty of stars again.

What the gate governs is unchanged by rule 5: overlay, calibration feed,
star analysis. The meteor gate, roof alerts, timelapse roof mode and the
ASCOM safety file keep following the roof verdict alone — "no stars" is
not "roof closed".
"""
import threading
from datetime import datetime, timezone

from .logger import app_logger
from .sky_evidence import EVIDENCE_KEY, ml_star_signals, parse_exposure_seconds

_CACHE_KEY = '_observing_window'

# Which rule fired for the frame: 'twilight', 'static', 'exposure', 'roof',
# 'no_stars', or '' when observable. Cached alongside the verdict.
REASON_KEY = '_observing_window_reason'

# Set on a frame's metadata by a caller that is processing a capture it has
# already processed (a reprocess after a settings change). Such a run starts
# from fresh metadata, so the per-frame cache above cannot recognise it — and
# counting it again would let ONE misread frame confirm itself as two.
SAME_CAPTURE_KEY = '_same_capture_reprocessed'

# Civil twilight. Above this the sky is too bright for any of the features.
TWILIGHT_SUN_ALT_DEG = -6.0

# Consecutive frames the roof must read Closed before sky features switch off.
# The roof classifier's known failure is the overexposed or otherwise unusual
# frame — exactly what an exposure change produces — and acting on the raw
# per-frame verdict blanked the whole all-sky overlay for that frame. The two
# other consumers of the same verdict (the ASCOM safety file and the roof
# alert) already wait for a second frame; this gate was the odd one out.
# A real closure costs one frame of delay, on a frame with no stars in it.
ROOF_CLOSED_CONFIRM_FRAMES = 2

# Consecutive frames without star evidence before the roof's Open verdict is
# overruled. One more than the roof's two: the no-stars reading is weaker
# evidence than a classifier verdict (a passing cloud bank or a dew-covered
# lens can empty one or two frames of a clear night), and every frame the
# rule waits is a frame that is drawn on a lit roof or on cloud, which is
# wrong but harmless. Three frames at the reporter's 10-30 s cadence is
# under two minutes.
NO_STARS_CONFIRM_FRAMES = 3

# Recovery hysteresis: leaving the no-stars state needs this many consecutive
# frames at RECOVERY_STAR_FACTOR times the floor. A sky hovering at the floor
# (thin cloud, the first stars at dusk) would otherwise flick the overlay on
# and off every frame; at twice the floor the frame is unambiguously a
# star field, and two frames rule out a single glint-rich frame.
RECOVERY_CONFIRM_FRAMES = 2
RECOVERY_STAR_FACTOR = 2

_TRANSITION_TEXT = {
    'twilight': "sun above civil twilight",
    'static': "frame is sensor noise only",
    'exposure': "exposure under the floor",
    'roof': "roof reads Closed",
    'no_stars': "roof reads Open but no stars are detected",
}


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


class _NoStarsStreak:
    """Consecutive frames without star evidence, with recovery hysteresis.

    Suppressed after NO_STARS_CONFIRM_FRAMES misses in a row; released after
    RECOVERY_CONFIRM_FRAMES strong frames in a row. One observation per
    frame — reprocesses and callers without evidence read the standing
    state instead.
    """

    def __init__(self):
        self._misses = 0
        self._hits = 0
        self._blocked = False
        self._lock = threading.Lock()

    def observe(self, no_stars: bool, strong: bool) -> bool:
        with self._lock:
            if self._blocked:
                self._hits = self._hits + 1 if strong else 0
                if self._hits >= RECOVERY_CONFIRM_FRAMES:
                    self._blocked = False
                    self._hits = self._misses = 0
            else:
                self._misses = self._misses + 1 if no_stars else 0
                if self._misses >= NO_STARS_CONFIRM_FRAMES:
                    self._blocked = True
                    self._hits = 0
            return self._blocked

    def blocked(self) -> bool:
        with self._lock:
            return self._blocked

    def reset(self) -> None:
        with self._lock:
            self._misses = self._hits = 0
            self._blocked = False


_roof_streak = _RoofClosedStreak()
_no_stars_streak = _NoStarsStreak()
_last_logged_reason = None
_log_lock = threading.Lock()


def reset_roof_gate() -> None:
    """Forget the Closed and no-stars streaks and the last logged state (tests)."""
    global _last_logged_reason
    _roof_streak.reset()
    _no_stars_streak.reset()
    with _log_lock:
        _last_logged_reason = None


def sun_elevation(config):
    """Sun elevation in degrees now, or None without a location / astral."""
    weather_cfg = config.get('weather', {})
    lat = weather_cfg.get('latitude', '')
    lon = weather_cfg.get('longitude', '')
    if not (lat and lon):
        return None
    try:
        from astral import LocationInfo
        from astral.sun import elevation

        loc = LocationInfo(latitude=float(lat), longitude=float(lon))
        return float(elevation(loc.observer, dateandtime=datetime.now(tz=timezone.utc)))
    except Exception as e:
        app_logger.debug(f"Sun elevation check failed, allowing sky features: {e}")
        return None


def sun_is_up(config) -> bool:
    """True when the sun is above civil twilight; False when below or unknown."""
    alt = sun_elevation(config)
    return alt is not None and alt > TWILIGHT_SUN_ALT_DEG


def is_observing_window(config, metadata, feature="feature"):
    """Return True when it is safe to run sky-dependent image analysis.

    See the module docstring for the rules. Falls through (returns True)
    when location is not configured or astral is unavailable, so features
    degrade gracefully; ML rules only apply when ML wrote to the metadata,
    and the star-evidence rule only when ``sky_evidence`` measured the frame.

    Result is cached on ``metadata`` so multiple callers in a single frame
    (e.g. star detection + all-sky overlay) share one astral computation —
    and so each streak advances once per frame, not once per caller. The
    rule that fired is left in ``metadata[REASON_KEY]``.

    Args:
        config: Application config dict.
        metadata: Frame metadata dict. May contain 'ROOF_STATUS' populated
            by the ML service ("Open (95%)" / "Closed (98%)" / "N/A"),
            '_ML_RESULTS', 'EXPOSURE' and sky_evidence's EVIDENCE_KEY.
        feature: Short label used in debug log messages.
    """
    cached = metadata.get(_CACHE_KEY)
    if cached is not None:
        return cached

    reason = _evaluate(config, metadata, feature)
    metadata[_CACHE_KEY] = result = (reason == '')
    metadata[REASON_KEY] = reason
    _log_transition(reason)
    return result


def _log_transition(reason: str) -> None:
    global _last_logged_reason
    with _log_lock:
        if reason == _last_logged_reason:
            return
        previous, _last_logged_reason = _last_logged_reason, reason
    if reason:
        app_logger.info(f"Sky features suppressed: {_TRANSITION_TEXT.get(reason, reason)}")
    elif previous is not None:
        app_logger.info("Sky features resumed: frame looks like an observable sky")


def _evaluate(config, metadata, feature) -> str:
    sun_alt = sun_elevation(config)
    if sun_alt is not None and sun_alt > TWILIGHT_SUN_ALT_DEG:
        app_logger.debug(
            f"{feature} suppressed: sun elevation {sun_alt:.1f}° "
            f"(above civil twilight {TWILIGHT_SUN_ALT_DEG:.0f}°)"
        )
        return 'twilight'

    signals = ml_star_signals(metadata)
    if signals['frame_is_static']:
        app_logger.debug(f"{feature} suppressed: frame is sensor noise only")
        return 'static'

    evidence = metadata.get(EVIDENCE_KEY) or {}
    allsky_cfg = config.get('allsky_overlay', {})
    exposure_s = (evidence['exposure_s'] if 'exposure_s' in evidence
                  else parse_exposure_seconds(metadata.get('EXPOSURE')))
    floor_s = _float(allsky_cfg.get('min_exposure_s', 0))
    if exposure_s is not None and floor_s > 0 and exposure_s < floor_s:
        app_logger.debug(
            f"{feature} suppressed: exposure {exposure_s:g}s under the {floor_s:g}s floor")
        return 'exposure'

    if _roof_closed(config, metadata, feature):
        return 'roof'

    if _no_star_evidence(metadata, signals, evidence, allsky_cfg, feature):
        return 'no_stars'

    return ''


def _roof_closed(config, metadata, feature) -> bool:
    ml_config = config.get('ml_models', {})
    if not (ml_config.get('enabled', False) and ml_config.get('roof_gates_sky_features', True)):
        return False
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
        return True
    if streak:
        app_logger.debug(
            f"{feature}: ML roof status '{roof_status}' on one frame — "
            f"waiting for a second before suppressing")
    return False


def _no_star_evidence(metadata, signals, evidence, allsky_cfg, feature) -> bool:
    """Rule 5. Model-free on purpose: the bright-anchor check would be
    sharper, but it needs a credible calibration, and on the rig that raised
    issue #93 the model was a chance fit."""
    floor = int(_float(allsky_cfg.get('min_star_detections', 0)))
    star_count = evidence.get('star_count')
    if star_count is None or metadata.get(SAME_CAPTURE_KEY):
        # Same reasoning as the roof streak: a caller that never measured
        # the frame, or a reprocess of a capture already counted, reads the
        # standing state and leaves the count alone.
        blocked = _no_stars_streak.blocked()
    else:
        no_stars = star_count < floor and signals['stars_visible'] is not True
        strong = star_count >= RECOVERY_STAR_FACTOR * floor
        blocked = _no_stars_streak.observe(no_stars, strong)
    if blocked:
        app_logger.debug(
            f"{feature} suppressed: roof reads Open but {star_count} stars detected "
            f"(floor {floor})")
    return blocked


def _float(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0
