"""orientation_search — roll by vote, scale by scan, the pole as seed and
filter, the centre-offset vote (issue #93, package 5b).

Synthetic buffers from tests/allsky_synth on all three rig presets: three
frames four hours apart (the ring's first, middle and last), Polaris
hidden, a jittering LED 100 px from the pole, 10 % dropout. "True basin"
means a candidate whose orientation, taken as a rotation of the sky into
the camera frame, is within 4° of the preset's and whose plate scale is
within 15 % — the capture range the joint fit recovers from.
"""
import logging
import math
import os
import sys
import time

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from tests.allsky_synth import (
    PRESETS, REFERENCE, REPORTER, SOUTHERN, StaticLight, instants, synth_frames, true_pole)
from services.allsky import orientation_search as OS
from services.allsky.calibration import CalibrationError
from services.allsky.calibration_validate import (
    A3_SEED_DEFAULT, a1_from_sky_radius, median_sky_r)
from services.allsky.fisheye import FisheyeModel
from services.allsky.pole_estimate import PoleEstimate

pytest.importorskip('scipy')

SPAN_MIN = 240.0


def _frames(preset, n=3, span=SPAN_MIN, seed=3, **kw):
    px, py = true_pole(preset)
    return synth_frames(preset, instants(n, span), seed=seed, hide_pole_px=60,
                        static_lights=[StaticLight(px + 100, py, 1.2)],
                        with_catalog=True, dropout=0.1, **kw)


@pytest.fixture(scope='module', params=PRESETS, ids=[p.name for p in PRESETS])
def night(request):
    return request.param, _frames(request.param)


orientation_error_deg = OS.orientation_separation_deg


def rank_of_truth(cands, preset):
    m = preset.model
    for i, c in enumerate(cands):
        if (c.east_left == m.east_left and orientation_error_deg(c, m) <= 4.0
                and 0.85 <= c.a1 / m.a1 <= 1.15):
            return i
    return None


def _rotation_pole(preset, sigma=5.0, scale_error=0.97):
    px, py = true_pole(preset)
    return PoleEstimate(x=px + 4.0, y=py - 3.0, east_left=None, sign=-1, n_frames=20,
                        span_minutes=200.0, drift_px=0.0, flux=0.0, sign_votes=(0, 0),
                        sigma_px=sigma, source='rotation',
                        a1_px_per_rad=preset.model.a1 * scale_error)


def _polaris_pole(preset):
    px, py = true_pole(preset)
    return PoleEstimate(x=px, y=py, east_left=None, sign=-1, n_frames=20,
                        span_minutes=200.0, drift_px=0.0, flux=0.0, sign_votes=(0, 0))


class TestGeometry:
    def test_camera_frame_matches_the_fisheye_model_with_roll_added(self):
        """pixel_polar of the model's own projection is phi0 + roll."""
        rng = np.random.default_rng(1)
        alt = rng.uniform(5.0, 85.0, 200)
        az = rng.uniform(0.0, 360.0, 200)
        for m in (REFERENCE.model, REPORTER.model,
                  FisheyeModel(cx=500, cy=520, a1=300, roll=-2.4, axis_alt=70,
                               axis_az=130, east_left=False)):
            px, py, vis = m.altaz_array_to_pixels(alt, az)
            theta, phi0 = OS.camera_frame(alt, az, m.axis_alt, m.axis_az)
            r, psi = OS.pixel_polar(px[vis], py[vis], m.cx, m.cy, m.east_left)
            delta = (psi - (phi0[vis] + m.roll) + np.pi) % (2 * np.pi) - np.pi
            assert np.abs(delta).max() < 1e-9
            expected_r = m.a1 * theta[vis] + m.a3 * theta[vis] ** 3 + m.a5 * theta[vis] ** 5
            assert np.allclose(r, expected_r, atol=1e-6)

    def test_axis_grid_is_uniform_and_stops_at_the_fit_bound(self):
        cells = OS.axis_grid()
        alts = sorted({a for a, _ in cells})
        assert alts[0] == OS.AXIS_ALT_MIN_DEG and alts[-1] == 90.0
        assert sum(1 for a, _ in cells if a == 90.0) == 1
        n60 = sum(1 for a, _ in cells if a == 60.0)
        n84 = sum(1 for a, _ in cells if a == 84.0)
        assert n60 == round(360 * math.cos(math.radians(60)) / OS.AXIS_STEP_DEG)
        assert n84 < n60

    def test_scale_scan_ranges(self):
        measured = OS.scale_seeds(1000.0, measured=True)
        circle = OS.scale_seeds(1000.0, measured=False)
        assert measured[0] == pytest.approx(700.0) and measured[-1] <= 1500.0
        assert measured[-1] * OS.SCALE_STEP > 1500.0
        assert circle[0] == pytest.approx(700.0) and circle[-1] <= 1800.0
        assert np.allclose(np.diff(np.log(measured)), np.log(OS.SCALE_STEP))

    def test_a3_seed_scales_with_plate_scale(self):
        assert OS.a3_seed_for(OS.A3_SEED_A1_REF_PX) == pytest.approx(A3_SEED_DEFAULT)
        assert OS.a3_seed_for(OS.A3_SEED_A1_REF_PX / 4.7) == pytest.approx(A3_SEED_DEFAULT / 4.7)


