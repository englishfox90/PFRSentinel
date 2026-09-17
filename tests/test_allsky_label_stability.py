"""
Tests for frame-to-frame label stability in the all-sky overlay.

Covers the three pieces of services/allsky/label_stability.py in isolation,
the slot memory hook in LabelGrid, and the renderer end to end on synthetic
star frames with injected single-frame noise (issues #31, #13).
"""
import numpy as np
import pytest
from PIL import Image

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from services.allsky.fisheye import FisheyeModel
from services.allsky.label_collision import LabelGrid, candidate_slots, default_gap
from services.allsky.label_stability import (
    SkyMaskHistory, StickySelection, get_label_stabilizer, reset_label_stability,
)


@pytest.fixture(autouse=True)
def _fresh_stabilizer():
    reset_label_stability()
    yield
    reset_label_stability()


def _mask(shape=(4, 4), value=255):
    return np.full(shape, value, dtype=np.uint8)


# ===================================================================
# SkyMaskHistory
# ===================================================================

class TestSkyMaskHistory:
    def test_first_frame_is_returned_as_is(self):
        h = SkyMaskHistory(depth=3)
        m = _mask()
        m[0, 0] = 0
        out = h.update(m)
        assert out.dtype == np.uint8
        assert out[0, 0] == 0 and out[1, 1] == 255

    def test_single_frame_dropout_is_ignored(self):
        """A region that is sky in two of three frames stays sky."""
        h = SkyMaskHistory(depth=3)
        h.update(_mask())
        h.update(_mask())
        glitch = _mask()
        glitch[2, 2] = 0
        out = h.update(glitch)
        assert out[2, 2] == 255

    def test_single_frame_appearance_is_ignored(self):
        h = SkyMaskHistory(depth=3)
        h.update(_mask(value=0))
        h.update(_mask(value=0))
        glitch = _mask(value=0)
        glitch[1, 1] = 255
        out = h.update(glitch)
        assert out[1, 1] == 0

    def test_persistent_change_is_adopted_on_second_frame(self):
        h = SkyMaskHistory(depth=3)
        for _ in range(3):
            h.update(_mask())
        blocked = _mask()
        blocked[3, 3] = 0
        assert h.update(blocked)[3, 3] == 255   # 1 of 3 frames: outvoted
        assert h.update(blocked)[3, 3] == 0     # 2 of 3 frames: adopted

    def test_missing_frame_holds_last_vote(self):
        h = SkyMaskHistory(depth=3, hold_frames=2)
        vote = h.update(_mask())
        assert h.update(None) is vote
        assert h.update(None) is vote

    def test_sustained_misses_release_the_hold_and_reset(self):
        h = SkyMaskHistory(depth=3, hold_frames=2)
        h.update(_mask())
        h.update(None)
        h.update(None)
        assert h.update(None) is None
        assert h.depth == 0
        # A fresh frame after the reset is not out-voted by stale history.
        fresh = _mask(value=0)
        assert h.update(fresh)[0, 0] == 0

    def test_good_frame_clears_miss_count(self):
        h = SkyMaskHistory(depth=3, hold_frames=1)
        h.update(_mask())
        assert h.update(None) is not None
        h.update(_mask())
        assert h.update(None) is not None

    def test_shape_change_resets_history(self):
        h = SkyMaskHistory(depth=3)
        h.update(_mask((4, 4)))
        h.update(_mask((4, 4)))
        out = h.update(_mask((6, 6), value=0))
        assert out.shape == (6, 6)
        assert h.depth == 1
        assert out[0, 0] == 0

    def test_depth_is_bounded(self):
        h = SkyMaskHistory(depth=2)
        for _ in range(5):
            h.update(_mask())
        assert h.depth == 2


# ===================================================================
# StickySelection
# ===================================================================

