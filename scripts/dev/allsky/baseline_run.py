"""
Offline calibration baseline over the sample_images set.

Runs single-image calibrate() over every lum_20260116_*.fits frame and
records success/failure, n_matches, RMS, and the failure reason. Writes a
markdown summary to docs/dev/allsky_baseline_<date>.md.

This is the regression evidence the ALLSKY_RELIABILITY_PLAN "Offline
validation protocol" mandates before/after Phase 2/4 changes. Dev-only —
not imported by the app.

Run from repo root:
    python scripts/dev/allsky/baseline_run.py [--date YYYY-MM-DD] [--limit N]
"""
import argparse
import os
import re
import sys
import time
from datetime import datetime, timezone, timedelta

import numpy as np

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))))

from services.allsky.calibration import calibrate, CalibrationError
from services.allsky.star_centroid import detect_stars, estimate_sky_circle
from services.allsky.calibration_validate import validate_bright_anchors

# Observer for the sample set (see ALLSKY_RELIABILITY_PLAN "Offline validation").
LAT, LON = 38.97, -95.24

# Confirmed star->pixel anchors for lum_20260116_021511.fits (UTC 08:15:11).
# Any model that misses these by >40px is wrong regardless of its RMS.
REF_FRAME = "lum_20260116_021511.fits"
REF_ANCHORS = {
    "Regulus": (1160.5, 2274.4),
    "Procyon": (1991.3, 2458.4),
    "Alkaid":  (769.6, 1028.4),
    "Mizar":   (916.2, 1006.7),
    "Alioth":  (982.5, 1056.2),
    "Merak":   (1242.8, 1243.3),
    "Dubhe":   (1303.3, 1137.1),
}


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
    m = re.search(r"(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})",
                  os.path.basename(path))
    if not m:
        return None
    yr, mo, dy, hh, mm, ss = (int(x) for x in m.groups())
    # filenames are CST; true UTC = filename + 6h
    return datetime(yr, mo, dy, hh, mm, ss, tzinfo=timezone.utc) + timedelta(hours=6)


def anchor_accuracy(model, img, dt):
    """Return {name: miss_px} for the confirmed reference anchors."""
    from services.allsky.catalogs import get_bright_stars
    from services.allsky.coords import radec_to_altaz
    catalog = {s["name"]: s for s in get_bright_stars(max_mag=6.5)
               if s.get("name") in REF_ANCHORS}
    out = {}
    for name, (px, py) in REF_ANCHORS.items():
        s = catalog.get(name)
        if s is None:
            out[name] = float("nan")
            continue
        alt, az = radec_to_altaz(s["ra_deg"], s["dec_deg"], LAT, LON, dt)
        xy = model.altaz_to_pixel(float(alt), float(az))
        out[name] = float("inf") if xy is None else float(
            np.hypot(xy[0] - px, xy[1] - py))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"))
    ap.add_argument("--limit", type=int, default=0,
                    help="process only the first N frames (0 = all)")
    args = ap.parse_args()

    repo = os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))))
    sample_dir = os.path.join(repo, "sample_images")
    files = sorted(f for f in os.listdir(sample_dir)
                   if f.startswith("lum_20260116") and f.endswith(".fits"))
    if args.limit:
        files = files[:args.limit]

    print(f"Baseline over {len(files)} frames (lat={LAT}, lon={LON})")
    rows = []
    t_start = time.monotonic()
    for i, fname in enumerate(files):
        path = os.path.join(sample_dir, fname)
        dt = fname_to_utc(path)
        try:
            img = load_fits(path)
        except Exception as e:
            rows.append((fname, "LOAD_FAIL", 0, 0.0, str(e)[:80], None))
            print(f"  [{i+1}/{len(files)}] {fname}: LOAD_FAIL {e}")
            continue

        t0 = time.monotonic()
        try:
            model = calibrate(img, LAT, LON, dt=dt)
            elapsed = time.monotonic() - t0
            anchors = None
            if fname == REF_FRAME:
                anchors = anchor_accuracy(model, img, dt)
            rows.append((fname, "OK", model.n_matches,
                         round(model.rms_residual, 2), "", anchors))
            print(f"  [{i+1}/{len(files)}] {fname}: OK "
                  f"{model.n_matches} matches RMS={model.rms_residual:.2f}px "
                  f"({elapsed:.1f}s)")
        except CalibrationError as e:
            elapsed = time.monotonic() - t0
            rows.append((fname, "FAIL", 0, 0.0, str(e).split(chr(10))[0][:120],
                         None))
            print(f"  [{i+1}/{len(files)}] {fname}: FAIL ({elapsed:.1f}s) {e}")

    total_s = time.monotonic() - t_start
    n_ok = sum(1 for r in rows if r[1] == "OK")
    ok_rms = [r[3] for r in rows if r[1] == "OK"]
    median_rms = float(np.median(ok_rms)) if ok_rms else 0.0

    out_dir = os.path.join(repo, "docs", "dev")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f"allsky_baseline_{args.date}.md")
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(f"# All-Sky Calibration Baseline — {args.date}\n\n")
        fh.write("Single-image `calibrate()` over the sample set. Produced by "
                 "`scripts/dev/allsky/baseline_run.py`.\n\n")
        fh.write(f"- Observer: lat {LAT}, lon {LON} (UTC = filename + 6h)\n")
        fh.write(f"- Frames: {len(files)}\n")
        fh.write(f"- **Success rate: {n_ok}/{len(files)} "
                 f"({100*n_ok/max(len(files),1):.1f}%)**\n")
        fh.write(f"- Median RMS (successful frames): {median_rms:.2f}px\n")
        fh.write(f"- Wall time: {total_s:.0f}s\n\n")

        ref = next((r for r in rows if r[0] == REF_FRAME), None)
        if ref and ref[5]:
            fh.write(f"## Reference-frame anchor accuracy ({REF_FRAME})\n\n")
            fh.write("Confirmed star->pixel pairs; >40px = wrong fit.\n\n")
            fh.write("| Star | Miss (px) |\n|---|---|\n")
            for name, miss in ref[5].items():
                fh.write(f"| {name} | {miss:.1f} |\n")
            worst = max(v for v in ref[5].values() if np.isfinite(v))
            fh.write(f"\nWorst anchor miss: {worst:.1f}px\n\n")

        fh.write("## Per-frame results\n\n")
        fh.write("| Frame | Status | n_matches | RMS px | Reason |\n")
        fh.write("|---|---|---|---|---|\n")
        for fname, status, nm, rms, reason, _ in rows:
            fh.write(f"| {fname} | {status} | {nm} | {rms} | {reason} |\n")

    print(f"\nSuccess: {n_ok}/{len(files)} "
          f"({100*n_ok/max(len(files),1):.1f}%), median RMS {median_rms:.2f}px")
    print(f"Wrote {out_path}")


if __name__ == "__main__":
    main()