class TestSearchFrames:
    def test_ring_first_middle_last_when_it_spans_enough(self):
        frames = _frames(REFERENCE, n=5, span=30.0)
        ring = _frames(REFERENCE, n=7, span=300.0, seed=4)
        chosen = OS.search_frames(frames, ring)
        assert [f['dt'] for f in chosen] == [ring[0]['dt'], ring[3]['dt'], ring[6]['dt']]

    def test_short_ring_falls_back_to_the_buffer(self):
        frames = _frames(REFERENCE, n=5, span=30.0)
        ring = _frames(REFERENCE, n=4, span=20.0, seed=4)
        chosen = OS.search_frames(frames, ring)
        assert [f['dt'] for f in chosen] == [frames[0]['dt'], frames[2]['dt'], frames[4]['dt']]

    def test_no_usable_frames_raises(self):
        with pytest.raises(CalibrationError):
            OS.search_frames([{'dt': None, 'detected': [], 'above_horizon': []}])


class TestUnseeded:
    def test_true_basin_ranks_first(self, night):
        preset, frames = night
        cands = OS.orientation_candidates(frames)
        assert len(cands) <= OS.BOOT_TOP_K
        assert rank_of_truth(cands, preset) == 0

    def test_mirror_hint_is_respected(self, night):
        preset, frames = night
        cands = OS.orientation_candidates(frames, east_left_hint=not preset.model.east_left)
        assert all(c.east_left is (not preset.model.east_left) for c in cands)
        assert rank_of_truth(cands, preset) is None

    def test_candidates_are_distinct_basins(self, night):
        _preset, frames = night
        cands = OS.orientation_candidates(frames)
        for i, a in enumerate(cands):
            for b in cands[i + 1:]:
                assert (a.east_left != b.east_left
                        or orientation_error_deg(a, b) > OS.DISTINCT_ORIENTATION_DEG
                        or abs(math.log(a.a1 / b.a1)) > math.log1p(OS.DISTINCT_SCALE_FRACTION))


class TestSeeded:
    def test_rotation_pole_seeds_scale_and_filters(self, night):
        """The plan's acceptance: the pole-seeded path finds the true roll in
        ≤ 48 fits — here in BOOT_TOP_K_SEEDED candidates, the true basin
        first, every candidate consistent with the pole."""
        preset, frames = night
        pole = _rotation_pole(preset)
        cands = OS.orientation_candidates(frames, pole=pole, lat_deg=preset.lat)
        assert len(cands) <= OS.BOOT_TOP_K_SEEDED <= 48
        assert rank_of_truth(cands, preset) == 0
        cx = float(np.median([f['sky_cx'] for f in frames]))
        cy = float(np.median([f['sky_cy'] for f in frames]))
        cells = [OS.OrientationCandidate(0.0, c.east_left, c.axis_alt, c.axis_az,
                                         math.degrees(c.roll), c.a1, 0, 0.0)
                 for c in cands]
        assert len(OS.pole_consistent(cells, (cx, cy), pole, preset.lat,
                                      median_sky_r(frames))) == len(cells)

    def test_polaris_pole_without_sigma_or_scale_still_seeds(self, night):
        preset, frames = night
        cands = OS.orientation_candidates(frames, pole=_polaris_pole(preset),
                                          lat_deg=preset.lat)
        assert rank_of_truth(cands, preset) == 0

    def test_a_pole_no_cell_can_reach_falls_back_to_the_unfiltered_ranking(self):
        """A trusted pole that contradicts every cell (a moved camera, a
        stale consensus) must not empty the search: the full ranking is
        used, at the unseeded candidate count."""
        preset = REPORTER
        frames = _frames(preset)
        wrong = _rotation_pole(preset)
        wrong.x, wrong.y = -4000.0, -4000.0        # off the sensor for every cell
        cx = float(np.median([f['sky_cx'] for f in frames]))
        cy = float(np.median([f['sky_cy'] for f in frames]))
        cands = OS.orientation_candidates(frames, pole=wrong, lat_deg=preset.lat)
        assert len(cands) == OS.BOOT_TOP_K
        assert rank_of_truth(cands, preset) is not None
        cells = [OS.OrientationCandidate(0.0, c.east_left, c.axis_alt, c.axis_az,
                                         math.degrees(c.roll), c.a1, 0, 0.0) for c in cands]
        assert OS.pole_consistent(cells, (cx, cy), wrong, preset.lat,
                                  median_sky_r(frames)) == []


