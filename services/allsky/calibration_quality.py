"""
Calibration quality levels and the metric → level mapping.

Extracted from calibration_service.py (which re-exports both names for its
existing callers). Pure: no Qt, no I/O.
"""
from typing import Optional

from .calibration_fit_merit import fit_is_credible
from .fisheye import FisheyeModel
from .model_admission import is_guided


class CalibrationQuality:
    """
    Calibration quality levels with display metadata.

    Each level has a value string, numeric rank (for comparison), a
    user-facing description, and background/text colour pair for the UI
    badge (dark-theme palette).
    """

    # (value, rank, description, badge_bg, badge_text)
    _LEVELS = {
        'none':        (0, 'Not calibrated',
                        '#1E1E1E', '#706F6A'),
        'preliminary': (1, 'Single image — rough overlay',
                        '#2D2305', '#FFD166'),
        'acceptable':  (2, 'Multi-image — improving',
                        '#2D1A05', '#FF9F43'),
        'good':        (3, 'Multi-image — accurate',
                        '#132D21', '#3DD68C'),
        'excellent':   (4, 'Long baseline — best accuracy',
                        '#0D2D1A', '#4ADE80'),
    }

    NONE        = 'none'
    PRELIMINARY = 'preliminary'
    ACCEPTABLE  = 'acceptable'
    GOOD        = 'good'
    EXCELLENT   = 'excellent'

    ALL = (NONE, PRELIMINARY, ACCEPTABLE, GOOD, EXCELLENT)

    @classmethod
    def rank(cls, value: str) -> int:
        return cls._LEVELS.get(value, cls._LEVELS['none'])[0]

    @classmethod
    def description(cls, value: str) -> str:
        return cls._LEVELS.get(value, cls._LEVELS['none'])[1]

    @classmethod
    def badge_colors(cls, value: str) -> tuple:
        """Return (background_hex, text_hex) for the UI badge."""
        entry = cls._LEVELS.get(value, cls._LEVELS['none'])
        return entry[2], entry[3]


# An automatic fit below this many matches is not a calibration, it is an
# underdetermined solve: the model has 8 free parameters, and issue #33 rated a
# five-star fit 'preliminary' — rank 1, enough to be kept and rendered. Equal to
# calibrate()'s own min_matches, so every model the single-image path can now
# return still rates 'preliminary' or better.
#
# Guided solves are exempt: guided_calibration.MIN_ANCHORS is 5 by construction
# (cx/cy come from the sky circle, and a human identified every anchor), so the
# floor would permanently rate a legitimate guided model 'none'.
MIN_AUTO_MATCHES = 8


def model_quality(
    model: Optional[FisheyeModel],
    n_images: int = 1,
    span_minutes: float = 0.0,
) -> str:
    """
    Assess calibration quality from model metrics.

    Returns one of the CalibrationQuality level strings:
    'none', 'preliminary', 'acceptable', 'good', 'excellent'.

    A fit whose own statistics are no better than chance (calibration_fit_merit)
    never rates above PRELIMINARY, whatever its frame count: issue #93's
    606-match, 60-frame chance fit rated Good for a week on these tiers.
    """
    if model is None or not model.is_valid():
        return CalibrationQuality.NONE
    n = model.n_matches
    if n < MIN_AUTO_MATCHES and not is_guided(model):
        return CalibrationQuality.NONE
    tier = _tier(n, model.rms_residual, n_images, span_minutes)
    if tier != CalibrationQuality.PRELIMINARY and not fit_is_credible(model)[0]:
        return CalibrationQuality.PRELIMINARY
    return tier


def _tier(n: int, rms: float, n_images: int, span_minutes: float) -> str:
    if n_images >= 20 and span_minutes >= 60 and rms <= 8.0:
        return CalibrationQuality.EXCELLENT
    if n_images >= 10 and n >= 100 and rms <= 12.0:
        return CalibrationQuality.GOOD
    if n_images >= 3 and n >= 30 and rms <= 15.0:
        return CalibrationQuality.ACCEPTABLE
    return CalibrationQuality.PRELIMINARY
