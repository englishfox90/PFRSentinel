"""
One frame's verdict: is this frame good enough to image through?

Pure: every input — the frame's signals, the sun, the learned baseline,
the recent star counts, the night's FWHM — is handed in, so the rules are
testable without a clock, a camera or a file. The streaks that turn frame
verdicts into ``imaging_ok`` live in ``verdict_hysteresis``.

Reason codes (machine-readable; the NINA plugin matches on them):

``daylight``          sun above ``max_sun_alt_deg``
``roof_not_open``     roof not read Open with ``min_roof_confidence``
``frame_static``      ML: the frame is sensor noise only
``sky_unknown``       no star count and no usable ML sky reading
``cloud_cover_high``  estimated cloud cover above ``max_cloud_cover_pct``
``stars_low``         star count under ``min_star_ratio`` x this rig's
                      learned baseline; with no count at all, when ML
                      reads no stars or the observable-sky gate withheld
                      the count because the open-roof frame shows none —
                      no absolute star count is ever compared here
``stars_dropping``    star count fell more than ``star_drop_pct`` % below
                      the median of the last ``trend_frames`` frames
``fwhm_high``         FWHM more than ``fwhm_rise_pct`` % above tonight's
                      median

Every star threshold is relative — to the local baseline or to the recent
frames — because the count depends on the lens, the camera, the horizon
and the light pollution of each installation.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from typing import List, Optional, Sequence

from .frame_signals import FrameSignals
from .settings import ConditionsSettings

DAYLIGHT = 'daylight'
ROOF_NOT_OPEN = 'roof_not_open'
FRAME_STATIC = 'frame_static'
SKY_UNKNOWN = 'sky_unknown'
CLOUD_COVER_HIGH = 'cloud_cover_high'
STARS_LOW = 'stars_low'
STARS_DROPPING = 'stars_dropping'
FWHM_HIGH = 'fwhm_high'

SOURCE_STAR_BASELINE = 'star_baseline'
SOURCE_ML_CLASS = 'ml_class'

# The sky model's three classes (ml.sky_classifier.SKY_CONDITIONS; a test
# keeps this in step) -> a coarse cover figure, used only while this rig has
# no star baseline for the Moon's state. Coarse on purpose and labelled as
# such (cloud_cover_source "ml_class"): "Partly Cloudy" means noticeable
# patches, read above the 50 % default so it pauses imaging.
ML_CLASS_CLOUD_PCT = {
    'Clear': 5.0,
    'Partly Cloudy': 60.0,
    'Overcast': 95.0,
}
CLEAR_CLASSES = ('Clear',)

# The trend needs at least this many earlier frames, and the night median
# FWHM this many readings, before either may call a frame bad.
MIN_TREND_FRAMES = 3
MIN_FWHM_SAMPLES = 10


@dataclass
class FrameEvaluation:
    reasons: List[str] = field(default_factory=list)
    ratio: Optional[float] = None
    trend: Optional[float] = None
    change_pct: Optional[float] = None
    fwhm_change_pct: Optional[float] = None
    cloud_cover_pct: Optional[float] = None
    cloud_cover_source: Optional[str] = None
    fwhm_px: Optional[float] = None

    @property
    def ok(self) -> bool:
        return not self.reasons


def roof_is_open(signals: FrameSignals, settings: ConditionsSettings) -> bool:
    if signals.roof_state != 'Open':
        return False
    conf = signals.roof_confidence
    return conf is None or conf >= settings.min_roof_confidence


def usable_sky_class(signals: FrameSignals, settings: ConditionsSettings) -> Optional[str]:
    if signals.sky_condition is None:
        return None
    conf = signals.sky_confidence
    if conf is not None and conf < settings.min_sky_confidence:
        return None
    return signals.sky_condition


def recent_median(counts: Sequence[int], settings: ConditionsSettings) -> Optional[float]:
    if len(counts) < MIN_TREND_FRAMES:
        return None
    return float(statistics.median(counts[-settings.trend_frames:]))


def night_median(fwhm_values: Sequence[float]) -> Optional[float]:
    if len(fwhm_values) < MIN_FWHM_SAMPLES:
        return None
    return float(statistics.median(fwhm_values))


def usable_fwhm(signals: FrameSignals, settings: ConditionsSettings) -> Optional[float]:
    if signals.fwhm_px is None:
        return None
    if signals.detected_stars is None or signals.detected_stars < settings.min_stars_for_fwhm:
        return None
    return signals.fwhm_px


def evaluate_frame(signals: FrameSignals, settings: ConditionsSettings, *,
                   sun_alt_deg: Optional[float], baseline: Optional[float],
                   recent_counts: Sequence[int],
                   night_fwhm_median: Optional[float]) -> FrameEvaluation:
    ev = FrameEvaluation()
    reasons = ev.reasons

    # No site configured: the sun cannot be placed, so it never blocks —
    # the same fail-open choice observing_window makes.
    if sun_alt_deg is not None and sun_alt_deg > settings.max_sun_alt_deg:
        reasons.append(DAYLIGHT)
    if settings.require_roof_open and not roof_is_open(signals, settings):
        reasons.append(ROOF_NOT_OPEN)
    if signals.frame_is_static:
        reasons.append(FRAME_STATIC)

    count = signals.star_count
    if baseline is not None and count is not None:
        ev.ratio = round(count / baseline, 3)
        ev.cloud_cover_pct = round(max(0.0, min(100.0, (1.0 - ev.ratio) * 100.0)), 1)
        ev.cloud_cover_source = SOURCE_STAR_BASELINE
    else:
        sky_class = usable_sky_class(signals, settings)
        if sky_class in ML_CLASS_CLOUD_PCT:
            ev.cloud_cover_pct = ML_CLASS_CLOUD_PCT[sky_class]
            ev.cloud_cover_source = SOURCE_ML_CLASS

    median = recent_median(recent_counts, settings) if count is not None else None
    if median is not None and median > 0:
        ev.trend = round((count - median) / median, 3)
        ev.change_pct = round(100.0 * (count - median) / median, 1)

    ev.fwhm_px = usable_fwhm(signals, settings)
    if ev.fwhm_px is not None and night_fwhm_median:
        ev.fwhm_change_pct = round(100.0 * (ev.fwhm_px - night_fwhm_median)
                                   / night_fwhm_median, 1)

    if count is None and ev.cloud_cover_pct is None and signals.stars_visible is None:
        if not signals.frame_is_static and not signals.no_star_evidence:
            reasons.append(SKY_UNKNOWN)
    if ev.cloud_cover_pct is not None and ev.cloud_cover_pct > settings.max_cloud_cover_pct:
        reasons.append(CLOUD_COVER_HIGH)
    if _stars_low(signals, settings, ev.ratio):
        reasons.append(STARS_LOW)
    if ev.change_pct is not None and ev.change_pct < -settings.star_drop_pct:
        reasons.append(STARS_DROPPING)
    if ev.fwhm_change_pct is not None and ev.fwhm_change_pct > settings.fwhm_rise_pct:
        reasons.append(FWHM_HIGH)
    return ev


def _stars_low(signals, settings, ratio) -> bool:
    if ratio is not None:
        return ratio < settings.min_star_ratio
    # Cold start: no baseline for this Moon state yet, so a count means
    # nothing on its own. With no count at all: ML's own "no stars" verdict,
    # or the observable-sky gate's (it withholds star detection on an
    # open-roof frame with no stars in it, so that frame has no STAR_COUNT).
    if signals.star_count is not None:
        return False
    return signals.stars_visible is False or signals.no_star_evidence


def is_baseline_frame(signals: FrameSignals, settings: ConditionsSettings,
                      sun_alt_deg: Optional[float]) -> bool:
    """A frame the clear-sky baseline may learn from: roof confidently Open,
    astronomical night (always -18°, whatever the imaging threshold — a
    nautical-twilight sky shows fewer stars and would pull the baseline
    down), ML Clear, not sensor noise, and a measured star count."""
    from services.time_context import ASTRONOMICAL_NIGHT_ELEVATION

    return (signals.star_count is not None
            and not signals.frame_is_static
            and signals.roof_state == 'Open'
            and (signals.roof_confidence is None
                 or signals.roof_confidence >= settings.min_roof_confidence)
            and sun_alt_deg is not None and sun_alt_deg <= ASTRONOMICAL_NIGHT_ELEVATION
            and usable_sky_class(signals, settings) in CLEAR_CLASSES)
