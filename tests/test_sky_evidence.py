"""services/sky_evidence.py — frame evidence for the observable-sky gate (issue #93).

Synthetic frames are what CI runs. The real-data tests at the bottom use the
reference rig's 256 px community frames (NINA's roof state as ground truth)
and its image library, and skip when that data is not on this machine —
real observatory data never enters the repository.
"""
import csv
import glob
import os
import sqlite3
import time
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest
from PIL import Image

from services import sky_evidence as se
from services.config_defaults import DEFAULT_CONFIG
from services.allsky.star_centroid import detect_stars, fallback_sky_circle, measure_sky_circle
from services.observing_window import (
    NO_STARS_CONFIRM_FRAMES, RECOVERY_STAR_FACTOR, REASON_KEY, is_observing_window,
    reset_roof_gate)

REPO = os.path.join(os.path.dirname(__file__), '..')
ML_SET = os.path.join(REPO, 'sample_images', 'reference_rig_2026-09', 'ml_contribution')
LIBRARY = os.path.join(REPO, 'sample_images', 'reference_rig_2026-09', 'library')
# The reference rig's site (tests/test_allsky_anchor_gate_real.py) and its
# UTC offset in summer, for the night recomputation the sidecars' stale
# time_context cannot provide (its README: filenames are local, UTC-5).
REFERENCE_LAT, REFERENCE_LON, REFERENCE_UTC_OFFSET_H = 38.9717, -95.2353, -5
# Burned-in text blocks on the library JPEGs, (x0, y0, x1, y1) in 750 px
# frame pixels, from the dataset README.
LIBRARY_TEXT_RECTS = [(0, 0, 160, 72), (590, 0, 750, 80), (0, 690, 130, 750), (715, 715, 750, 750)]
# The shipped floor, calibrated on the reference rig's real frames (the two
# real-data tests print the tables): a dimly lit closed roof carries 20-80
# star-like sources on the evidence plane (lights, reflections, residual
# grain), a clear night 206-445.
DEFAULT_FLOOR = int(DEFAULT_CONFIG['allsky_overlay']['min_star_detections'])

CONFIG = {'allsky_overlay': {'min_exposure_s': 0.5, 'min_star_detections': 15},   # synthetic fields are sparse
          'weather': {}}


@pytest.fixture(autouse=True)
def _fresh_state():
    reset_roof_gate()
    se.reset_sky_evidence_cache()
    yield
    reset_roof_gate()
    se.reset_sky_evidence_cache()


def synthetic_allsky(n_stars, size=3552, seed=0, sky=18.0, noise=3.0):
    """A fisheye disc on black corners with a dark pier wedge and Gaussian
    stars (FWHM 3.5-6 px, half of them faint) at full ASI676MC resolution."""
    rng = np.random.default_rng(seed)
    h = w = size
    yy, xx = np.mgrid[0:h, 0:w]
    cx, cy, r = w / 2 + 30, h / 2 - 20, size * 0.44
    disc = (xx - cx) ** 2 + (yy - cy) ** 2 <= r * r
    img = np.where(disc, sky, 2.0).astype(np.float32)
    img += rng.normal(0, noise, img.shape).astype(np.float32)
    img[(yy > cy) & (np.abs(xx - cx) < 200)] = 3.0
    for _ in range(n_stars):
        ang = rng.uniform(0, 2 * np.pi)
        rad = rng.uniform(0, r * 0.83)
        sx, sy = cx + rad * np.cos(ang), cy + rad * np.sin(ang)
        sig = rng.uniform(1.5, 2.5)
        peak = rng.uniform(20, 60) if rng.random() < 0.5 else rng.uniform(60, 240)
        x0, x1, y0, y1 = int(sx) - 10, int(sx) + 11, int(sy) - 10, int(sy) + 11
        if x0 < 0 or y0 < 0 or x1 >= w or y1 >= h:
            continue
        py, px = np.mgrid[y0:y1, x0:x1]
        img[y0:y1, x0:x1] += peak * np.exp(-((px - sx) ** 2 + (py - sy) ** 2) / (2 * sig * sig))
    return np.clip(img, 0, 255).astype(np.uint8)


def _rgb(gray):
    return Image.fromarray(np.stack([gray] * 3, axis=-1))


@pytest.fixture(scope='module')
def star_field():
    return _rgb(synthetic_allsky(120, seed=7))


# --- exposure parsing -----------------------------------------------------

