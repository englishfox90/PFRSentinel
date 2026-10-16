"""
Tests for services/allsky/star_sightings.py — a star detected at its own
predicted pixel keeps its label whatever the sky-mask vote says, held a few
frames past the last sighting (discussion #105).
"""
from datetime import datetime, timezone

import numpy as np
import pytest
from PIL import Image

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from services.allsky.fisheye import FisheyeModel
from services.allsky.label_stability import (
    get_label_stabilizer, reset_label_stability, vote_grid,
)
from services.allsky.render_objects import _is_sky_visible
from services.allsky.star_sightings import (
    PATCH_RADIUS_PX, SIGHTING_TOL_MAX_PX, SIGHTING_TOL_MIN_PX,
    StarSightings, apply_sightings, label_targets, paint_sightings,
    sighting_tolerance,
)

DT = datetime(2024, 6, 21, 22, 0, 0, tzinfo=timezone.utc)
LAT, LON = 51.5, -0.1
TARGETS = {'star:A': (100.0, 100.0), 'star:B': (300.0, 300.0)}
AT_A = np.array([[103.0, 98.0]])
NOWHERE = np.array([[600.0, 600.0]])


@pytest.fixture(autouse=True)
def _fresh_label_state():
    reset_label_stability()
    yield
    reset_label_stability()


def _zenith_model(size=750, a1=215.0) -> FisheyeModel:
    return FisheyeModel(
        cx=size / 2, cy=size / 2, a1=a1, a3=0.0, a5=0.0,
        roll=0.0, axis_alt=90.0, axis_az=0.0,
        rms_residual=1.0, n_matches=50,
        calibrated_at="2024-01-01T00:00:00+00:00",
        image_width=size, image_height=size,
    )


def _run(s, frames, tol=12.0):
    """Feed a list of detection arrays; return visible() after each."""
    out = []
    for det in frames:
        s.update(TARGETS, det, tol)
        out.append(s.visible())
    return out


class TestTolerance:
    def test_follows_twice_the_model_residual(self):
        assert sighting_tolerance(7.0, 1.0) == pytest.approx(14.0)

    def test_floor_for_a_tight_guided_model(self):
        assert sighting_tolerance(1.4, 1.0) == pytest.approx(SIGHTING_TOL_MIN_PX)

    def test_cap_for_a_loose_model(self):
        assert sighting_tolerance(40.0, 1.0) == pytest.approx(SIGHTING_TOL_MAX_PX)

    def test_scaled_to_the_frame_drawn_on(self):
        assert sighting_tolerance(7.0, 0.5) == pytest.approx(7.0)


class TestStarSightings:
    def test_one_sighting_is_not_enough(self):
        """A chance coincidence with one detection never shows a label."""
        assert _run(StarSightings(), [AT_A]) == [set()]

    def test_two_sightings_in_a_row_show_the_label(self):
        assert _run(StarSightings(), [AT_A, AT_A])[-1] == {'star:A'}

    def test_two_sightings_with_a_gap_do_not(self):
        out = _run(StarSightings(), [AT_A, NOWHERE, AT_A])
        assert out == [set(), set(), set()]

    def test_a_steady_star_is_held_four_frames_after_the_last_sighting(self):
        s = StarSightings()
        _run(s, [AT_A] * 10)
        out = _run(s, [NOWHERE] * 6)
        assert [('star:A' in v) for v in out] == [True, True, True, True, False, False]

    def test_a_brief_star_is_held_three_frames(self):
        s = StarSightings()
        _run(s, [AT_A, AT_A])
        out = _run(s, [NOWHERE] * 5)
        assert [('star:A' in v) for v in out] == [True, True, True, False, False]

    def test_a_star_lost_for_a_frame_now_and_then_never_blinks(self):
        s = StarSightings()
        _run(s, [AT_A] * 3)
        out = _run(s, [NOWHERE, AT_A, AT_A, NOWHERE, AT_A, NOWHERE, NOWHERE, AT_A] * 3)
        assert all('star:A' in v for v in out)

    def test_outside_the_tolerance_is_not_a_sighting(self):
        far = np.array([[100.0 + 13.0, 100.0]])
        assert _run(StarSightings(), [far, far], tol=12.0)[-1] == set()

    def test_no_detections_decays(self):
        s = StarSightings()
        _run(s, [AT_A] * 3)
        for _ in range(10):
            s.update(TARGETS, None, 12.0)
        assert s.visible() == set()

    def test_reset_forgets(self):
        s = StarSightings()
        _run(s, [AT_A] * 3)
        s.reset()
        assert s.visible() == set()
        assert _run(s, [AT_A]) == [set()]


