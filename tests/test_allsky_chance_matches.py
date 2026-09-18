"""Chance-match expectation for the greedy star matcher (issue #33).

The reporter's Monte-Carlo of `_brightness_match` on purely random points is the
ground truth these tests hold `chance_matches` to: with 200 detections, 400
catalogue candidates and a sky radius of 1345 px, summed over a 60-frame
buffer, it produced 3685 / 1595 / 619 matches at tolerances 43 / 26 / 15.5 px
and a median residual of 11.1 px at the tightest.
"""
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from services.allsky.chance_matches import (
    CHANCE_MARGIN,
    chance_median_residual,
    chance_ratio,
    check_above_chance,
    estimate_chance,
    excess_over_chance,
    expected_chance_matches,
    expected_frame_matches,
    frame_pool,
    is_above_chance,
)

# Reporter's rig (issue #33).
N_DET, N_CAT, SKY_R = 200, 400, 1345.0
# A rig detecting under ~78 objects per frame expects fewer than 4 chance
# matches per frame — the regime the per-frame expectation filter wiped out.
LOW_N_DET = 40
N_FRAMES = 60
MONTE_CARLO = {43.0: 3685, 26.0: 1595, 15.5: 619}


class _AllInsideModel:
    """Projects every catalogue entry to one point inside the sky disc."""

    def __init__(self, x=1000.0, y=1000.0, visible=True):
        self.x, self.y, self.visible = x, y, visible

    def altaz_array_to_pixels(self, alts, azs):
        n = len(alts)
        return (np.full(n, self.x), np.full(n, self.y),
                np.full(n, self.visible, dtype=bool))


def _frame(n_det=N_DET, n_cat_entries=2000, vmag=5.0, sky_r=SKY_R):
    return {
        'detected': [(0.0, 0.0, 1.0)] * n_det,
        'above_horizon': [({'name': 'x', 'vmag': vmag}, 45.0, 10.0)] * n_cat_entries,
        'sky_cx': 1000.0, 'sky_cy': 1000.0, 'sky_r': sky_r,
        'image_width': 3552, 'image_height': 3552,
    }


class TestCalibrationAgainstMonteCarlo:
    """The published Monte-Carlo numbers are the model's calibration points."""

    @pytest.mark.parametrize('tol,observed', sorted(MONTE_CARLO.items()))
    def test_matches_monte_carlo_within_15_percent(self, tol, observed):
        predicted = expected_frame_matches(N_DET, N_CAT, SKY_R, tol) * N_FRAMES
        rel = abs(predicted - observed) / observed
        assert rel <= 0.15, (
            f"tol={tol}px: predicted {predicted:.0f} vs Monte-Carlo {observed} "
            f"({rel * 100:.1f}% off)")

    def test_buffer_size_falls_out_as_60_frames(self):
        """619 observed / 10.35 per frame at the tightest tolerance = 60 frames,
        which is also the calibration service's MAX_BUFFER."""
        per_frame = expected_frame_matches(N_DET, N_CAT, SKY_R, 15.5)
        assert round(MONTE_CARLO[15.5] / per_frame) == N_FRAMES

    def test_median_chance_residual(self):
        """tol/sqrt(2) = 10.96px vs the 10.86px the wrong-basin fits reported."""
        assert chance_median_residual(15.5) == pytest.approx(10.96, abs=0.05)


class TestFrameModel:

    def test_zero_detections_gives_zero(self):
        assert expected_frame_matches(0, N_CAT, SKY_R, 20.0) == 0.0
        assert expected_chance_matches([_frame(n_det=0)],
                                       _AllInsideModel(), 20.0) == 0.0

    def test_zero_catalogue_gives_zero(self):
        assert expected_frame_matches(N_DET, 0, SKY_R, 20.0) == 0.0

    def test_zero_tolerance_gives_zero(self):
        assert expected_frame_matches(N_DET, N_CAT, SKY_R, 0.0) == 0.0

    def test_scaling_is_quadratic_in_tolerance(self):
        """Away from saturation, doubling the tolerance quadruples chance."""
        small = expected_frame_matches(N_DET, N_CAT, SKY_R, 4.0)
        double = expected_frame_matches(N_DET, N_CAT, SKY_R, 8.0)
        assert double / small == pytest.approx(4.0, rel=0.01)

    def test_scaling_is_quadratic_in_sky_radius(self):
        half_r = expected_frame_matches(N_DET, N_CAT, SKY_R / 2, 4.0)
        full_r = expected_frame_matches(N_DET, N_CAT, SKY_R, 4.0)
        assert half_r / full_r == pytest.approx(4.0, rel=0.01)

    @pytest.mark.parametrize('n_det,n_cat', [(200, 400), (400, 200), (50, 50)])
    def test_saturation_never_exceeds_the_smaller_pool(self, n_det, n_cat):
        """Greedy matching is one-to-one, so a tolerance covering the whole sky
        still cannot produce more matches than min(N_det, N_cat)."""
        for tol in (SKY_R / 2, SKY_R, SKY_R * 10):
            e = expected_frame_matches(n_det, n_cat, SKY_R, tol)
            assert e <= min(n_det, n_cat) + 1e-9, f"tol={tol}: {e}"

    def test_saturates_toward_the_smaller_pool(self):
        assert expected_frame_matches(200, 400, SKY_R, SKY_R * 10) == 200


