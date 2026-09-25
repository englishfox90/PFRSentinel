"""pole_tolerance — the calibrated pole admission distance and the joint
fit's pole sigma (issue #93, package 5d), and validate_pole using them."""
import math
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from services.allsky.calibration_validate import (
    POLE_TOL_REF_PX, REF_SKY_R_PX, tol_scale, validate_pole)
from services.allsky.fisheye import FisheyeModel
from services.allsky.pole_estimate import PoleEstimate
from services.allsky.pole_tolerance import (
    POLE_SIGMA_MULTIPLE, POLE_TOL_FLOOR_REF_PX, POLE_TOL_RADIAL_FRACTION,
    PoleConstraint, pole_sigma_px, pole_tolerance_px)

LAT = 38.9717


def _reference_model() -> FisheyeModel:
    return FisheyeModel(
        cx=1532.277480022239, cy=1748.2333782530266,
        a1=1277.1768448604173, a3=-47.565326396085396, a5=-58.919100634659344,
        roll=1.2213944731235367, axis_alt=84.49275734968984,
        axis_az=280.49549589886345, east_left=True)


def _pole(x=1718.0, y=646.0, sigma_px=0.0, source='polaris'):
    return PoleEstimate(x=x, y=y, east_left=True, sign=-1, n_frames=12,
                        span_minutes=60.0, drift_px=3.3, flux=3600.0,
                        sign_votes=(300, 1700), sigma_px=sigma_px, source=source)


# The reference pole's distance from the reference model's optical centre.
R_P = math.hypot(1718.0 - 1532.277480022239, 646.0 - 1748.2333782530266)


class TestTolerance:
    def test_from_sigma_and_radius(self):
        assert pole_tolerance_px(10.0, 1.0, 500.0) == pytest.approx(
            POLE_SIGMA_MULTIPLE * 10.0 + POLE_TOL_RADIAL_FRACTION * 500.0)

    def test_floor_binds_near_the_centre(self):
        assert pole_tolerance_px(1.0, 1.0, 100.0) == pytest.approx(POLE_TOL_FLOOR_REF_PX)
        assert POLE_SIGMA_MULTIPLE * 1.0 + POLE_TOL_RADIAL_FRACTION * 100.0 < POLE_TOL_FLOOR_REF_PX

    def test_unknown_sigma_keeps_the_flat_tolerance(self):
        assert pole_tolerance_px(0.0, 1.0, 1000.0) == POLE_TOL_REF_PX == 140.0
        assert pole_tolerance_px(None, 1.0, 1000.0) == POLE_TOL_REF_PX

    def test_flat_tolerance_and_floor_scale_with_resolution(self):
        s = tol_scale(REF_SKY_R_PX * 0.2)
        assert pole_tolerance_px(0.0, s, 200.0) == pytest.approx(POLE_TOL_REF_PX * s)
        assert pole_tolerance_px(0.5, s, 10.0) == pytest.approx(POLE_TOL_FLOOR_REF_PX * s)

    def test_reference_night_margins(self):
        """Plan §0.7: on the reference rig's 750 px night (sky_r 297, pole
        290 px from the centre, sigma 3.8) the guided model sat 11.5 px from
        the rotation pole and every anchor-passing joint fit 29–32 px. The
        gate must pass all of them."""
        tol = pole_tolerance_px(3.8, tol_scale(296.6), 290.0)
        assert tol > 32.0
        assert tol < 60.0     # and still not a blanket pass at that scale

    def test_pole_near_the_centre_is_held_tighter_than_the_flat_gate(self):
        """A high-latitude rig with the pole 400 px from the centre and a
        10 px sigma is gated at 70 px, half the flat 140."""
        assert pole_tolerance_px(10.0, 1.0, 400.0) == pytest.approx(70.0)
        assert pole_tolerance_px(10.0, 1.0, 400.0) < POLE_TOL_REF_PX


