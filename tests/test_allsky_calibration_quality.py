"""
Tests for services/allsky/calibration_quality.py.

Focus: the match-count floor added for issue #33. A single-image fit of 8 lens
parameters to 5 matched stars was rated 'preliminary' (rank 1) and kept.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from services.allsky.calibration_quality import (
    MIN_AUTO_MATCHES,
    CalibrationQuality,
    model_quality,
)
from services.allsky.fisheye import FisheyeModel
from services.allsky.model_admission import PROVENANCE_GUIDED, PROVENANCE_POLE


def _model(n_matches: int, rms: float = 2.43, **over) -> FisheyeModel:
    m = FisheyeModel(cx=1843.7, cy=1670.7, a1=1269.1, a3=-10.1559,
                     a5=-56.231751, rms_residual=rms, n_matches=n_matches)
    for k, v in over.items():
        setattr(m, k, v)
    return m


class TestMatchFloor:
    def test_reporter_five_match_model_is_not_a_calibration(self):
        """The exact model the #33 rig saved: n=5, RMS 2.43px, n_images=1."""
        assert model_quality(_model(5), 1, 0.0) == CalibrationQuality.NONE

    def test_floor_matches_the_single_image_calibrator_minimum(self):
        """calibrate()'s own min_matches default — every model the automatic
        path can now return still rates 'preliminary' or better."""
        from services.allsky.calibration import calibrate
        import inspect
        default = inspect.signature(calibrate).parameters['min_matches'].default
        assert MIN_AUTO_MATCHES == default

    def test_at_the_floor_is_preliminary(self):
        q = model_quality(_model(MIN_AUTO_MATCHES), 1, 0.0)
        assert q == CalibrationQuality.PRELIMINARY

    def test_one_below_the_floor_is_none(self):
        q = model_quality(_model(MIN_AUTO_MATCHES - 1), 1, 0.0)
        assert q == CalibrationQuality.NONE

    def test_ten_match_single_image_is_preliminary(self):
        assert model_quality(_model(10), 1, 0.0) == CalibrationQuality.PRELIMINARY

    def test_none_outranks_nothing(self):
        """The demotion is what lets the escape path replace the model on
        disk — 'none' is rank 0, so any valid candidate is a rank upgrade."""
        assert CalibrationQuality.rank(CalibrationQuality.NONE) == 0
        assert (CalibrationQuality.rank(CalibrationQuality.PRELIMINARY)
                > CalibrationQuality.rank(CalibrationQuality.NONE))


class TestGuidedExemption:
    def test_guided_five_anchor_solve_is_not_demoted(self):
        """guided_calibration.MIN_ANCHORS is 5 by construction and a human
        identified every anchor — the floor must not rate it 'none'."""
        m = _model(5, rms=2.4, provenance=PROVENANCE_GUIDED)
        assert model_quality(m, 1, 0.0) == CalibrationQuality.PRELIMINARY

    def test_pole_provenance_does_not_exempt(self):
        """Only the guided rung is exempt: a 'pole' stamp says the basin was
        corroborated, not that the fit had enough stars to be one."""
        m = _model(5, provenance=PROVENANCE_POLE)
        assert model_quality(m, 1, 0.0) == CalibrationQuality.NONE


class TestExistingLevelsUnchanged:
    @pytest.mark.parametrize('n_images,n,rms,span,expected', [
        (1, 40, 6.0, 0.0, CalibrationQuality.PRELIMINARY),
        (3, 30, 14.0, 10.0, CalibrationQuality.ACCEPTABLE),
        (10, 100, 11.0, 30.0, CalibrationQuality.GOOD),
        (20, 4561, 7.8, 80.0, CalibrationQuality.EXCELLENT),
    ])
    def test_multi_image_levels(self, n_images, n, rms, span, expected):
        assert model_quality(_model(n, rms=rms), n_images, span) == expected

    def test_missing_and_invalid_models(self):
        assert model_quality(None) == CalibrationQuality.NONE
        assert model_quality(_model(0), 1, 0.0) == CalibrationQuality.NONE
        assert model_quality(_model(50, a1=0.0), 10, 60.0) == CalibrationQuality.NONE
