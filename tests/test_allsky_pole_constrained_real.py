"""Real-data regression: the pole-constrained solve on the reference rig's
2026-09-18 night (issue #93, package 5; plan §0.7).

Runs only on a developer machine that holds the (gitignored) reference-rig
library and the package-0 buffer dump of that night, as
tests/test_allsky_pool_hygiene_real.py does; skips everywhere else. The
detections are rebuilt from the JPEGs with the package-4 detector and the
burned-in text rects, static lights stripped — the pool §0.6 measured.
Ground truth is the guided model recorded in the dump, rescaled to 750 px.

What §0.7 measured and this test holds:
- the rotation pole is found, sigma ~4 px, plate scale within 5 % of the
  guided model's, 11–12 px from where the guided model puts the pole —
  inside the calibrated tolerance with a margin of 3;
- the orientation search seeded by that pole runs in seconds and its
  best candidate, verified through the joint fit, lands in the guided
  basin: same mirror, a1 within 8 %, pole within the tolerance of the
  measured one. On `main` before package 4 every cold-start candidate was
  rejected at chance (§0.6);
- a guided-seeded refinement ends within the pole tolerance whether or
  not the pole term is on (it is neutral on this night, §0.7).
"""
import glob
import json
import math
import os
import time
from datetime import datetime

import numpy as np
import pytest

from services.allsky.calibration_validate import median_sky_r, tol_scale, validate_pole
from services.allsky.catalogs import get_bright_stars
from services.allsky.coords import radec_to_altaz
from services.allsky.detection_filters import DetectionFilters
from services.allsky.fisheye import FisheyeModel
from services.allsky.joint_fit import build_all_matches, joint_iterative_fit, pole_constraint_for
from services.allsky.model_admission import projected_pole
from services.allsky.multi_calibrate import refine_from_detections
from services.allsky.orientation_search import orientation_candidates
from services.allsky.pole_finder import find_pole
from services.allsky.pole_tolerance import pole_tolerance_px
from services.allsky.star_centroid import detect_stars
from services.allsky.static_lights import find_static_lights, strip_static

ROOT = os.path.join(
    os.environ.get('PFR_SAMPLE_IMAGES') or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'sample_images'),
    'reference_rig_2026-09')
LIBRARY = os.path.join(ROOT, 'library', '2026-09-18')
DUMPS = [os.path.join(ROOT, 'buffer_2026-09-18.json'),
         os.path.join(os.environ.get('PFR_SCRATCH', ''), 'refrig', 'buffer_2026-09-18.json')]
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
    guided = FisheyeModel(**{k: v for k, v in payload['model'].items()
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

    frames = []
    filters = DetectionFilters(ignore_rects=RECTS)
    for rec, path in zip(payload['frames'], files):
        dt = datetime.fromisoformat(rec['dt'])
        det = detect_stars(Image.open(path), max_stars=200, sky_cx=rec['sky_cx'],
                           sky_cy=rec['sky_cy'], sky_radius=rec['sky_r'], filters=filters)
        frames.append({'dt': dt, 'detected': det, 'above_horizon': above_horizon(dt),
                       'sky_cx': rec['sky_cx'], 'sky_cy': rec['sky_cy'],
                       'sky_r': rec['sky_r'], 'image_width': 750, 'image_height': 750})
    cleaned = strip_static(frames, find_static_lights(frames))
    pole = find_pole(cleaned, lat)
    return {'guided': guided, 'frames': cleaned, 'lat': lat, 'pole': pole}


def _pole_distance(model, pole, lat):
    xy = projected_pole(model, lat)
    return math.hypot(xy[0] - pole.x, xy[1] - pole.y)


class TestRotationPole:
    def test_found_with_the_guided_plate_scale(self, night):
        pole, guided = night['pole'], night['guided']
        assert pole is not None and pole.source == 'rotation'
        assert pole.sigma_px < 8.0
        assert abs(pole.a1_px_per_rad / guided.a1 - 1.0) < 0.05     # measured 0.975

    def test_guided_model_passes_the_calibrated_gate_with_margin(self, night):
        pole, guided, lat = night['pole'], night['guided'], night['lat']
        sky_r = median_sky_r(night['frames'])
        r_p = math.hypot(pole.x - guided.cx, pole.y - guided.cy)
        tol = pole_tolerance_px(pole.sigma_px, tol_scale(sky_r), r_p)
        d = _pole_distance(guided, pole, lat)
        assert d < 15.0                     # measured 11.5 px
        assert tol > 2.5 * d                # measured 40 px, ×3.5
        assert validate_pole(guided, lat, pole, sky_r=sky_r)[0]


class TestOrientationSearch:
    def test_seeded_search_is_fast(self, night):
        t0 = time.perf_counter()
        cands = orientation_candidates(night['frames'], pole=night['pole'],
                                       lat_deg=night['lat'], east_left_hint=True)
        wall = time.perf_counter() - t0
        assert cands
        assert wall < 25.0, f"{wall:.1f} s — over the 5x sanity ceiling of the 5 s budget"


class TestColdStart:
    def test_pole_seeded_cold_start_lands_in_the_guided_basin(self, night):
        """§0.7: candidate 1 passes (1985 matches, 2.16× chance), a1 0.95×
        the guided model's, pole 32 px from the measured (tol 40)."""
        guided, lat, pole = night['guided'], night['lat'], night['pole']
        model = refine_from_detections(night['frames'], None, max_residual_px=20.0,
                                       east_left_hint=True, pole=pole, lat_deg=lat)
        assert model.east_left is guided.east_left
        assert abs(model.a1 / guided.a1 - 1.0) < 0.08
        sky_r = median_sky_r(night['frames'])
        assert validate_pole(model, lat, pole, sky_r=sky_r)[0]
        gp = projected_pole(guided, lat)
        mp = projected_pole(model, lat)
        assert math.hypot(gp[0] - mp[0], gp[1] - mp[1]) < 30.0     # measured 21 px
        assert model.chance_ratio > 2.0


class TestSeededRefinement:
    def test_pole_term_is_neutral_on_a_seed_in_the_basin(self, night):
        guided, lat, pole, frames = night['guided'], night['lat'], night['pole'], night['frames']
        ts = tol_scale(median_sky_r(frames))
        plain = FisheyeModel(**{**guided.__dict__, 'provenance': ''})
        con = pole_constraint_for(pole, lat, frames, plain)
        assert con is not None
        results = []
        for constraint in (None, con):
            matches = build_all_matches(frames, plain, tol_px=50.0 * ts, min_per_image=4)
            model, rms = joint_iterative_fit(matches, frames, plain, 4, 20, 20.0,
                                             tol_scale_factor=ts, pole=constraint)
            results.append((model, rms))
        (free, rms_free), (held, rms_held) = results
        assert abs(rms_held - rms_free) < 0.2
        assert abs(held.a1 / free.a1 - 1.0) < 0.02
        sky_r = median_sky_r(frames)
        assert validate_pole(held, lat, pole, sky_r=sky_r)[0]      # 30 px of 40
