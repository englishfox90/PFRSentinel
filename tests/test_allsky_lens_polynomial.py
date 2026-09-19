"""
Tests for services/allsky/lens_polynomial.py — the radial fold-over helper
added for issue #33.
"""
import math
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from services.allsky.fisheye import FisheyeModel
from services.allsky.lens_polynomial import (
    MONOTONIC_MAX_THETA_DEG,
    radial_monotonic,
    radial_slope,
    radial_turnover_deg,
)


def _reference_model() -> FisheyeModel:
    """The known-good multi-image model of the reference rig."""
    return FisheyeModel(
        cx=1532.277480022239, cy=1748.2333782530266,
        a1=1277.1768448604173, a3=-47.565326396085396, a5=-58.919100634659344,
        roll=1.2213944731235367, axis_alt=84.49275734968984,
        axis_az=280.49549589886345, east_left=True,
    )


# The 2026-09 single-image fit the reporter got: 8 parameters over 5 matched
# stars. Its r(θ) peaks at 82.5° and falls to the horizon.
_REPORTER_A1, _REPORTER_A3, _REPORTER_A5 = 1269.1, -10.1559, -56.231751


def _reporter_model() -> FisheyeModel:
    return FisheyeModel(cx=1843.7, cy=1670.7, a1=_REPORTER_A1,
                        a3=_REPORTER_A3, a5=_REPORTER_A5,
                        rms_residual=2.43, n_matches=5)


class TestRadialMonotonic:
    """r(θ) = a1·θ + a3·θ³ + a5·θ⁵ must keep increasing across the field the
    camera actually images, or two sky altitudes share one pixel radius."""

    def test_pure_linear_never_turns_over(self):
        ok, turnover = radial_monotonic(FisheyeModel(a1=800.0))
        assert ok and turnover is None

    def test_fold_inside_the_field_rejected(self):
        ok, turnover = radial_monotonic(
            FisheyeModel(a1=800.0, a3=-60.0, a5=-100.0))
        assert not ok
        assert turnover == pytest.approx(60.0, abs=0.1)

    def test_turnover_just_outside_the_limit_accepted(self):
        """a5=-71.0 turns over at 70.2°, a hair beyond the limit."""
        ok, turnover = radial_monotonic(FisheyeModel(a1=800.0, a5=-71.0))
        assert ok
        assert turnover == pytest.approx(70.2, abs=0.1)
        assert radial_slope(FisheyeModel(a1=800.0, a5=-71.0),
                            math.radians(MONOTONIC_MAX_THETA_DEG)) > 0

    def test_turnover_just_inside_the_limit_rejected(self):
        ok, turnover = radial_monotonic(FisheyeModel(a1=800.0, a5=-73.0))
        assert not ok
        assert turnover == pytest.approx(69.7, abs=0.1)

    def test_known_good_reference_model_accepted(self):
        """The production reference model turns over at 78.0°, past the edge
        of its own illuminated disc (~17° altitude, θ≈73°). The gate must
        accept it — model_admission asks validate_lens_polynomial whether a
        pole-corroborated incumbent may still vouch for the rig."""
        ok, turnover = radial_monotonic(_reference_model())
        assert ok
        assert turnover == pytest.approx(78.0, abs=0.2)

    def test_guided_reference_solve_accepted(self):
        """The #10 rig's guided solve (a1 1.1% lower) turns over at 77.8°."""
        m = _reference_model()
        m.a1 = m.a1 / 1.011
        ok, turnover = radial_monotonic(m)
        assert ok
        assert turnover == pytest.approx(77.8, abs=0.2)

    def test_reporter_model_folds_but_outside_the_imaged_field(self):
        """Issue #33 reads the reporter's r(θ) fold as the defect. It is real
        — the curve peaks at 82.5° and drops 33px to the horizon — but it is
        NOT separable from a healthy model: the known-good reference model
        turns over EARLIER (78.0°) and falls harder (-868 vs -518 px/rad at
        the horizon). A gate that rejected the reporter's coefficients would
        reject the reference rig and every incumbent it vouches for, so the
        monotonicity limit stops at the edge of the imaged field and this
        model is caught by the match-count floors instead (calibrate()'s
        min_matches and calibration_quality.MIN_AUTO_MATCHES)."""
        ok, turnover = radial_monotonic(_reporter_model())
        assert ok
        assert turnover == pytest.approx(82.46, abs=0.05)

        ref_turnover = radial_turnover_deg(_reference_model())
        assert ref_turnover < turnover, (
            "if the known-good model ever folds LATER than the reporter's, "
            "the turnover angle becomes a usable discriminator and this gate "
            "should be revisited")

    def test_non_positive_a1_is_not_monotonic(self):
        ok, turnover = radial_monotonic(FisheyeModel(a1=0.0))
        assert not ok and turnover == 0.0

    def test_slope_is_the_polynomial_derivative(self):
        m = FisheyeModel(a1=1000.0, a3=-40.0, a5=-50.0)
        theta = 1.0
        expected = 1000.0 + 3 * -40.0 * theta ** 2 + 5 * -50.0 * theta ** 4
        assert radial_slope(m, theta) == pytest.approx(expected)
        assert radial_slope(m, 0.0) == pytest.approx(1000.0)

    def test_turnover_limit_is_respected(self):
        """A fold past the requested limit is reported as absent."""
        m = FisheyeModel(a1=800.0, a5=-73.0)   # turns over at 69.7°
        assert radial_turnover_deg(m, 60.0) is None
        assert radial_turnover_deg(m, 90.0) == pytest.approx(69.7, abs=0.1)