class TestStickySelection:
    def test_first_frame_takes_the_brightest(self):
        sel = StickySelection(rank_margin=3)
        assert sel.select(['a', 'b', 'c', 'd', 'e'], 3) == {'a', 'b', 'c'}

    def test_budget_is_never_exceeded(self):
        sel = StickySelection(rank_margin=3)
        sel.select(['a', 'b', 'c'], 3)
        out = sel.select(['x', 'y', 'a', 'b', 'c'], 3)
        assert len(out) == 3

    def test_incumbent_just_past_the_budget_keeps_its_slot(self):
        sel = StickySelection(rank_margin=3)
        sel.select(['a', 'b', 'c'], 3)
        # 'n' is a newcomer at rank 2; 'c' slips to rank 3 (first past the budget).
        out = sel.select(['a', 'b', 'n', 'c'], 3)
        assert out == {'a', 'b', 'c'}

    def test_incumbent_far_past_the_budget_is_dropped(self):
        sel = StickySelection(rank_margin=3)
        sel.select(['a', 'b', 'c'], 3)
        out = sel.select(['a', 'b', 'n1', 'n2', 'n3', 'n4', 'c'], 3)
        assert 'c' not in out
        assert out == {'a', 'b', 'n1'}

    def test_much_brighter_newcomer_displaces_an_incumbent(self):
        """The Moon rising must show even though every slot is held."""
        sel = StickySelection(rank_margin=3)
        sel.select(['a', 'b', 'c'], 3)
        out = sel.select(['moon', 'a', 'b', 'c'], 3)
        assert 'moon' in out
        assert out == {'moon', 'a', 'b'}

    def test_invisible_incumbent_drops_and_slot_refills(self):
        sel = StickySelection(rank_margin=3)
        sel.select(['a', 'b', 'c', 'd'], 3)
        out = sel.select(['a', 'b', 'd'], 3)
        assert out == {'a', 'b', 'd'}

    def test_boundary_object_flicker_does_not_evict_the_replacement(self):
        """Regression for #31: object at the budget edge blinking in and out
        must not swap two labels every frame."""
        sel = StickySelection(rank_margin=3)
        stable = ['a', 'b']
        sel.select(stable + ['x', 'y'], 3)          # x shown, y waits
        assert sel.select(stable + ['y'], 3) == {'a', 'b', 'y'}   # x vanishes
        shown = []
        for _ in range(4):
            shown.append(sel.select(stable + ['x', 'y'], 3))  # x back
            shown.append(sel.select(stable + ['y'], 3))       # x gone
        assert all(s == {'a', 'b', 'y'} for s in shown)

    def test_zero_budget_shows_everything(self):
        sel = StickySelection()
        assert sel.select(['a', 'b'], 0) == {'a', 'b'}

    def test_shrinking_budget_trims_from_the_faint_end(self):
        sel = StickySelection(rank_margin=3)
        sel.select(['a', 'b', 'c', 'd'], 4)
        assert sel.select(['a', 'b', 'c', 'd'], 2) == {'a', 'b'}

    def test_reset_forgets_incumbents(self):
        sel = StickySelection(rank_margin=3)
        sel.select(['a', 'b', 'c'], 3)
        sel.reset()
        assert sel.select(['a', 'b', 'n', 'c'], 3) == {'a', 'b', 'n'}


# ===================================================================
# LabelGrid slot memory
# ===================================================================

class TestSlotMemory:
    def test_remembered_slot_is_tried_first(self):
        memory = {'star:1': 2}  # "below"
        grid = LabelGrid(1920, 1080, slot_memory=memory)
        gap = default_gap(14.0)
        pos = grid.try_place(500.0, 500.0, 60.0, 14.0, key='star:1')
        assert pos == candidate_slots(500.0, 500.0, 60.0, 14.0, gap)[2]

    def test_winning_slot_is_recorded(self):
        memory = {}
        grid = LabelGrid(1920, 1080, slot_memory=memory)
        grid.try_place(500.0, 500.0, 60.0, 14.0, key='star:1')
        assert memory['star:1'] == 0
        grid2 = LabelGrid(1920, 1080, slot_memory=memory)
        gap = default_gap(14.0)
        grid2.occupy(500.0 + gap, 480.0, 60.0, 40.0)  # block the right slot
        grid2.try_place(500.0, 500.0, 60.0, 14.0, key='star:1')
        assert memory['star:1'] == 1

    def test_blocked_remembered_slot_falls_through(self):
        memory = {'star:1': 0}
        grid = LabelGrid(1920, 1080, slot_memory=memory)
        gap = default_gap(14.0)
        grid.occupy(500.0 + gap, 480.0, 60.0, 40.0)
        pos = grid.try_place(500.0, 500.0, 60.0, 14.0, key='star:1')
        assert pos is not None
        assert pos[0] + 60.0 == 500.0 - gap  # left slot

    def test_no_key_leaves_memory_untouched(self):
        memory = {}
        grid = LabelGrid(1920, 1080, slot_memory=memory)
        grid.try_place(500.0, 500.0, 60.0, 14.0)
        assert memory == {}

    def test_stale_slot_index_is_ignored(self):
        memory = {'star:1': 99}
        grid = LabelGrid(1920, 1080, slot_memory=memory)
        assert grid.try_place(500.0, 500.0, 60.0, 14.0, key='star:1') is not None
        assert memory['star:1'] == 0


