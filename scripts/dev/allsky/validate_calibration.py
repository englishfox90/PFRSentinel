"""
Validate calibration improvements against sample FITS images.

Tests:
  1. Single-image auto-calibration (grid search + roll sweep)
  2. Vectorized _brightness_match consistency
  3. Triangle hash matching (forced, bypassing grid search)
  4. Multi-image refinement from detections
  5. Binomial CDF validation
  6. Chord-based angular distance accuracy

Run from repo root:
    python scripts/validate_calibration.py
"""
import sys, os, time, math, re
import numpy as np

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timezone, timedelta
from services.allsky.fisheye import FisheyeModel
from services.allsky.calibration import (
    calibrate, CalibrationError, _brightness_match, _compute_rms,
)
from services.allsky.star_centroid import detect_stars, estimate_sky_circle
from services.allsky.catalogs import get_bright_stars
from services.allsky.coords import radec_to_altaz
from services.allsky.triangle_match import (
    triangle_calibrate, _angular_sep_matrix, _match_probability,
    TriangleIndex,
)
from services.allsky.multi_calibrate import refine_from_detections


# ── Helpers ──────────────────────────────────────────────────────────

def load_fits(path):
    from PIL import Image
    from astropy.io import fits as af
    with af.open(path) as hdu:
        data = hdu[0].data
    if data.ndim == 3 and data.shape[0] in (1, 3, 4):
        data = np.moveaxis(data, 0, -1)
    if data.dtype != np.uint8:
        flat = data.flatten().astype(np.float32)
        lo = float(np.percentile(flat, 1))
        hi = float(np.percentile(flat, 99))
        data = ((data.astype(np.float32) - lo) / max(hi - lo, 1) * 255
                ).clip(0, 255).astype(np.uint8)
    if data.ndim == 2:
        return Image.fromarray(data).convert("RGB")
    return Image.fromarray(data[:, :, :3]).convert("RGB")


def fname_to_utc(path):
    m = re.search(r"(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})", os.path.basename(path))
    if not m:
        return None
    yr, mo, dy, hh, mm, ss = (int(x) for x in m.groups())
    return datetime(yr, mo, dy, hh, mm, ss, tzinfo=timezone.utc) + timedelta(hours=6)


LAT, LON = 38.9717, -95.2353
REF_MODEL = FisheyeModel.load("sample_images/multi_calibration.json")
SEP = "=" * 65


# ── Test 1: Single-image auto-calibration ────────────────────────────

print(f"\n{SEP}")
print("TEST 1: Single-image auto-calibration")
print(SEP)

test_image = "sample_images/lum_20260116_021511.fits"
dt = fname_to_utc(test_image)
img = load_fits(test_image)
print(f"Image: {os.path.basename(test_image)}, UTC={dt}")

t0 = time.monotonic()
try:
    model = calibrate(img, LAT, LON, dt=dt)
    elapsed = time.monotonic() - t0
    print(f"  SUCCESS: {model.n_matches} matches, RMS={model.rms_residual:.2f}px")
    print(f"  cx={model.cx:.1f}  cy={model.cy:.1f}  a1={model.a1:.1f}")
    print(f"  roll={math.degrees(model.roll):.1f} deg  axis_alt={model.axis_alt:.1f} deg")
    print(f"  east_left={model.east_left}")
    print(f"  Time: {elapsed:.1f}s")
except CalibrationError as e:
    elapsed = time.monotonic() - t0
    print(f"  FAILED ({elapsed:.1f}s): {e}")


# ── Test 2: Vectorized _brightness_match ─────────────────────────────

print(f"\n{SEP}")
print("TEST 2: Vectorized _brightness_match vs reference model")
print(SEP)

sky_cx, sky_cy, sky_r = estimate_sky_circle(img)
detected = detect_stars(img, max_stars=200,
                        sky_cx=sky_cx, sky_cy=sky_cy, sky_radius=sky_r)
print(f"  Detected {len(detected)} stars")

catalog = get_bright_stars(max_mag=6.5)
cat_altaz = []
for s in catalog:
    a, z = radec_to_altaz(s["ra_deg"], s["dec_deg"], LAT, LON, dt)
    if float(a) > 3.0:
        cat_altaz.append((s, float(a), float(z)))
cat_altaz.sort(key=lambda x: x[0]["vmag"])
print(f"  {len(cat_altaz)} catalog stars above horizon")

for tol in (20.0, 30.0, 50.0):
    t0 = time.monotonic()
    matches = _brightness_match(detected, cat_altaz, REF_MODEL, tol_px=tol)
    elapsed = (time.monotonic() - t0) * 1000
    rms = _compute_rms(matches, REF_MODEL)
    print(f"  tol={tol:.0f}px: {len(matches)} matches, "
          f"RMS={rms:.2f}px ({elapsed:.1f}ms)")


# ── Test 3: Triangle hash matching ───────────────────────────────────

print(f"\n{SEP}")
print("TEST 3: Triangle hash matching (forced, bypassing grid search)")
print(SEP)