class TestDistinct:
    def _cell(self, score, alt, az, roll, a1, east_left=True):
        return OS.OrientationCandidate(score, east_left, alt, az, roll, a1, 0, 0.0)

    def test_same_basin_collapses_to_the_best_score(self):
        cells = [self._cell(5.0, 80.0, 100.0, 30.0, 1000.0),
                 self._cell(9.0, 82.0, 102.0, 33.0, 1040.0),
                 self._cell(7.0, 80.0, 100.0, 30.0, 1000.0, east_left=False),
                 self._cell(6.0, 70.0, 250.0, -100.0, 1000.0)]
        chosen = OS.distinct_candidates(cells, 8)
        assert [c.score for c in chosen] == [9.0, 7.0, 6.0]

    def test_k_caps_the_list(self):
        cells = [self._cell(float(i), 60.0 + 3.0 * i, 10.0 * i, 20.0 * i, 900.0 + 50.0 * i)
                 for i in range(10)]
        assert len(OS.distinct_candidates(cells, 4)) == 4


class TestCentreVote:
    @pytest.mark.parametrize('offset', [40.0, 80.0, 120.0])
    def test_recovers_a_wrong_centre(self, offset):
        frames = _frames(REFERENCE, n=8, span=60.0, seed=5)
        seed = FisheyeModel(**{**REFERENCE.model.__dict__,
                               'cx': REFERENCE.model.cx + offset,
                               'cy': REFERENCE.model.cy - offset / 2})
        dx, dy, votes = OS.centre_offset_vote(frames[:3], seed, median_sky_r(frames))
        assert votes >= OS.CENTRE_VOTE_MIN_PEAK
        assert abs(dx + offset) < 3.0 and abs(dy - offset / 2) < 3.0

    def test_no_bright_stars_means_no_correction(self):
        frames = _frames(REFERENCE, n=2, span=10.0, seed=6)
        dim = [{**f, 'above_horizon': [(s, a, z) for s, a, z in f['above_horizon']
                                       if s['vmag'] > OS.CENTRE_VOTE_MAX_VMAG]}
               for f in frames]
        assert OS.centre_offset_vote(dim, REFERENCE.model, median_sky_r(frames)) == (0.0, 0.0, 0)


class TestEndToEnd:
    @pytest.mark.slow
    def test_cold_start_recovers_the_reporter_rig_with_a_pole(self):
        """refine_from_detections from nothing, with the rotation pole: the
        model comes back in the true basin at the true scale."""
        from services.allsky.multi_calibrate import refine_from_detections
        preset = REPORTER
        frames = _frames(preset, n=5, span=240.0, seed=7)
        model = refine_from_detections(frames, None, max_residual_px=20.0,
                                       pole=_rotation_pole(preset), lat_deg=preset.lat)
        assert model.east_left is preset.model.east_left
        assert orientation_error_deg(model, preset.model) < 1.0
        assert abs(model.a1 / preset.model.a1 - 1.0) < 0.03