@pytest.mark.parametrize('text, expected', [
    ("30.0s", 30.0), ("30s", 30.0), ("13 s", 13.0), ("0.06s", 0.06),
    ("500ms", 0.5), ("500 ms", 0.5), ("8ms", 0.008), ("32us", 32e-6), ("32µs", 32e-6),
    ("2m", 120.0), ("1.5 min", 90.0), ("30", 30.0), ("30.0", 30.0), ("0.21", 0.21),
    ("1e-3", 0.001), ("20.66s (auto)", 20.66), ("  15S ", 15.0), ("12 sec", 12.0),
    (30, 30.0), (0.5, 0.5), (0, 0.0),
])
def test_exposure_formats_parse_to_seconds(text, expected):
    assert se.parse_exposure_seconds(text) == pytest.approx(expected)


@pytest.mark.parametrize('text', [None, "", "N/A", "?", "—", "auto", "-3s", True, float('nan')])
def test_unreadable_exposure_is_none_and_never_blocks(text):
    assert se.parse_exposure_seconds(text) is None
    metadata = {'EXPOSURE': text}
    assert is_observing_window(CONFIG, metadata, feature="test") is True


def test_telemetry_bar_formats_through_the_shared_parser():
    pytest.importorskip("PySide6")
    from ui.components.telemetry_bar import TelemetryBar
    assert TelemetryBar._fmt_exposure("30.0s") == "30.00s"
    assert TelemetryBar._fmt_exposure("500ms") == "500ms"
    assert TelemetryBar._fmt_exposure("2m") == "2.00m"
    assert TelemetryBar._fmt_exposure("auto") == "auto"
    assert TelemetryBar._fmt_exposure(None) == "—"


# --- ML signals from either pipeline ---------------------------------------

def test_ml_signals_come_from_results_in_camera_mode_and_tokens_in_watch_mode():
    camera = {'_ML_RESULTS': {'stars_visible': True, 'frame_is_static': True},
              'STARS_VISIBLE': 'No'}
    assert se.ml_star_signals(camera) == {'stars_visible': True, 'frame_is_static': True}
    assert se.ml_star_signals({'STARS_VISIBLE': 'Yes'}) == {'stars_visible': True, 'frame_is_static': False}
    assert se.ml_star_signals({'STARS_VISIBLE': 'No'})['stars_visible'] is False
    assert se.ml_star_signals({'STARS_VISIBLE': 'N/A'})['stars_visible'] is None
    assert se.ml_star_signals({})['stars_visible'] is None


# --- evidence on the frame --------------------------------------------------

def test_evidence_fields_and_sky_circle_in_frame_pixels(star_field):
    metadata = {'EXPOSURE': '20.0s'}
    ev = se.compute_sky_evidence(star_field, metadata, CONFIG)

    assert metadata[se.EVIDENCE_KEY] is ev
    assert set(ev) == {'star_count', 'exposure_s', 'sky_circle', 'frame_size'}
    assert ev['exposure_s'] == 20.0
    assert ev['frame_size'] == (3552, 3552)
    assert ev['star_count'] >= 15
    cx, cy, r = ev['sky_circle']
    # The synthetic disc is centred at (1806, 1756) with radius 1563, trimmed
    # 15 % inward by the measurement: the circle must come back in frame
    # pixels, not in the reduced plane's.
    assert abs(cx - 1806) < 40 and abs(cy - 1756) < 40
    assert 1200 < r < 1500


def test_count_matches_detect_stars_on_the_same_plane():
    """The cheap counter must mean the same thing as the calibration feed's
    detector on a star field: same thresholds, minus the sub-pixel
    centroiding. (On a noise-only frame it deliberately differs — see the
    signed-residual note in count_star_like_sources.)"""
    for n_stars in (40, 300):
        plane, _ = se.reduce_for_evidence(_rgb(synthetic_allsky(n_stars, seed=n_stars)))
        circle = measure_sky_circle(plane) or fallback_sky_circle(plane.shape[1], plane.shape[0])
        fast = se.count_star_like_sources(plane, circle)
        reference = len(detect_stars(plane, max_stars=10000, sky_cx=circle[0],
                                     sky_cy=circle[1], sky_radius=circle[2]))
        assert abs(fast - reference) <= max(3, int(reference * 0.15)), (n_stars, fast, reference)
    plane, _ = se.reduce_for_evidence(_rgb(synthetic_allsky(0, seed=0)))
    circle = measure_sky_circle(plane) or fallback_sky_circle(plane.shape[1], plane.shape[0])
    assert se.count_star_like_sources(plane, circle) <= 10


