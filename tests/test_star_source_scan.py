"""services/star_source_scan.py + star_detection.analyze_stars — the one
star count: noise-adaptive, inside the sky circle, at the frame's own
resolution, and the same number the imaging-conditions feed reads.

Synthetic frames only: no real sky frames exist in the repository, so the
counts are checked against the stars drawn, not against a real night.
"""
import time

import numpy as np
import pytest

from services import sky_evidence as se
from services import star_source_scan as scan
from services.imaging_conditions.frame_signals import read_frame_signals
from services.star_detection import analyze_frame_stars, analyze_stars, detect_stars, estimate_fwhm


def allsky(n_stars, size=1200, seed=0, sky=18.0, noise=3.0):
    """A sky disc on dark corners with Gaussian stars; returns (frame, circle, drawn)."""
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:size, 0:size]
    cx, cy, r = size / 2 + 10, size / 2 - 8, size * 0.44
    img = np.where((xx - cx) ** 2 + (yy - cy) ** 2 <= r * r, sky, 2.0).astype(np.float32)
    img += rng.normal(0, noise, img.shape).astype(np.float32)
    drawn = []
    for _ in range(n_stars):
        ang, rad = rng.uniform(0, 2 * np.pi), rng.uniform(0, r * 0.85)
        sx, sy = cx + rad * np.cos(ang), cy + rad * np.sin(ang)
        sig = rng.uniform(2.5, 3.5)
        y0, y1, x0, x1 = int(sy) - 14, int(sy) + 15, int(sx) - 14, int(sx) + 15
        py, px = np.mgrid[y0:y1, x0:x1]
        img[y0:y1, x0:x1] += rng.uniform(80, 220) * np.exp(
            -((px - sx) ** 2 + (py - sy) ** 2) / (2 * sig * sig))
        drawn.append((sx, sy))
    return np.clip(img, 0, 255).astype(np.uint8), (cx, cy, r), drawn


@pytest.fixture(scope='module')
def field():
    return allsky(80, seed=3)


def test_scan_finds_the_drawn_stars_at_their_positions(field):
    frame, circle, drawn = field
    stars, grey = scan.scan_frame(frame, circle)
    assert grey is frame
    assert 0.85 * len(drawn) <= len(stars) <= len(drawn)
    found = np.array([(s['x'], s['y']) for s in stars], dtype=float)
    near = [np.min(np.hypot(found[:, 0] - x, found[:, 1] - y)) <= 3.0 for x, y in drawn]
    assert sum(near) >= 0.85 * len(drawn)
    assert all(set(s) == {'x', 'y', 'size'} and isinstance(s['x'], int) for s in stars)


def test_more_stars_count_higher_and_an_empty_sky_counts_near_zero():
    counts = [len(scan.scan_frame(*allsky(n, seed=5)[:2])[0]) for n in (0, 30, 120)]
    assert counts[0] <= 2 and counts[0] < counts[1] < counts[2]


def test_noise_only_frame_counts_no_stars():
    # Amplified sensor noise (a dark closed roof): the threshold rises with it.
    rng = np.random.default_rng(1)
    noise = np.clip(rng.normal(40, 22, (900, 900)), 0, 255).astype(np.uint8)
    assert len(scan.scan_frame(noise)[0]) <= 5


def test_rgb_rgba_and_uint16_count_like_the_grey_frame(field):
    frame, circle, _ = field
    base = len(scan.scan_frame(frame, circle)[0])
    rgb = np.stack([frame] * 3, axis=-1)
    rgba = np.dstack([rgb, np.full(frame.shape, 255, np.uint8)])
    linear16 = frame.astype(np.uint16) * 40     # a dim linear RAW16 frame
    for other in (rgb, rgba, linear16):
        assert abs(len(scan.scan_frame(other, circle)[0]) - base) <= max(2, 0.05 * base)


def test_unreadable_input_is_no_stars_not_an_error():
    for bad in (None, np.zeros((0, 0), np.uint8), np.zeros((4, 4, 2), np.uint8), "frame"):
        assert scan.scan_frame(bad) == ([], None)
    assert analyze_stars(None) == {'STAR_COUNT': '0', 'FWHM': 'N/A', 'SEEING': 'N/A'}
    assert scan.scan_frame(np.full((300, 300), 0.5, np.float32))[0] == []


def test_sources_outside_the_circle_and_in_ignore_rects_are_left_out(field):
    frame, circle, _ = field
    lit = frame.copy()
    yy, xx = np.mgrid[0:41, 0:41]
    blob = (200 * np.exp(-((xx - 20) ** 2 + (yy - 20) ** 2) / 18.0)).astype(np.uint8)
    lit[20:61, 20:61] = np.maximum(lit[20:61, 20:61], blob)      # a light in a dark corner
    inside = len(scan.scan_frame(frame, circle)[0])
    assert len(scan.scan_frame(lit, circle)[0]) == inside
    assert len(scan.scan_frame(lit)[0]) > 0                      # whole frame sees it
    cx, cy, _r = circle
    masked = scan.find_star_like_sources(frame, circle, [(0, 0, frame.shape[1], cy)])
    assert 0 < len(masked) < inside and (masked[:, 1] >= cy - 1).all()