class TestPainting:
    def test_patch_passes_the_label_test_at_the_object(self):
        plane = np.zeros((400, 400), dtype=np.uint8)
        out = paint_sightings(plane, TARGETS, {'star:A'})
        assert _is_sky_visible(out, 100, 100)
        assert not _is_sky_visible(out, 300, 300)
        assert out[100, 100 + PATCH_RADIUS_PX + 2] == 0, "only a patch, not a region"

    def test_caller_plane_is_never_written(self):
        plane = np.zeros((400, 400), dtype=np.uint8)
        plane.setflags(write=False)
        out = paint_sightings(plane, TARGETS, {'star:A'})
        assert plane.max() == 0 and out.max() == 255

    def test_nothing_sighted_returns_the_plane_itself(self):
        plane = np.zeros((400, 400), dtype=np.uint8)
        assert paint_sightings(plane, TARGETS, set()) is plane

    def test_object_off_the_frame_is_skipped(self):
        plane = np.zeros((50, 50), dtype=np.uint8)
        out = paint_sightings(plane, {'star:X': (500.0, 500.0)}, {'star:X'})
        assert out.max() == 0


class TestApply:
    def test_reprocess_reads_without_advancing(self):
        s = StarSightings()
        plane = np.zeros((400, 400), dtype=np.uint8)
        apply_sightings(plane, s, TARGETS, AT_A, 12.0)
        for _ in range(5):
            out = apply_sightings(plane, s, TARGETS, None, 12.0)
        assert s.visible() == set() and out.max() == 0, "one real sighting, still one"

    def test_the_equipment_map_has_the_last_word(self):
        s = StarSightings()
        plane = np.zeros((400, 400), dtype=np.uint8)
        _, grid = vote_grid(plane.shape)
        equipment = np.zeros(grid, dtype=bool)
        open_sky = np.ones(grid, dtype=bool)
        apply_sightings(plane, s, TARGETS, AT_A, 12.0, map_sky=equipment)
        out = apply_sightings(plane, s, TARGETS, AT_A, 12.0, map_sky=equipment)
        assert s.visible() == {'star:A'} and out.max() == 0
        out = apply_sightings(plane, s, TARGETS, None, 12.0, map_sky=open_sky)
        assert _is_sky_visible(out, 100, 100)


class TestLabelTargets:
    def _config(self, **over):
        cfg = {'bright_stars': {'enabled': True, 'max_magnitude': 2.5},
               'planets': {'enabled': True}}
        cfg.update(over)
        return cfg

    def test_named_bright_stars_above_ten_degrees(self):
        model = _zenith_model()
        targets = label_targets(model, self._config(), LAT, LON, DT)
        stars = [u for u in targets if u.startswith('star:')]
        assert stars, "a June night over London has bright stars up"
        for x, y in targets.values():
            assert 0 <= x < 750 and 0 <= y < 750

    def test_layers_switched_off_are_not_looked_for(self):
        model = _zenith_model()
        cfg = self._config(bright_stars={'enabled': False}, planets={'enabled': False})
        assert label_targets(model, cfg, LAT, LON, DT) == {}

    def test_never_the_sun_or_the_moon(self):
        model = _zenith_model()
        targets = label_targets(model, self._config(), LAT, LON, DT)
        assert 'planet:Sun' not in targets and 'planet:Moon' not in targets


def _write_model(tmp_path) -> str:
    path = str(tmp_path / "cal.json")
    _zenith_model().save(path)
    return path


def _sky_with_stars_at(points, size=750) -> Image.Image:
    """Dark frame: a uniform sky disc with a Gaussian star at each point."""
    arr = np.zeros((size, size), dtype=np.float32)
    yy, xx = np.mgrid[0:size, 0:size]
    c, r = size / 2.0, size * 0.45
    arr[((xx - c) ** 2 + (yy - c) ** 2) <= r * r] = 40
    for x, y in points:
        arr += 200 * np.exp(-(((xx - x) ** 2 + (yy - y) ** 2) / (2 * 1.5 ** 2)))
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8)).convert('RGBA')


class TestRenderer:
    @pytest.mark.slow
    def test_stars_detected_at_their_pixels_are_sighted_and_reset_clears(self, tmp_path):
        from services.allsky.overlay_renderer import render_allsky_overlay
        from services.allsky.obstruction_map import get_obstruction_map
        get_obstruction_map().reset()
        config = {
            'enabled': True, 'calibration_file': _write_model(tmp_path),
            '_lat': LAT, '_lon': LON, '_obs_utc': DT.isoformat(),
            'top_n': 0, 'grid': {'enabled': False},
            'constellations': {'enabled': False},
            'bright_stars': {'enabled': True, 'max_magnitude': 2.5},
            'messier': {'enabled': False}, 'ngc': {'enabled': False},
            'planets': {'enabled': False},
        }
        targets = label_targets(_zenith_model(), config, LAT, LON, DT)
        frame = _sky_with_stars_at(list(targets.values()))
        for _ in range(2):
            render_allsky_overlay(frame, config, {})
        sighted = get_label_stabilizer().sightings.visible()
        assert sighted and sighted <= set(targets)
        reset_label_stability()
        assert get_label_stabilizer().sightings.visible() == set()