def test_more_stars_count_higher_and_an_empty_disc_counts_near_zero():
    counts = []
    for n_stars in (0, 40, 300):
        ev = se.compute_sky_evidence(_rgb(synthetic_allsky(n_stars, seed=1)), {'EXPOSURE': '10s'}, CONFIG)
        counts.append(ev['star_count'])
    assert counts[0] <= 3
    assert counts[0] < counts[1] < counts[2]


def test_noise_only_and_lit_ceiling_count_no_stars():
    rng = np.random.default_rng(3)
    noise = Image.fromarray(np.clip(rng.normal(8, 6, (1200, 1200)), 0, 255).astype(np.uint8))
    ceiling = np.clip(rng.normal(120, 10, (1200, 1200)), 0, 255).astype(np.uint8)
    import cv2
    ceiling = Image.fromarray(cv2.GaussianBlur(ceiling, (0, 0), 3))
    assert se.compute_sky_evidence(noise, {'EXPOSURE': '13s'}, CONFIG)['star_count'] == 0
    se.reset_sky_evidence_cache()
    assert se.compute_sky_evidence(ceiling, {'EXPOSURE': '13s'}, CONFIG)['star_count'] == 0


def test_numpy_frames_give_the_same_count_as_pil(star_field):
    pil = se.compute_sky_evidence(star_field, {'EXPOSURE': '10s'}, CONFIG)['star_count']
    se.reset_sky_evidence_cache()
    arr = se.compute_sky_evidence(np.asarray(star_field), {'EXPOSURE': '10s'}, CONFIG)['star_count']
    assert abs(pil - arr) <= max(2, pil // 10)


def test_a_grey_library_sized_frame_is_brought_to_the_plane():
    gray = synthetic_allsky(60, size=750, seed=4)
    plane, scale = se.reduce_for_evidence(Image.fromarray(gray))
    assert plane.shape == (900, 900) and scale == pytest.approx(1.2)
    ev = se.compute_sky_evidence(Image.fromarray(gray), {'EXPOSURE': '10s'}, CONFIG)
    assert ev['star_count'] > 0 and ev['frame_size'] == (750, 750)


def test_config_object_with_get_is_accepted(star_field):
    class _Config:
        def get(self, key, default=None):
            return CONFIG.get(key, default)

    ev = se.compute_sky_evidence(star_field, {'EXPOSURE': '0.1s'}, _Config())
    assert ev['star_count'] is None       # the floor from the object applied


# --- the scan is skipped when a cheaper rule decides the frame ------------

def _scan_calls(monkeypatch):
    calls = []
    real = se._scan

    def spy(image, ignore_rects=None):
        calls.append(1)
        return real(image, ignore_rects)

    monkeypatch.setattr(se, '_scan', spy)
    return calls


def test_short_exposure_skips_the_scan(monkeypatch, star_field):
    calls = _scan_calls(monkeypatch)
    ev = se.compute_sky_evidence(star_field, {'EXPOSURE': '0.06s'}, CONFIG)
    assert ev['star_count'] is None and ev['exposure_s'] == 0.06
    assert calls == []


def test_a_zero_floor_disables_the_exposure_skip(monkeypatch, star_field):
    calls = _scan_calls(monkeypatch)
    cfg = {'allsky_overlay': {'min_exposure_s': 0, 'min_star_detections': 15}, 'weather': {}}
    ev = se.compute_sky_evidence(star_field, {'EXPOSURE': '0.06s'}, cfg)
    assert ev['star_count'] is not None and calls == [1]


def test_absent_exposure_is_scanned(monkeypatch, star_field):
    calls = _scan_calls(monkeypatch)
    ev = se.compute_sky_evidence(star_field, {}, CONFIG)
    assert ev['exposure_s'] is None and ev['star_count'] is not None and calls == [1]


def test_static_frame_skips_the_scan(monkeypatch, star_field):
    calls = _scan_calls(monkeypatch)
    ev = se.compute_sky_evidence(
        star_field, {'EXPOSURE': '10s', '_ML_RESULTS': {'frame_is_static': True}}, CONFIG)
    assert ev['star_count'] is None and calls == []


def test_daylight_skips_the_scan(monkeypatch, star_field):
    calls = _scan_calls(monkeypatch)
    monkeypatch.setattr('astral.sun.elevation', lambda *a, **kw: 10.0)
    cfg = dict(CONFIG, weather={'latitude': '51.5', 'longitude': '-0.1'})
    assert se.compute_sky_evidence(star_field, {'EXPOSURE': '10s'}, cfg)['star_count'] is None
    assert calls == []
    monkeypatch.setattr('astral.sun.elevation', lambda *a, **kw: -20.0)
    assert se.compute_sky_evidence(star_field, {'EXPOSURE': '10s'}, cfg)['star_count'] is not None


def test_a_failed_scan_leaves_no_evidence_rather_than_raising(monkeypatch, star_field):
    monkeypatch.setattr(se, '_scan', lambda image, rects=None: (_ for _ in ()).throw(RuntimeError("cv2")))
    ev = se.compute_sky_evidence(star_field, {'EXPOSURE': '10s'}, CONFIG)
    assert ev['star_count'] is None and ev['sky_circle'] is None


def test_the_sky_circle_is_measured_once_per_size_then_refreshed(monkeypatch, star_field):
    measured = []
    import services.allsky.star_centroid as sc   # imported lazily by the scan
    real = sc.measure_sky_circle

    def spy(plane, *a, **kw):
        measured.append(plane.shape)
        return real(plane, *a, **kw)

    monkeypatch.setattr(sc, 'measure_sky_circle', spy)
    for _ in range(3):
        se.compute_sky_evidence(star_field, {'EXPOSURE': '10s'}, CONFIG)
    assert measured == [(900, 900)]
    assert list(se._circle_cache._entries) == [(3552, 3552)]

    # Another frame size is another camera: its own circle, even though every
    # plane is the same 900 px.
    other = Image.fromarray(synthetic_allsky(30, size=1000, seed=2))
    se.compute_sky_evidence(other, {'EXPOSURE': '10s'}, CONFIG)
    assert measured == [(900, 900), (900, 900)]
    assert sorted(se._circle_cache._entries) == [(1000, 1000), (3552, 3552)]

    monkeypatch.setattr(se, 'CIRCLE_REFRESH_FRAMES', 2)
    se.compute_sky_evidence(star_field, {'EXPOSURE': '10s'}, CONFIG)   # 4th frame of this size
    assert len(measured) == 3


# --- resource budget (plan §8) ---------------------------------------------

@pytest.mark.slow
def test_evidence_stays_within_the_per_frame_budget(star_field):
    """Budget (plan §8): ≤ 30 ms per 3552 px frame, single core. The test
    measures and logs p50/p95 over 50 frames (the first carries the
    sky-circle measurement; the rest are steady state) and asserts only a
    sanity ceiling of 5x the budget, so a genuine regression fails while
    scheduler noise from the other xdist workers never does — the hard
    budget is verified from the measured figures, not from CI.

    Measured idle, 2026-09-25 (this container, cv2 single-threaded):
    p50 19 ms, p95 23 ms, fastest 18 ms, first frame 50-100 ms; on the same
    box loaded by two suites and five agents p50 20-28 ms, p95 40-56 ms."""
    import cv2
    from services.logger import app_logger
    threads = cv2.getNumThreads()
    cv2.setNumThreads(1)
    try:
        elapsed = []
        for _ in range(50):
            metadata = {'EXPOSURE': '20.0s'}
            started = time.perf_counter()
            ev = se.compute_sky_evidence(star_field, metadata, CONFIG)
            elapsed.append((time.perf_counter() - started) * 1000.0)
    finally:
        cv2.setNumThreads(threads)
    ordered = sorted(elapsed)
    fastest, p50, p95 = ordered[0], ordered[24], ordered[47]
    assert ev['star_count'] is not None
    figures = (f"sky evidence at 3552 px: p50 {p50:.1f} ms, p95 {p95:.1f} ms, "
               f"fastest {fastest:.1f} ms, first {elapsed[0]:.1f} ms, max {ordered[-1]:.1f} ms")
    app_logger.info(figures)
    print(f"\n{figures}")
    assert p50 <= 150.0, f"{figures} — p50 over the 5x sanity ceiling of the 30 ms budget"


def test_no_full_resolution_plane_is_kept(star_field):
    """The cache holds circles, never pixels."""
    se.compute_sky_evidence(star_field, {'EXPOSURE': '10s'}, CONFIG)
    for circle, _age in se._circle_cache._entries.values():
        assert circle is None or len(circle) == 3


# --- one count whatever the input size ----------------------------------------

def test_the_same_sky_counts_alike_at_any_input_size():
    """The camera pipeline measures its post-resize frame: a 3552 px rig at
    60 % hands over 2131 px, at 50 % 1776 px. The fixed evidence plane must
    make those count within 15 % of the full frame."""
    full = _rgb(synthetic_allsky(300, seed=11))
    counts = {}
    for edge in (3552, 2131, 1776):
        frame = full if edge == 3552 else full.resize((edge, edge), Image.Resampling.LANCZOS)
        se.reset_sky_evidence_cache()
        counts[edge] = se.compute_sky_evidence(frame, {'EXPOSURE': '10s'}, CONFIG)['star_count']
    reference = counts[3552]
    assert reference >= 30, counts
    for edge, count in counts.items():
        assert abs(count - reference) <= 0.15 * reference, counts


def test_a_small_frame_is_interpolated_up_to_the_plane():
    plane, scale = se.reduce_for_evidence(Image.fromarray(synthetic_allsky(60, size=256, seed=4)))
    assert plane.shape == (900, 900) and scale == pytest.approx(900 / 256)
    plane, scale = se.reduce_for_evidence(np.zeros((300, 600), dtype=np.uint8))
    assert plane.shape == (450, 900) and scale == pytest.approx(1.5)


def test_ignore_rects_take_burned_in_text_out_of_the_count():
    gray = synthetic_allsky(0, size=1200, seed=5)
    for x in range(400, 600, 12):         # a line of bright glyph-sized blobs in the disc
        gray[304:312, x:x + 6] = 255
    frame = Image.fromarray(gray)
    se.reset_sky_evidence_cache()
    with_text = se.compute_sky_evidence(frame, {'EXPOSURE': '10s'}, CONFIG)['star_count']
    se.reset_sky_evidence_cache()
    masked = se.compute_sky_evidence(frame, {'EXPOSURE': '10s'}, CONFIG,
                                     ignore_rects=[(390, 290, 610, 330)])['star_count']
    assert with_text > masked


# --- real data: the reference rig (skipped when absent) --------------------

def _sun_elevation_local(stamp: str) -> float:
    from astral import LocationInfo
    from astral.sun import elevation
    when = (datetime.strptime(stamp, '%Y%m%d_%H%M%S').replace(tzinfo=timezone.utc)
            - timedelta(hours=REFERENCE_UTC_OFFSET_H))
    loc = LocationInfo(latitude=REFERENCE_LAT, longitude=REFERENCE_LON)
    return float(elevation(loc.observer, dateandtime=when))


def _ml_samples():
    """(stamp, category, roof_open, exposure_s, cloud, sun_alt, plane data)
    for the community set, night frames only (sun below civil twilight,
    recomputed from the stamp — the sidecar flag is known wrong)."""
    fits = pytest.importorskip('astropy.io.fits')
    manifest = os.path.join(ML_SET, 'MANIFEST.csv')
    if not os.path.exists(manifest):
        pytest.skip('reference-rig ML contribution set not present')
    rows = []
    with open(manifest, newline='') as fh:
        for row in csv.DictReader(fh):
            path = os.path.join(ML_SET, f"lum_{row['stamp']}.fits")
            if not os.path.exists(path):
                continue
            sun_alt = _sun_elevation_local(row['stamp'])
            if sun_alt > -6.0:
                continue
            with fits.open(path) as hdu:
                data = hdu[0].data.astype(np.float32)
            rows.append((row['stamp'], row['category'], row['roof_open'] == 'True',
                         float(row['exposure']), row['cloud_pct'], sun_alt, data))
    if not rows:
        pytest.skip('no night samples in the set')
    return rows


def _library_frames():
    """(name, roof from library.db, PIL frame) for every library JPEG."""
    db_path = os.path.join(LIBRARY, 'library.db')
    frames = sorted(glob.glob(os.path.join(LIBRARY, '2026-09-*', '*.jpg')))
    if not frames or not os.path.exists(db_path):
        pytest.skip('reference-rig library not present')
    db = sqlite3.connect(db_path)
    try:
        rows = []
        for path in frames:
            stamp = os.path.basename(path)[:15]
            captured = int((datetime.strptime(stamp, '%Y%m%d_%H%M%S').replace(tzinfo=timezone.utc)
                            - timedelta(hours=REFERENCE_UTC_OFFSET_H)).timestamp())
            hit = db.execute("select roof from images where abs(captured_at - ?) <= 2",
                             (captured,)).fetchone()
            rows.append((os.path.basename(path), hit[0] if hit else None,
                         Image.open(path).convert('RGB')))
    finally:
        db.close()
    return rows


def _count(image, ignore_rects=None):
    se.reset_sky_evidence_cache()
    cfg = {'allsky_overlay': {'min_exposure_s': 0, 'min_star_detections': DEFAULT_FLOOR},
           'weather': {}}
    return se.compute_sky_evidence(image, {'EXPOSURE': '13s'}, cfg, ignore_rects)['star_count']


def test_real_community_frames_closed_roof_counts_under_open_sky(capsys):
    """256 px luminance FITS, NINA's roof state as truth, interpolated up to
    the evidence plane. Prints the table; asserts the separation at
    the shipped floor."""
    rows = _ml_samples()
    table = [(stamp, cat, roof_open, exp, cloud, sun_alt, _count(data))
             for stamp, cat, roof_open, exp, cloud, sun_alt, data in rows]
    with capsys.disabled():
        print("\nstamp            category          roof    exp(s)  cloud  sun    stars@900")
        for stamp, cat, roof_open, exp, cloud, sun_alt, count in table:
            print(f"{stamp:<16} {cat:<17} {'open' if roof_open else 'CLOSED':<7} {exp:>6.2f}  "
                  f"{str(cloud):<5}  {sun_alt:>5.1f}  {count}")
    closed = [c for _, _, o, _, _, _, c in table if not o]
    opened = [c for _, _, o, _, _, _, c in table if o]
    with capsys.disabled():
        print(f"closed max {max(closed) if closed else None}, open min {min(opened) if opened else None}; "
              f"default floor {DEFAULT_FLOOR} separates: "
              f"{bool(closed and opened and max(closed) < DEFAULT_FLOOR <= min(opened))}")
    assert closed and opened
    assert max(closed) < DEFAULT_FLOOR <= min(opened), (max(closed), min(opened))
    assert min(opened) >= RECOVERY_STAR_FACTOR * DEFAULT_FLOOR   # recovery reachable


def test_real_library_frames_text_does_not_register_and_roof_states_separate(capsys):
    """750 px finished JPEGs with burned-in text. Counts with and without the
    text rects masked must agree (the text is not read as stars), and the
    roof states recorded in library.db must separate at the shipped floor."""
    rows = _library_frames()
    table = [(name, roof, _count(img), _count(img, LIBRARY_TEXT_RECTS)) for name, roof, img in rows]
    with capsys.disabled():
        by_roof = {}
        for name, roof, raw, masked in table:
            by_roof.setdefault(roof, []).append(masked)
        print(f"\nlibrary frames: {len(table)}")
        for roof, counts in by_roof.items():
            print(f"  roof {roof!s:<7} n={len(counts):<3} stars@900 (text masked) "
                  f"min/median/max {min(counts)}/{int(np.median(counts))}/{max(counts)}")
        drift = [abs(raw - masked) for _, _, raw, masked in table]
        print(f"  |unmasked - masked| max {max(drift)}")
    for name, roof, raw, masked in table:
        assert abs(raw - masked) <= 2, (name, raw, masked)
    closed = [m for _, roof, _, m in table if roof == 'Closed']
    opened = [m for _, roof, _, m in table if roof == 'Open']
    assert closed and opened
    assert max(closed) < DEFAULT_FLOOR <= min(opened), (max(closed), min(opened))


def test_real_closed_roof_at_13s_is_blanked_by_the_no_stars_rule():
    """Sep 17 23:59:46, roof Closed 100 % at a 13 s exposure: the exposure
    floor cannot fire, the roof verdict is removed, rule 5 must (plan §7.1)."""
    matches = glob.glob(os.path.join(LIBRARY, '2026-09-17', '20260917_235946_*.jpg'))
    if not matches:
        pytest.skip('reference-rig library frame not present')
    frame = Image.open(matches[0]).convert('RGB')
    cfg = {'allsky_overlay': {'min_exposure_s': 0.5, 'min_star_detections': DEFAULT_FLOOR},
           'weather': {}}
    verdicts = []
    for _ in range(NO_STARS_CONFIRM_FRAMES):
        metadata = {'EXPOSURE': '13s'}
        ev = se.compute_sky_evidence(frame, metadata, cfg, LIBRARY_TEXT_RECTS)
        assert ev['exposure_s'] == 13.0 and ev['star_count'] is not None
        verdicts.append(is_observing_window(cfg, metadata, feature="test"))
    assert ev['star_count'] < DEFAULT_FLOOR
    assert verdicts[:-1] == [True] * (NO_STARS_CONFIRM_FRAMES - 1)
    assert verdicts[-1] is False and metadata[REASON_KEY] == 'no_stars'