class TestFramePool:

    def test_mirrors_the_matchers_400_candidate_cap(self):
        n_det, n_cat, radius = frame_pool(_frame(), _AllInsideModel())
        assert (n_det, n_cat, radius) == (N_DET, 400, SKY_R)

    def test_pool_is_five_per_detection_below_the_cap(self):
        _n_det, n_cat, _r = frame_pool(_frame(n_det=30), _AllInsideModel())
        assert n_cat == 150

    def test_max_vmag_filters_the_pool(self):
        _n, n_cat, _r = frame_pool(_frame(vmag=5.0), _AllInsideModel(),
                                   max_vmag=3.5)
        assert n_cat == 0

    def test_candidates_outside_the_sky_disc_do_not_count(self):
        model = _AllInsideModel(x=1000.0, y=1000.0 + SKY_R + 50.0)
        _n, n_cat, _r = frame_pool(_frame(), model)
        assert n_cat == 0

    def test_invisible_candidates_do_not_count(self):
        _n, n_cat, _r = frame_pool(_frame(), _AllInsideModel(visible=False))
        assert n_cat == 0

    def test_no_sky_circle_falls_back_to_the_frame_area(self):
        f = _frame()
        f.pop('sky_r')
        _n, n_cat, radius = frame_pool(f, _AllInsideModel())
        # Equal-area disc for a 3552x3552 frame.
        assert radius == pytest.approx(3552 / np.sqrt(np.pi), rel=1e-6)
        assert n_cat == 400

    def test_no_geometry_at_all_reports_zero_radius(self):
        f = _frame()
        for k in ('sky_r', 'image_width', 'image_height'):
            f.pop(k)
        _n, _c, radius = frame_pool(f, _AllInsideModel())
        assert radius == 0.0


class TestEstimateOverFrames:

    def test_sums_over_frames(self):
        frames = [_frame() for _ in range(N_FRAMES)]
        est = estimate_chance(frames, _AllInsideModel(), 15.5)
        assert est.n_frames == N_FRAMES
        assert est.expected == pytest.approx(MONTE_CARLO[15.5], rel=0.15)
        assert est.median_residual_px == pytest.approx(10.96, abs=0.05)

    def test_low_expectation_frames_still_contribute(self):
        """Every frame with geometry counts. `_build_all_matches` drops frames
        by their ACTUAL match count, which is a different quantity — dropping
        them by their EXPECTATION instead zeroed the estimate on any rig whose
        per-frame chance sits under the floor."""
        frames = [_frame(n_det=LOW_N_DET) for _ in range(N_FRAMES)]
        per_frame = expected_frame_matches(
            LOW_N_DET, LOW_N_DET * 5, SKY_R, 15.5)
        assert per_frame < 4.0, "fixture must sit under a typical floor"
        est = estimate_chance(frames, _AllInsideModel(), 15.5)
        assert est.n_frames == N_FRAMES
        assert est.expected == pytest.approx(per_frame * N_FRAMES, rel=1e-6)

    def test_frames_without_geometry_are_skipped(self):
        f = _frame()
        for k in ('sky_r', 'image_width', 'image_height'):
            f.pop(k)
        est = estimate_chance([f], _AllInsideModel(), 15.5)
        assert est.n_frames == 0 and est.expected == 0.0


class TestDecision:

    def test_reporter_wrong_basin_is_rejected(self):
        """686 observed vs 619 expected — an 11% excess, inside the noise."""
        assert not is_above_chance(686, 619.0)
        assert chance_ratio(686, 619.0) == pytest.approx(1.108, abs=0.01)
        assert excess_over_chance(686, 619.0) == pytest.approx(67.0, abs=0.5)

    def test_a_real_fit_is_accepted(self):
        assert is_above_chance(300, 40.0)
        assert chance_ratio(300, 40.0) == pytest.approx(7.5)

    def test_margin_is_the_decision_boundary(self):
        assert is_above_chance(200, 100.0)
        assert not is_above_chance(199, 100.0)
        assert CHANCE_MARGIN == 2.0

    def test_fails_open_when_there_is_no_expectation(self):
        """No geometry means no estimate, which is not evidence of a bad fit."""
        assert is_above_chance(30, 0.0)
        assert chance_ratio(30, 0.0) == float('inf')

    def test_check_above_chance_reports_the_numbers(self):
        frames = [_frame() for _ in range(N_FRAMES)]
        ok, msg, est = check_above_chance(686, frames, _AllInsideModel(), 15.5)
        assert not ok
        assert '686 matches' in msg and 'chance' in msg
        assert est.expected == pytest.approx(MONTE_CARLO[15.5], rel=0.15)

    def test_check_above_chance_passes_a_real_fit(self):
        frames = [_frame() for _ in range(N_FRAMES)]
        ok, _msg, _est = check_above_chance(2000, frames, _AllInsideModel(),
                                            15.5)
        assert ok

    def test_low_detection_rig_does_not_fail_open(self):
        """The reviewer's scenario: 60 frames at ~40 detections each. Every
        frame's chance expectation is ~1, well under a 4-match floor, so the
        old expectation-based drop zeroed `expected` and the gate passed
        anything. A real fit (20 matches/frame) must be judged, not waved
        through."""
        frames = [_frame(n_det=LOW_N_DET) for _ in range(N_FRAMES)]
        real_fit = 20 * N_FRAMES
        ok, _msg, est = check_above_chance(real_fit, frames,
                                           _AllInsideModel(), 15.5)
        assert est.expected > 0
        assert est.expected < real_fit / CHANCE_MARGIN
        assert ok

    def test_low_detection_rig_still_rejects_a_chance_level_fit(self):
        """Same rig, a fit sitting at its chance expectation. With `expected`
        pinned to 0 this passed on the fail-open branch; it must now fail."""
        frames = [_frame(n_det=LOW_N_DET) for _ in range(N_FRAMES)]
        est = estimate_chance(frames, _AllInsideModel(), 15.5)
        ok, _msg, _est = check_above_chance(round(est.expected), frames,
                                            _AllInsideModel(), 15.5)
        assert not ok