class TestSigma:
    def test_measurement_and_model_error_add_in_quadrature(self):
        assert pole_sigma_px(5.0, 1.0, 300.0) == pytest.approx(
            math.hypot(5.0, POLE_TOL_RADIAL_FRACTION * 300.0))

    def test_unknown_sigma_is_the_flat_tolerance_over_the_multiple_plus_the_radial_term(self):
        assert pole_sigma_px(0.0, 1.0, 300.0) == pytest.approx(
            math.hypot(POLE_TOL_REF_PX / POLE_SIGMA_MULTIPLE, POLE_TOL_RADIAL_FRACTION * 300.0))
        assert pole_sigma_px(None, 1.0, 0.0) == pytest.approx(POLE_TOL_REF_PX / POLE_SIGMA_MULTIPLE)

    def test_no_sigma_never_pulls_harder_than_a_measured_sigma(self):
        """PR #100 review: at the reference rig's pole radius (~1250 px full
        res) a Polaris-path or legacy estimate must not out-pull the
        validated no-harm case, and a good model's 50–70 px regional
        error must sit under 1σ."""
        r_p = 1250.0
        unknown = pole_sigma_px(0.0, 1.0, r_p)
        assert unknown >= 70.0
        for sigma in (0.5, 3.0, 5.0, 10.0, 15.0):
            assert unknown >= pole_sigma_px(sigma, 1.0, r_p)


class TestPoleConstraint:
    def test_none_without_a_pole(self):
        assert PoleConstraint.from_estimate(None, LAT, 1.0, (0.0, 0.0)) is None

    def test_hemisphere_sets_the_pole_azimuth(self):
        centre = (1532.3, 1748.2)
        north = PoleConstraint.from_estimate(_pole(sigma_px=4.0), 38.97, 1.0, centre)
        south = PoleConstraint.from_estimate(_pole(sigma_px=4.0), -38.97, 1.0, centre)
        assert (north.alt_deg, north.az_deg) == pytest.approx((38.97, 0.0))
        assert (south.alt_deg, south.az_deg) == pytest.approx((38.97, 180.0))
        assert north.sigma_px == pytest.approx(pole_sigma_px(4.0, 1.0, R_P), abs=0.1)
        assert (north.x, north.y) == (1718.0, 646.0)


class TestValidatePoleUsesTheCalibratedTolerance:
    def test_flat_tolerance_without_sigma(self):
        ok, msg = validate_pole(_reference_model(), LAT, _pole(), sky_r=REF_SKY_R_PX)
        assert ok and 'tol 140px' in msg

    def test_tolerance_is_the_calibrated_one_when_sigma_is_known(self):
        pole = _pole(sigma_px=5.0, source='rotation')
        ok, msg = validate_pole(_reference_model(), LAT, pole, sky_r=REF_SKY_R_PX)
        expected = pole_tolerance_px(5.0, 1.0, R_P)
        assert f"{expected:.0f}px" in msg
        assert ok, msg    # 15 + 0.1·1118 = 127 px; the reference model is ~70 px off

    def test_a_pole_near_the_centre_is_gated_tighter_than_the_flat_gate(self):
        """A high-latitude rig with its axis at the zenith projects the pole
        ~220 px from the centre. A measured pole 60 px off with a 5 px
        sigma fails the calibrated gate (floor, 40 px) where the flat 140 px
        gate — the only one a sigma-less estimate gets — would pass it."""
        m = FisheyeModel(cx=1776.0, cy=1776.0, a1=1277.0, axis_alt=90.0, east_left=True)
        lat = 80.0
        xy = m.altaz_to_pixel(lat, 0.0)
        assert math.hypot(xy[0] - m.cx, xy[1] - m.cy) < 300.0
        moved = _pole(x=xy[0] + 60.0, y=xy[1], sigma_px=5.0, source='rotation')
        ok, msg = validate_pole(m, lat, moved, sky_r=REF_SKY_R_PX)
        assert not ok and 'limit 40px' in msg
        ok, _ = validate_pole(m, lat, _pole(x=xy[0] + 60.0, y=xy[1]), sky_r=REF_SKY_R_PX)
        assert ok

    def test_caller_may_pass_its_own_tolerance(self):
        ok, _ = validate_pole(_reference_model(), LAT, _pole(sigma_px=5.0),
                              sky_r=REF_SKY_R_PX, tol_px=500.0)
        assert ok
        ok, _ = validate_pole(_reference_model(), LAT, _pole(sigma_px=5.0),
                              sky_r=REF_SKY_R_PX, tol_px=10.0)
        assert not ok

    def test_legacy_estimate_without_the_field_still_works(self):
        class Legacy:
            x, y, east_left = 1718.0, 646.0, True
        ok, msg = validate_pole(_reference_model(), LAT, Legacy(), sky_r=REF_SKY_R_PX)
        assert ok and 'tol 140px' in msg
