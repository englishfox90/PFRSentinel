"""
Tests for services/frame_static_score.py.

The score has to separate a frame that is only sensor noise (closed roof at
night, exposure at its ceiling) from a real but dim scene, without caring about
brightness or bit depth. Scenes here are synthetic: hard-edged shapes stand in
for the pier, telescope and horizon that a real all-sky frame always contains.
"""
import tracemalloc

import numpy as np
import pytest

from services.frame_static_score import STATIC_RATIO_THRESHOLD, measure_static

H, W = 1080, 1920


def _noise(mean, sd, shape=(H, W, 3), seed=11):
    rng = np.random.default_rng(seed)
    return np.clip(rng.normal(mean, sd, shape), 0, 255).astype(np.uint8)


def _scene(scale=1.0, noise_sd=3.0, seed=5):
    rng = np.random.default_rng(seed)
    frame = np.full((H, W), 60.0)
    frame[:, :300] = 8.0                   # dark wall down one side
    frame[500:900, 600:1300] = 150.0       # telescope-sized block
    frame[100:140, 1500:1800] = 200.0      # bright fixture
    yy, xx = np.mgrid[0:H, 0:W]
    frame[(xx - 1500) ** 2 + (yy - 700) ** 2 < 150 ** 2] = 20.0
    frame = frame * scale + rng.normal(4, noise_sd, frame.shape)
    return np.clip(frame, 0, 255).astype(np.uint8)


@pytest.mark.parametrize("mean,sd", [(0.5, 0.7), (2, 1.5), (4, 3), (8, 4), (20, 5)])
def test_pure_noise_scores_a_quarter_at_any_brightness(mean, sd):
    score = measure_static(_noise(mean, sd))
    assert score.is_static
    assert score.ratio == pytest.approx(0.25, abs=0.05)


def test_noise_is_static_regardless_of_bit_depth_and_channel_count():
    rng = np.random.default_rng(2)
    u16 = np.clip(rng.normal(900, 400, (H, W, 3)), 0, 65535).astype(np.uint16)
    unit_float = rng.normal(0.02, 0.01, (H, W)).astype(np.float32)
    assert measure_static(u16).is_static
    assert measure_static(unit_float).is_static


def test_scene_is_not_static():
    score = measure_static(_scene())
    assert not score.is_static
    assert score.ratio > 0.8


def test_dim_scene_buried_near_the_noise_floor_is_still_a_scene():
    # The case that makes gating dangerous: an underexposed open roof. At 5% of
    # the scene's brightness it must still read as structure.
    score = measure_static(_scene(scale=0.05))
    assert not score.is_static


def test_smooth_gradient_over_noise_is_still_static():
    # Amp glow / a light leak is "structure" to a plain scale ratio.
    rng = np.random.default_rng(8)
    xx = np.arange(W)[None, :] / W
    frame = np.clip(rng.normal(4, 3, (H, W)) + 3.0 * xx, 0, 255).astype(np.uint8)
    assert measure_static(frame).is_static


def test_a_few_leds_do_not_make_noise_look_like_a_scene():
    frame = _noise(4, 3, shape=(H, W)).copy()
    for cx, cy in [(300, 200), (1500, 900), (1700, 300), (60, 700)]:
        frame[cy - 6:cy + 6, cx - 6:cx + 6] = 255
    assert measure_static(frame).is_static


def test_small_frames_use_smaller_blocks():
    assert measure_static(_noise(4, 3, shape=(256, 256))).is_static


@pytest.mark.parametrize("frame", [
    np.zeros((64, 64), np.uint8),            # too few blocks to measure
    np.full((H, W, 3), 10, np.uint8),        # no noise to measure at all
    np.zeros((4, 4, 4, 4), np.uint8),        # not an image
    None,
])
def test_unmeasurable_input_returns_none(frame):
    assert measure_static(frame) is None


def test_frame_is_not_mutated():
    frame = _noise(4, 3)
    before = frame.copy()
    measure_static(frame)
    np.testing.assert_array_equal(frame, before)


def test_threshold_sits_between_noise_and_scene():
    assert 0.25 < STATIC_RATIO_THRESHOLD < 0.7


def test_full_resolution_plane_is_never_copied():
    # analyze_image runs this on the shared 12.6 MP float32 plane (50 MB). A
    # whole-frame cast or copy here would undo the per-frame memory work.
    plane = np.random.default_rng(4).normal(0.1, 0.02, (3552, 3552)).astype(np.float32)
    tracemalloc.start()
    try:
        measure_static(plane)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 10 * 1000 * 1000, f"peaked at {peak / 1e6:.1f} MB"