# ===================================================================
# Renderer end to end
# ===================================================================

def _synthetic_sky(n_stars: int, seed: int = 0, size: int = 750) -> Image.Image:
    """Dark frame with a bright sky disc and Gaussian stars inside it."""
    rng = np.random.default_rng(seed)
    arr = np.zeros((size, size), dtype=np.float32)
    yy, xx = np.mgrid[0:size, 0:size]
    c, r = size / 2.0, size * 0.45
    arr[((xx - c) ** 2 + (yy - c) ** 2) <= r * r] = 70
    arr += rng.normal(0, 2.0, arr.shape)
    for _ in range(n_stars):
        ang = rng.uniform(0, 2 * np.pi)
        rad = rng.uniform(0, r * 0.9)
        x, y = c + rad * np.cos(ang), c + rad * np.sin(ang)
        amp = rng.uniform(80, 180)
        arr += amp * np.exp(-(((xx - x) ** 2 + (yy - y) ** 2) / (2 * 1.5 ** 2)))
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8)).convert('RGBA')


def _overlay_config(cal_path: str) -> dict:
    return {
        'enabled': True,
        'calibration_file': cal_path,
        '_lat': 51.5, '_lon': -0.1,
        '_obs_utc': '2024-06-21T22:00:00+00:00',
        'top_n': 6,
        'grid': {'enabled': False},
        'constellations': {'enabled': True, 'lines': True, 'labels': True},
        'bright_stars': {'enabled': True, 'max_magnitude': 2.5},
        'messier': {'enabled': True},
        'ngc': {'enabled': False},
        'planets': {'enabled': True, 'colors': {}},
    }


def _write_model(tmp_path) -> str:
    model = FisheyeModel(
        cx=375.0, cy=375.0, a1=215.0, a3=0.0, a5=0.0,
        roll=0.0, axis_alt=90.0, axis_az=0.0,
        rms_residual=1.0, n_matches=50,
        calibrated_at="2024-01-01T00:00:00+00:00",
        image_width=750, image_height=750,
    )
    path = str(tmp_path / "cal.json")
    model.save(path)
    return path


class TestRendererStability:
    @pytest.mark.slow
    def test_low_detection_frame_keeps_the_label_set(self, tmp_path):
        """One frame with too few stars must not change which labels show."""
        from services.allsky.overlay_renderer import render_allsky_overlay
        config = _overlay_config(_write_model(tmp_path))
        stab = get_label_stabilizer()

        good = _synthetic_sky(60)
        for _ in range(3):
            render_allsky_overlay(good, config, {})
        assert stab.masks.depth == 3
        shown_before = stab.selection.shown
        assert shown_before, "expected some labels on the synthetic sky"

        render_allsky_overlay(_synthetic_sky(3), config, {})
        assert stab.selection.shown == shown_before
        assert stab.masks.depth == 3, "held mask, history untouched"

        render_allsky_overlay(good, config, {})
        assert stab.selection.shown == shown_before

    @pytest.mark.slow
    def test_identical_frames_render_identically(self, tmp_path):
        from services.allsky.overlay_renderer import render_allsky_overlay
        config = _overlay_config(_write_model(tmp_path))
        frame = _synthetic_sky(60)
        first = np.array(render_allsky_overlay(frame, config, {}))
        second = np.array(render_allsky_overlay(frame, config, {}))
        assert np.array_equal(first, second)

    @pytest.mark.slow
    def test_reset_clears_renderer_state(self, tmp_path):
        from services.allsky.overlay_renderer import render_allsky_overlay
        config = _overlay_config(_write_model(tmp_path))
        render_allsky_overlay(_synthetic_sky(60), config, {})
        stab = get_label_stabilizer()
        assert stab.masks.depth == 1 and stab.slot_memory
        reset_label_stability()
        assert stab.masks.depth == 0 and not stab.slot_memory
        assert stab.selection.shown == set()
