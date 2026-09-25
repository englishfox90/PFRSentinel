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
    MASK_HOLD_FRAMES, MASK_VOTE_DEPTH, MASK_VOTE_MAX_EDGE,
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

    def test_sustained_misses_keep_the_vote_and_mark_it_stale(self):
        """The vote is never thrown away for lack of frames (issue #93 H9):
        past the hold it is stale, and the caller lets the equipment map
        take over — a cloudy hour used to wipe it and hand the labels to a
        raw-brightness test that passed lit equipment."""
        h = SkyMaskHistory(depth=3, hold_frames=2)
        vote = h.update(_mask())
        h.update(None)
        assert h.update(None) is vote and not h.is_stale
        assert h.update(None) is vote and h.is_stale
        assert h.depth == 1

    def test_forty_misses_keep_the_vote(self):
        h = SkyMaskHistory()
        vote = h.update(_mask())
        for _ in range(40):
            assert h.update(None) is vote
        assert h.is_stale and h.vote is vote

    def test_a_real_mask_after_a_stale_run_replaces_the_history(self):
        h = SkyMaskHistory(depth=3, hold_frames=2)
        for _ in range(3):
            h.update(_mask())
        for _ in range(3):
            h.update(None)
        assert h.is_stale
        fresh = _mask(value=0)
        out = h.update(fresh)
        assert out[0, 0] == 0, "not out-voted by the stale frames"
        assert h.depth == 1 and not h.is_stale

    def test_partial_frames_only_add_sky(self):
        """3–9 detections vote sky inside their discs and abstain elsewhere:
        equipment stays equipment, sky stays sky, and the discs turn sky."""
        h = SkyMaskHistory(depth=3)
        full = _mask()
        full[:, :2] = 0                      # left half is equipment
        h.update(full)
        partial = _mask(value=0)
        partial[0, 0] = 255                  # one disc, over the equipment
        out = h.add_partial(partial)
        assert out[0, 0] == 255, "a detection is sky wherever it lands"
        assert out[3, 0] == 0, "uncovered equipment is not voted on"
        assert out[3, 3] == 255, "uncovered sky is not taken away"
        assert not h.is_stale

    def test_partial_frames_never_outvote_a_full_frame_elsewhere(self):
        h = SkyMaskHistory(depth=3)
        full = _mask()
        full[2:, :] = 0
        h.update(full)
        partial = _mask(value=0)
        partial[0, 0] = 255
        h.add_partial(partial)
        out = h.add_partial(partial)
        assert out[3, 3] == 0 and out[1, 1] == 255

    def test_moon_glare_is_left_out_of_the_denominator(self):
        """Glare blanks detections around the Moon; that must not vote the
        Moon's own patch of sky as equipment."""
        h = SkyMaskHistory(depth=3)
        frame = _mask()
        frame[0:2, 0:2] = 0                  # no discs under the glare
        frame[3, 3] = 0                      # a real obstruction
        moon = np.zeros((4, 4), dtype=bool)
        moon[0:2, 0:2] = True
        out = None
        for _ in range(3):
            out = h.update(frame, exclude=moon)
        assert out[0, 0] == 255, "unjudged pixels are left to the map"
        assert out[3, 3] == 0

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

    def test_a_run_of_shrunken_masks_is_ridden_out(self):
        """An exposure change lasts many frames, not one: the default vote has
        to outlast it, which a 3-frame vote never could (discussion #76)."""
        h = SkyMaskHistory()
        for _ in range(MASK_VOTE_DEPTH):
            h.update(_mask())
        shrunk = _mask()
        shrunk[2:, :] = 0
        for _ in range(6):
            assert h.update(shrunk)[3, 3] == 255

    def test_a_lasting_change_is_still_adopted(self):
        """A telescope parked across the field must not be labelled forever."""
        h = SkyMaskHistory()
        for _ in range(MASK_VOTE_DEPTH):
            h.update(_mask())
        blocked = _mask()
        blocked[3, 3] = 0
        out = None
        for _ in range(MASK_VOTE_DEPTH):
            out = h.update(blocked)
        assert out[3, 3] == 0

    def test_default_hold_outlasts_a_run_of_failed_detections(self):
        h = SkyMaskHistory()
        vote = h.update(_mask())
        for _ in range(MASK_HOLD_FRAMES):
            assert h.update(None) is vote and not h.is_stale
        assert h.update(None) is vote and h.is_stale

    def test_large_masks_are_voted_at_reduced_resolution(self):
        """Fifteen full-resolution masks of a 3552 px frame are ~190 MB."""
        h = SkyMaskHistory()
        big = np.zeros((2000, 3000), dtype=np.uint8)
        big[:, 1500:] = 255                       # right half is open sky
        out = h.update(big)
        assert out.shape == big.shape
        assert out[1000, 2500] == 255 and out[1000, 500] == 0
        stored = h._frames[0][0]
        assert max(stored.shape) <= MASK_VOTE_MAX_EDGE
        assert stored.nbytes < big.nbytes / 25

    def test_grid_planes_fold_without_a_full_resolution_copy(self):
        """The renderer feeds the vote on the grid; the full-resolution vote
        is built only when somebody asks for it."""
        from services.allsky.label_stability import vote_grid
        full_shape = (2000, 3000)
        _, small_shape = vote_grid(full_shape)
        small = np.zeros(small_shape, dtype=np.uint8)
        small[:, small_shape[1] // 2:] = 255
        h = SkyMaskHistory()
        h.fold_grid(small, full_shape=full_shape)
        assert h._full is None
        assert h.small_vote.shape == small_shape
        assert h.vote.shape == full_shape and h.vote[1000, 2500] == 255

    def test_running_vote_matches_a_recount(self):
        """The vote is a running sum; it must not drift from the frames it holds."""
        rng = np.random.default_rng(7)
        h = SkyMaskHistory(depth=4)
        frames = [np.where(rng.random((6, 6)) > 0.5, 255, 0).astype(np.uint8)
                  for _ in range(11)]
        for i, frame in enumerate(frames):
            out = h.update(frame)
            window = frames[max(0, i - 3):i + 1]
            expected = np.where(sum((f > 0).astype(int) for f in window) * 2
                                >= len(window), 255, 0)
            assert np.array_equal(out, expected), f"drifted at frame {i}"


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
    def test_labels_are_never_placed_where_the_map_says_equipment(self, tmp_path):
        """The persisted map vetoes the fresh vote: a rig the map has learned
        as all equipment gets no labels, whatever this frame's stars say."""
        from services.allsky.obstruction_map import get_obstruction_map
        from services.allsky.overlay_renderer import render_allsky_overlay
        config = _overlay_config(_write_model(tmp_path))
        good = _synthetic_sky(60)
        obs_map = get_obstruction_map()
        obs_map.reset()
        try:
            render_allsky_overlay(good, config, {})
            assert get_label_stabilizer().selection.shown, "control: labels shown"
            reset_label_stability()
            nothing = np.zeros((750, 750), dtype=np.uint8)
            obs_map.update(nothing, n_detections=60, frame_is_observable=True,
                           sky_region=np.ones((750, 750), dtype=bool))
            render_allsky_overlay(good, config, {})
            assert get_label_stabilizer().masks.depth == 1, "the vote still advanced"
            assert get_label_stabilizer().selection.shown == set()
        finally:
            obs_map.reset()

    @pytest.mark.slow
    def test_a_reprocess_of_the_same_capture_does_not_advance_the_vote(self, tmp_path):
        from services.observing_window import SAME_CAPTURE_KEY
        from services.allsky.overlay_renderer import render_allsky_overlay
        config = _overlay_config(_write_model(tmp_path))
        good = _synthetic_sky(60)
        render_allsky_overlay(good, config, {})
        shown = get_label_stabilizer().selection.shown
        render_allsky_overlay(good, config, {SAME_CAPTURE_KEY: True})
        assert get_label_stabilizer().masks.depth == 1
        assert get_label_stabilizer().selection.shown == shown

    @pytest.mark.slow
    def test_direct_renders_never_teach_the_map(self, tmp_path):
        """Only the preview path, past the observing-window check, marks a
        frame observable; a dev-tool or test render leaves the map alone."""
        from services.allsky.obstruction_map import get_obstruction_map
        from services.allsky.overlay_renderer import render_allsky_overlay
        get_obstruction_map().reset()
        config = _overlay_config(_write_model(tmp_path))
        render_allsky_overlay(_synthetic_sky(60), config, {})
        assert get_obstruction_map().frames_seen == 0
        config['_frame_is_observable'] = True
        render_allsky_overlay(_synthetic_sky(60), config, {})
        try:
            assert get_obstruction_map().frames_seen == 1
        finally:
            get_obstruction_map().reset()

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


# ===================================================================
# Sky mask vs frame brightness
# ===================================================================

class TestSkyMaskBrightness:
    """The mask says where the sky is, which does not change when the camera's
    exposure does. Absolute grey thresholds made it shrink to a third on a
    darker frame, and the labels over the lost sky vanished (discussion #76)."""

    @staticmethod
    def _coverage(img) -> float:
        from services.allsky.overlay_renderer import _detect_sky_mask
        mask = _detect_sky_mask(img)
        assert mask is not None, "synthetic frame should yield a detection mask"
        return float((mask > 0).mean())

    def test_same_sky_at_a_third_of_the_brightness_keeps_its_mask(self):
        bright = _synthetic_sky(60, seed=3)
        arr = np.array(bright.convert('L')).astype(np.float32)
        dim = Image.fromarray((arr * 0.35).astype(np.uint8)).convert('RGBA')

        full, faded = self._coverage(bright), self._coverage(dim)
        assert faded > 0.8 * full, (
            f"mask fell from {full:.0%} to {faded:.0%} of the frame when the "
            "same sky was simply exposed less")

    def test_stars_beside_dark_equipment_still_claim_less_sky(self):
        """The reason the weighting exists: a star at an obstruction's edge
        must not mark the obstruction as open sky."""
        img = _synthetic_sky(60, seed=3)
        arr = np.array(img.convert('L')).astype(np.float32)
        blocked = arr.copy()
        blocked[:, :375] *= 0.12                 # left half in deep shadow
        shadowed = Image.fromarray(blocked.astype(np.uint8)).convert('RGBA')
        from services.allsky.overlay_renderer import _detect_sky_mask
        mask = _detect_sky_mask(shadowed)
        if mask is None:
            pytest.skip("too few detections survive the synthetic shadow")
        left = float((mask[:, :375] > 0).mean())
        right = float((mask[:, 375:] > 0).mean())
        assert left < right
