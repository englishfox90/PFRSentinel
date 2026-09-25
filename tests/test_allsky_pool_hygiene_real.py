"""Real-data regression: pool hygiene on the reference rig's 2026-09-18 night.

Runs only on a developer machine that holds the (gitignored) reference-rig
library and the package-0 buffer dump of that night; skips everywhere else,
as tests/test_allsky_anchor_gate_real.py does. First replay findings are in
docs/ALLSKY_HOSTING_SITE_PLAN.md §0.6: every frame carried exactly the
200-detection cap — equipment texture, JPEG grain, burned-in overlay — and
the guided-seeded refinement was rejected at 1.15x chance although it held
the basin. This test holds the package-4 filters to reducing that.
"""
import glob
import json
import os
import re
from datetime import datetime

import numpy as np
import pytest

from services.allsky.calibration import CalibrationError
from services.allsky.catalogs import get_bright_stars
from services.allsky.chance_matches import estimate_chance
from services.allsky.coords import radec_to_altaz
from services.allsky.detection_filters import DetectionFilters
from services.allsky.fisheye import FisheyeModel
from services.allsky.multi_calibrate import refine_from_detections
from services.allsky.star_centroid import detect_stars
from services.allsky.static_lights import find_static_lights, strip_static

# The dataset lives in the (gitignored) repo sample_images/ folder; a
# developer machine that keeps it elsewhere points PFR_SAMPLE_IMAGES at it.
ROOT = os.path.join(
    os.environ.get('PFR_SAMPLE_IMAGES') or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'sample_images'),
    'reference_rig_2026-09')
LIBRARY = os.path.join(ROOT, 'library', '2026-09-18')
DUMPS = [os.path.join(ROOT, 'buffer_2026-09-18.json'),
         os.path.join(os.environ.get('PFR_SCRATCH', ''), 'refrig', 'buffer_2026-09-18.json')]
# Burned-in text boxes (README.md of the dataset), 750 px image pixels.
RECTS = [(0, 0, 160, 72), (590, 0, 750, 80), (0, 690, 130, 750), (715, 715, 750, 750)]

pytestmark = pytest.mark.slow


def _dump_path():
    for p in DUMPS:
        if p and os.path.isfile(p):
            return p
    return None


@pytest.fixture(scope='module')
def night():
    dump = _dump_path()
    files = sorted(glob.glob(os.path.join(LIBRARY, '*.jpg')))
    if dump is None or not files:
        pytest.skip("reference-rig library night / buffer dump not present")
    pytest.importorskip('PIL')
    from PIL import Image
    payload = json.load(open(dump, encoding='utf-8'))
    if len(payload['frames']) != len(files):
        pytest.skip("dump and library night disagree on frame count")
    model = FisheyeModel(**{k: v for k, v in payload['model'].items()
                            if k in FisheyeModel.__dataclass_fields__})
    lat, lon = payload['lat'], payload['lon']
    catalog = get_bright_stars(max_mag=6.5)
    ra = np.array([s['ra_deg'] for s in catalog])
    dec = np.array([s['dec_deg'] for s in catalog])

    def above_horizon(dt):
        alt, az = radec_to_altaz(ra, dec, lat, lon, dt)
        out = [(catalog[i], float(alt[i]), float(az[i])) for i in np.flatnonzero(alt > 3.0)]
        out.sort(key=lambda t: t[0]['vmag'])
        return out

    def build(filters):
        frames = []
        for rec, path in zip(payload['frames'], files):
            dt = datetime.fromisoformat(rec['dt'])
            det = detect_stars(Image.open(path), max_stars=200, sky_cx=rec['sky_cx'],
                               sky_cy=rec['sky_cy'], sky_radius=rec['sky_r'], filters=filters)
            frames.append({'dt': dt, 'detected': det, 'above_horizon': above_horizon(dt),
                           'sky_cx': rec['sky_cx'], 'sky_cy': rec['sky_cy'],
                           'sky_r': rec['sky_r'], 'image_width': 750, 'image_height': 750})
        return frames
    raw = build(None)
    filtered = build(DetectionFilters(ignore_rects=RECTS))
    cleaned = strip_static(filtered, find_static_lights(filtered))
    return {'model': model, 'raw': raw, 'cleaned': cleaned, 'dump': payload}


def _chance_ratio(frames, model):
    """The joint fit's own verdict on `frames` seeded from `model`."""
    try:
        m = refine_from_detections(frames, model, max_residual_px=20.0)
        return m.n_matches / max(getattr(m, 'chance_expected', 0.0), 1.0), True
    except CalibrationError as e:
        found = re.search(r'\(([\d.]+)x chance\)', str(e))
        if not found:
            raise
        return float(found.group(1)), False


class TestFirstReplayIsReproduced:
    def test_dump_was_at_the_cap_and_the_detector_no_longer_is(self, night):
        # The dump (main's detector) sat on the 200 cap on every frame; the
        # signed-sigma detector reads the same brightest stars but no grain.
        assert all(len(f['detected']) == 200 for f in night['dump']['frames'])
        counts = [len(f['detected']) for f in night['raw']]
        assert max(counts) < 200 and np.median(counts) < 170


class TestHygiene:
    def test_static_strip_removes_the_equipment_texture(self, night):
        raw = [len(f['detected']) for f in night['raw']]
        cleaned = [len(f['detected']) for f in night['cleaned']]
        assert max(cleaned) < 200
        assert sum(cleaned) < 0.9 * sum(raw)     # 25 lights, ~15 % of the pool

    def test_chance_expectation_falls(self, night):
        before = estimate_chance(night['raw'], night['model'], 3.8).expected
        after = estimate_chance(night['cleaned'], night['model'], 3.8).expected
        assert after < 0.9 * before        # §0.6: 315 -> 268 at 3.8 px

    def test_guided_seed_scores_higher_against_chance(self, night):
        """§0.6: 1.15x on main's pool. The sigma fix alone lifts the raw
        pool to 2.20x; the cleaned pool reads 1.97x at the wider tolerance
        the schedule stops at there (expectation grows as tol²), and at
        equal tolerance its expectation is 15 % lower."""
        raw, _ = _chance_ratio(night['raw'], night['model'])
        cleaned, _ = _chance_ratio(night['cleaned'], night['model'])
        assert raw > 2.0
        assert cleaned > 1.15 + 0.5