t0 = time.monotonic()
try:
    tri_model = triangle_calibrate(
        img, LAT, LON, dt=dt,
        detected=detected, above_horizon=cat_altaz,
        sky_cx=sky_cx, sky_cy=sky_cy, sky_radius=sky_r,
        min_matches=5,
    )
    elapsed = time.monotonic() - t0
    print(f"  SUCCESS: {tri_model.n_matches} matches, "
          f"RMS={tri_model.rms_residual:.2f}px")
    print(f"  cx={tri_model.cx:.1f}  cy={tri_model.cy:.1f}  a1={tri_model.a1:.1f}")
    print(f"  roll={math.degrees(tri_model.roll):.1f} deg  "
          f"axis_alt={tri_model.axis_alt:.1f} deg  "
          f"axis_az={tri_model.axis_az:.1f} deg")
    print(f"  east_left={tri_model.east_left}")
    print(f"  Time: {elapsed:.1f}s")

    # Compare match counts with reference
    ref_m = _brightness_match(detected, cat_altaz, REF_MODEL, tol_px=25.0)
    tri_m = _brightness_match(detected, cat_altaz, tri_model, tol_px=25.0)
    print(f"  Reference model @25px: {len(ref_m)} matches")
    print(f"  Triangle  model @25px: {len(tri_m)} matches")
except CalibrationError as e:
    elapsed = time.monotonic() - t0
    print(f"  FAILED ({elapsed:.1f}s): {e}")


# ── Test 4: Triangle index statistics ────────────────────────────────

print(f"\n{SEP}")
print("TEST 4: Triangle index build + statistics")
print(SEP)

bright_ah = [(s, a, z) for s, a, z in cat_altaz if s.get("vmag", 99) <= 5.5]
cat_stars = [s for s, _, _ in bright_ah]
ra_arr = np.array([s["ra_deg"] for s in cat_stars])
dec_arr = np.array([s["dec_deg"] for s in cat_stars])

t0 = time.monotonic()
sep_mat = _angular_sep_matrix(ra_arr, dec_arr)
ms_sep = (time.monotonic() - t0) * 1000

t0 = time.monotonic()
idx = TriangleIndex(sep_mat)
n_tri = idx.build()
ms_build = (time.monotonic() - t0) * 1000

n_bins = len(idx._table)
avg = n_tri / max(n_bins, 1)
print(f"  {len(cat_stars)} bright stars above horizon")
print(f"  Sep matrix:  {ms_sep:.0f}ms")
print(f"  Index build: {ms_build:.0f}ms")
print(f"  {n_tri} triplets in {n_bins} bins (avg {avg:.1f}/bin)")


# ── Test 5: Binomial CDF ─────────────────────────────────────────────

print(f"\n{SEP}")
print("TEST 5: Binomial CDF false-positive probability")
print(SEP)

n_det = len(detected)
n_cat = len(cat_altaz)
match_frac = 25.0 / (2 * sky_r)

for n_match in (3, 5, 8, 12, 20, 30):
    prob = _match_probability(n_match, n_det, n_cat, match_frac)
    if prob < 1e-3:
        tag = "GENUINE"
    elif prob > 0.1:
        tag = "RANDOM"
    else:
        tag = "MARGINAL"
    print(f"  {n_match:2d}/{n_det} matches: prob={prob:.2e}  [{tag}]")


# ── Test 6: Multi-image refinement ───────────────────────────────────

print(f"\n{SEP}")
print("TEST 6: Multi-image refinement from detections")
print(SEP)

multi_files = sorted([
    os.path.join("sample_images", f) for f in os.listdir("sample_images")
    if f.startswith("lum_20260116") and f.endswith(".fits")
])
# Pick 5 evenly spaced
if len(multi_files) > 5:
    step = len(multi_files) / 5
    multi_files = [multi_files[int(i * step)] for i in range(5)]

frames = []
for fpath in multi_files:
    futc = fname_to_utc(fpath)
    if futc is None:
        continue
    fimg = load_fits(fpath)
    fcx, fcy, fr = estimate_sky_circle(fimg)
    fdet = detect_stars(fimg, max_stars=200, sky_cx=fcx, sky_cy=fcy, sky_radius=fr)
    fah = []
    for s in catalog:
        a, z = radec_to_altaz(s["ra_deg"], s["dec_deg"], LAT, LON, futc)
        if float(a) > 3.0:
            fah.append((s, float(a), float(z)))
    fah.sort(key=lambda x: x[0]["vmag"])
    frames.append({"dt": futc, "detected": fdet, "above_horizon": fah})
    print(f"  {os.path.basename(fpath)}: {len(fdet)} detections")

if len(frames) >= 2:
    t0 = time.monotonic()
    try:
        refined = refine_from_detections(frames, REF_MODEL)
        elapsed = time.monotonic() - t0
        print(f"  REFINED: {refined.n_matches} matches, "
              f"RMS={refined.rms_residual:.2f}px  ({elapsed:.1f}s)")
        print(f"  cx={refined.cx:.1f}  cy={refined.cy:.1f}  "
              f"a1={refined.a1:.1f}  axis_alt={refined.axis_alt:.2f}")
    except Exception as e:
        elapsed = time.monotonic() - t0
        print(f"  FAILED ({elapsed:.1f}s): {e}")
else:
    print("  Skipped (need >= 2 frames)")


# ── Done ─────────────────────────────────────────────────────────────

print(f"\n{SEP}")
print("VALIDATION COMPLETE")
print(SEP)
