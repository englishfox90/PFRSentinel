"""
Tests for services/allsky/display_stretch.py — the guided-calibration
dialog's stretch slider (discussion #105: one fixed detector stretch read as
a washed-out frame on a moonlit rig, with the suggestion labels lost on it).
"""
import numpy as np
import pytest
from PIL import Image

from services.allsky.display_stretch import (
    DEFAULT_STRENGTH, DisplayStretch, clip_fraction, white_percentile)
from services.allsky.star_centroid import percentile_stretch

FRAME = 600


def _night_frame(seed=0) -> Image.Image:
    """A dark noisy sky with a few hundred stars of assorted brightness."""
    rng = np.random.default_rng(seed)
    arr = rng.normal(6.0, 1.5, (FRAME, FRAME)).clip(0, 255)
    ys = rng.integers(2, FRAME - 2, 400)
    xs = rng.integers(2, FRAME - 2, 400)
    peaks = rng.uniform(20, 255, 400)
    for y, x, peak in zip(ys, xs, peaks):
        arr[y - 1:y + 2, x - 1:x + 2] = np.maximum(
            arr[y - 1:y + 2, x - 1:x + 2], peak * 0.5)
        arr[y, x] = peak
    return Image.fromarray(np.repeat(arr.astype(np.uint8)[..., None], 3, 2))


def test_clip_fraction_is_the_detectors_one_percent_in_the_middle():
    assert clip_fraction(DEFAULT_STRENGTH) == pytest.approx(1e-2)
    assert clip_fraction(0.0) == pytest.approx(1e-4)
    assert clip_fraction(1.0) == pytest.approx(1e-1)
    assert white_percentile(DEFAULT_STRENGTH) == pytest.approx(99.0)


def test_clip_fraction_is_monotonic_and_clamped():
    steps = [clip_fraction(s) for s in np.linspace(0.0, 1.0, 41)]
    assert all(b > a for a, b in zip(steps, steps[1:]))
    assert clip_fraction(-3.0) == clip_fraction(0.0)
    assert clip_fraction(7.0) == clip_fraction(1.0)


def test_middle_of_the_slider_matches_the_detector_stretch():
    frame = _night_frame()
    arr = np.asarray(frame)
    want = percentile_stretch(arr)
    got = np.asarray(DisplayStretch(frame).render(DEFAULT_STRENGTH))

    lo, hi = DisplayStretch(frame).levels(DEFAULT_STRENGTH)
    assert lo == pytest.approx(np.percentile(arr, 1), abs=1.0)
    assert hi == pytest.approx(np.percentile(arr, 99), abs=1.0)
    # The histogram percentile lands on a whole level where numpy
    # interpolates, so allow that one level of slope on the ramp.
    assert np.mean(np.abs(got.astype(int) - want.astype(int))) < 4.0


def test_harder_stretch_is_brighter_and_saturates_more():
    stretch = DisplayStretch(_night_frame())
    means, whites = [], []
    for s in (0.0, 0.25, 0.5, 0.75, 1.0):
        out = np.asarray(stretch.render(s))
        means.append(float(out.mean()))
        whites.append(int((out == 255).sum()))
    assert all(b > a for a, b in zip(means, means[1:]))
    assert all(b >= a for a, b in zip(whites, whites[1:]))
    assert whites[0] < whites[-1]


def test_render_returns_a_new_rgb_image_and_leaves_the_frame_alone():
    frame = _night_frame()
    before = np.asarray(frame).copy()
    out = DisplayStretch(frame).render(0.8)
    assert out is not frame
    assert out.mode == 'RGB' and out.size == frame.size
    assert np.array_equal(np.asarray(frame), before)


def test_a_flat_frame_renders_black_rather_than_blown_up_noise():
    flat = Image.new('RGB', (64, 64), (4, 4, 4))
    out = np.asarray(DisplayStretch(flat).render(0.5))
    assert out.max() == 0


def test_non_rgb_input_is_converted_once():
    gray = Image.fromarray(np.asarray(_night_frame())[..., 0])
    out = DisplayStretch(gray).render(0.5)
    assert out.mode == 'RGB' and out.size == gray.size
    assert np.asarray(out).max() == 255