class TestResourceBudget:
    """Plan §8: pole-seeded search ≤ 5 s, unseeded ≤ 30 s (cold start and
    escape only). Measured 2026-09-25 on the CI container, both mirrors,
    three frames of 200 detections: unseeded 3.5–4.0 s, seeded 1.4–3.2 s
    (one mirror known: 1.8 s / 1.4 s). The figures are logged on every
    run; the assertions are a 5× sanity ceiling, as the other packages'
    budget tests do."""

    @pytest.mark.slow
    def test_unseeded_and_seeded_within_budget(self):
        frames = _frames(REFERENCE, n=36, span=350.0, seed=10)
        assert int(np.median([len(f['detected']) for f in frames])) == 200
        OS.orientation_candidates(frames[:3])       # warm imports and caches
        t0 = time.perf_counter()
        OS.orientation_candidates(frames, ring=frames)
        unseeded = time.perf_counter() - t0
        t0 = time.perf_counter()
        OS.orientation_candidates(frames, ring=frames, pole=_rotation_pole(REFERENCE),
                                  lat_deg=REFERENCE.lat)
        seeded = time.perf_counter() - t0
        figures = f"orientation search: unseeded {unseeded:.2f} s, pole-seeded {seeded:.2f} s"
        logging.getLogger(__name__).info(figures)
        assert unseeded < 150.0, f"{figures} — over the 5x sanity ceiling of the 30 s budget"
        assert seeded < 25.0, f"{figures} — over the 5x sanity ceiling of the 5 s budget"


# The full-resolution cold start costs ~7 min, which the three CI runners
# cannot carry in the default suite; it runs with PFR_FULL_ACCEPTANCE=1, as
# test_pole_from_rotation's 20-seed coverage run does.
FULL_ACCEPTANCE = os.environ.get('PFR_FULL_ACCEPTANCE') == '1'


@pytest.mark.skipif(not FULL_ACCEPTANCE,
                    reason="~7 min at full resolution; set PFR_FULL_ACCEPTANCE=1")
class TestFullResolutionColdStart:
    """The whole cold start (search + every candidate's joint fit) on a
    synthetic 3552 px buffer of 60 frames × 200 detections, pole-seeded and
    unseeded — the cost a real rig pays on the refine worker at cold start
    and basin escape, and only then. Measured 2026-09-25 on the CI
    container's single core: pole-seeded 184 s, unseeded 226 s (figures
    logged on every run; the assertion is a 5× ceiling on a 10-minute
    budget, as the other budget tests). The default suite's cousin is
    TestEndToEnd (5 frames at full resolution, pole-seeded, ~15 s)."""

    @pytest.mark.slow
    def test_seeded_and_unseeded_cold_start_time(self):
        from services.allsky.multi_calibrate import refine_from_detections
        preset = REFERENCE
        frames = _frames(preset, n=60, span=240.0, seed=12)
        ring = frames[::2][:36]
        t0 = time.perf_counter()
        seeded = refine_from_detections(frames, None, max_residual_px=20.0,
                                        pole=_rotation_pole(preset), lat_deg=preset.lat,
                                        ring=ring)
        t_seeded = time.perf_counter() - t0
        t0 = time.perf_counter()
        unseeded = refine_from_detections(frames, None, max_residual_px=20.0, ring=ring)
        t_unseeded = time.perf_counter() - t0
        figures = (f"full-resolution cold start, 60 x 200: pole-seeded {t_seeded:.0f} s, "
                   f"unseeded {t_unseeded:.0f} s")
        logging.getLogger(__name__).info(figures)
        for model in (seeded, unseeded):
            assert orientation_error_deg(model, preset.model) < 1.0
            assert abs(model.a1 / preset.model.a1 - 1.0) < 0.03
        assert max(t_seeded, t_unseeded) < 3000.0, f"{figures} — over the 5x ceiling"


class TestSouthern:
    def test_southern_preset_is_a_mirror_of_the_reference(self):
        assert SOUTHERN.lat < 0 and SOUTHERN.model == REFERENCE.model

    def test_pole_seed_uses_the_south_celestial_pole(self):
        frames = _frames(SOUTHERN)
        cands = OS.orientation_candidates(frames, pole=_rotation_pole(SOUTHERN),
                                          lat_deg=SOUTHERN.lat)
        assert rank_of_truth(cands, SOUTHERN) == 0


class TestSeedScale:
    def test_measured_scale_narrows_the_scan(self):
        assert len(OS.scale_seeds(a1_from_sky_radius(1386.0), False)) > len(
            OS.scale_seeds(1277.0, True))
