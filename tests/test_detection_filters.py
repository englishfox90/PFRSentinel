"""Tests for services.allsky.detection_filters through detect_stars.

Synthetic stretched frames with drawn silhouettes: round stars, a satellite
trail, glints on the rim of a dark scope, a burned-in text box.
"""
import numpy as np
import pytest

from services.allsky.detection_filters import (
    ASPECT_MAX, DARK_FLOOR, DARK_SKY_FRACTION, STREAK_MIN_LONG_PX, DetectionFilters,
    dark_threshold, in_ignore_rect, is_streak)
from services.allsky.star_centroid import detect_stars

W = 750
CX, CY, R = 375.0, 375.0, 330.0


def _frame(sky=60, seed=0):
    rng = np.random.default_rng(seed)
    img = np.full((W, W), sky, dtype=np.float32)
    img += rng.normal(0, 2.0, img.shape)
    yy, xx = np.mgrid[:W, :W]
    outside = np.hypot(xx - CX, yy - CY) > R + 5
    img[outside] = 0
    return img, rng


def _star(img, x, y, peak=120.0, sigma=1.2):
    yy, xx = np.mgrid[:W, :W]
    img += peak * np.exp(-((xx - x) ** 2 + (yy - y) ** 2) / (2 * sigma ** 2))


def _finish(img):
    return np.clip(img, 0, 255).astype(np.uint8)


STARS = [(200.0, 250.0), (400.0, 180.0), (520.0, 400.0), (300.0, 520.0), (450.0, 300.0)]


def _detect(img, filters):
    return detect_stars(img, max_stars=500, sky_cx=CX, sky_cy=CY, sky_radius=R,
                        filters=filters)


def _found(dets, x, y, tol=2.0):
    return any(np.hypot(dx - x, dy - y) <= tol for dx, dy, _ in dets)


class TestRules:
    def test_is_streak(self):
        assert is_streak(20, 3)
        diag = np.eye(24, dtype=bool) | np.eye(24, k=1, dtype=bool)   # a 45° trail
        assert is_streak(24, 24, component_mask=diag)
        yy, xx = np.mgrid[:11, :11]
        disc = (xx - 5) ** 2 + (yy - 5) ** 2 <= 16                     # a round star
        assert not is_streak(9, 9, component_mask=disc)
        assert not is_streak(4, 1)                   # too short to judge
        assert not is_streak(8, 4)                   # aspect 2: a coma'd star
        assert is_streak(STREAK_MIN_LONG_PX * 2 + 1, 2, scale=2.0)
        assert not is_streak(STREAK_MIN_LONG_PX * 2 - 1, 2, scale=2.0)
        assert ASPECT_MAX == 3.2

    def test_in_ignore_rect(self):
        rects = [(0, 0, 100, 50)]
        assert in_ignore_rect(10, 10, rects)
        assert not in_ignore_rect(100, 10, rects)   # exclusive upper edge
        assert not in_ignore_rect(10, 60, rects)

    def test_dark_threshold_follows_a_dark_sky(self):
        bright = np.full((50, 50), 120, dtype=np.uint8)
        dim = np.full((50, 50), 24, dtype=np.uint8)
        assert dark_threshold(bright) == DARK_FLOOR
        assert dark_threshold(dim) == pytest.approx(DARK_SKY_FRACTION * 24)


class TestOnFrames:
    def test_default_filters_keep_every_round_star(self):
        img, _ = _frame()
        for x, y in STARS:
            _star(img, x, y)
        dets = _detect(_finish(img), DetectionFilters())
        assert all(_found(dets, x, y) for x, y in STARS)

    def test_streak_is_dropped_stars_are_not(self):
        img, _ = _frame()
        for x, y in STARS:
            _star(img, x, y)
        yy, xx = np.mgrid[:W, :W]   # a 30 px trail, ~3 px wide
        trail = (np.abs(yy - (0.5 * xx + 100)) < 1.7) & (xx > 300) & (xx < 330)
        img[trail] += 120
        raw = _detect(_finish(img), None)
        filtered = _detect(_finish(img), DetectionFilters())
        assert len(raw) == len(filtered) + 1
        assert all(_found(filtered, x, y) for x, y in STARS)

    def test_ignore_rects_drop_burned_in_text(self):
        img, _ = _frame()
        for x, y in STARS:
            _star(img, x, y)
        # "text": a row of bright 4x6 glyphs inside the sky circle
        for k in range(6):
            img[330:336, 200 + 8 * k:204 + 8 * k] += 150
        raw = _detect(_finish(img), None)
        filtered = _detect(_finish(img), DetectionFilters(ignore_rects=[(195, 325, 260, 340)]))
        assert len(raw) >= len(filtered) + 6
        assert all(_found(filtered, x, y) for x, y in STARS)

    def test_dark_neighbourhood_rule_drops_rim_artefacts_when_enabled(self):
        """A small dark object (a cable end, a bracket) leaves a bright rim
        in the background-subtracted residual — the detector reports it as
        a source at the object's centre. Its darkest neighbour gives it away."""
        img, _ = _frame(sky=60)
        for x, y in STARS:
            _star(img, x, y)
        img[430:446, 150:166] = 8          # 16 px dark block
        off = _detect(_finish(img), DetectionFilters())
        on = _detect(_finish(img), DetectionFilters(reject_dark_neighbourhood=True))
        assert _found(off, 158.0, 438.0, tol=8.0)     # the rim's centroid, roughly
        assert not _found(on, 158.0, 438.0, tol=8.0)
        assert all(_found(on, x, y) for x, y in STARS)

    def test_dark_rule_on_a_dark_sky_keeps_the_stars(self):
        """Sky at 20/255: the fixed floor would reject every star; the
        threshold caps at half the sky level so only real silhouettes count."""
        img, _ = _frame(sky=20)
        for x, y in STARS:
            _star(img, x, y, peak=80.0)
        on = _detect(_finish(img), DetectionFilters(reject_dark_neighbourhood=True))
        assert all(_found(on, x, y) for x, y in STARS)

    def test_no_filters_is_the_old_behaviour(self):
        img, _ = _frame()
        for x, y in STARS:
            _star(img, x, y)
        assert _detect(_finish(img), None) == detect_stars(
            _finish(img), max_stars=500, sky_cx=CX, sky_cy=CY, sky_radius=R)