def test_circle_measured_on_a_resized_copy_is_scaled_to_the_frame():
    assert scan.circle_in_frame((500, 480, 400), (1000, 1000), (3000, 3000)) == (1500, 1440, 1200)
    assert scan.circle_in_frame((500, 480, 400), None, (1000, 1000)) == (500, 480, 400)
    assert scan.circle_in_frame((500, 480, 400), (1000, 1000), (1000, 1000)) == (500, 480, 400)
    # Another aspect ratio is another framing, not a resize: whole frame.
    assert scan.circle_in_frame((500, 480, 400), (1000, 1000), (1920, 1080)) is None
    for bad in (None, (1, 2), ('a', 'b', 'c'), (5, 5, 0)):
        assert scan.circle_in_frame(bad, (10, 10), (10, 10)) is None
    assert scan.circle_in_frame((5, 5, 3), (0, 10), (10, 10)) is None


def test_analyze_frame_stars_uses_the_evidence_circle_at_either_size(field):
    frame, circle, _ = field
    expected = analyze_stars(frame, circle)
    assert int(expected['STAR_COUNT']) > 50 and expected['FWHM'] != 'N/A'
    same = {se.EVIDENCE_KEY: {'sky_circle': circle, 'frame_size': frame.shape[::-1]}}
    assert analyze_frame_stars(frame, same) == expected
    # Camera mode: the evidence was measured on a 60 % resized copy.
    half = {se.EVIDENCE_KEY: {'sky_circle': tuple(v * 0.6 for v in circle),
                              'frame_size': (720, 720)}}
    assert analyze_frame_stars(frame, half) == expected
    # No evidence, or a scan that measured no disc: the whole frame.
    whole = analyze_stars(frame)
    assert analyze_frame_stars(frame, {}) == whole
    assert analyze_frame_stars(frame, {se.EVIDENCE_KEY: {'sky_circle': None}}) == whole
    assert analyze_frame_stars(frame, {se.EVIDENCE_KEY: 'garbage'}) == whole


def test_fwhm_is_measured_on_the_scan_plane_without_a_float_copy(field):
    frame, circle, _ = field
    stars, grey = scan.scan_frame(frame, circle)
    fwhm = estimate_fwhm(grey, stars)
    assert 5.0 <= fwhm <= 9.0                       # sigma 2.5-3.5 px -> FWHM 5.9-8.2 px
    assert estimate_fwhm(np.stack([frame] * 3, axis=-1), stars) == fwhm
    assert grey.dtype == np.uint8                   # the caller's plane is left as it was


def test_the_gate_plane_count_is_the_same_scan_at_900_px(field):
    frame, circle, _ = field
    plane, scale = se.reduce_for_evidence(frame)
    scaled = tuple(v * scale for v in circle)
    assert se.count_star_like_sources(plane, scaled) == len(
        scan.find_star_like_sources(plane, scaled))


def test_conditions_read_the_frame_star_count_not_the_gate_plane_count(field):
    frame, circle, _ = field
    metadata = {se.EVIDENCE_KEY: {'star_count': 7, 'sky_circle': circle,
                                  'frame_size': frame.shape[::-1]}}
    metadata.update(analyze_frame_stars(frame, metadata))
    signals = read_frame_signals(metadata)
    assert signals.star_count == int(metadata['STAR_COUNT']) != 7
    assert signals.detected_stars == signals.star_count


@pytest.mark.slow
def test_full_resolution_cost_against_the_detector_it_replaced():
    """Logs p50/p95 for the old and new per-frame star analysis on a
    synthetic 3552 px RGB frame; asserts only that the new path is not
    slower than the old one and a 5x sanity ceiling on the figure in
    star_source_scan's docstring (63 ms), so xdist noise never fails it."""
    from services.logger import app_logger
    from tests.test_sky_evidence import synthetic_allsky

    grey = synthetic_allsky(300)
    rgb = np.dstack([grey] * 3)
    size = grey.shape[0]
    circle = (size / 2 + 30, size / 2 - 20, size * 0.44)

    def old():
        stars = detect_stars(rgb)
        estimate_fwhm(rgb, stars)
        return len(stars)

    def timed(fn, runs=7):
        elapsed = []
        for _ in range(runs):
            started = time.perf_counter()
            result = fn()
            elapsed.append((time.perf_counter() - started) * 1000.0)
        elapsed.sort()
        return elapsed[len(elapsed) // 2], elapsed[-2], result

    old_p50, old_p95, old_count = timed(old)
    new_p50, new_p95, tokens = timed(lambda: analyze_stars(rgb, circle))
    figures = (f"star analysis at 3552 px: old p50 {old_p50:.0f} ms p95 {old_p95:.0f} ms "
               f"({old_count} stars); new p50 {new_p50:.0f} ms p95 {new_p95:.0f} ms "
               f"({tokens['STAR_COUNT']} stars of 300 drawn)")
    app_logger.info(figures)
    print(f"\n{figures}")
    assert 240 <= int(tokens['STAR_COUNT']) <= 300
    assert new_p50 <= old_p50, figures
    assert new_p50 <= 315.0, figures
