r"""
colorize_from_lum.py

Drop-in replacement for PFRSentinel stretch/color tuning.

What this version does (per your spec):
- Uses 4 fixed "DIY overscan" corner ROIs to estimate per-frame:
    - bias (median floor)
    - sigma (MAD-based noise estimate)
  using ROI size 50x50 with 5px margin from each edge.
- Subtracts bias from LUMINANCE ONLY (lum frame), then continues:
    1) stretch luminance using bp/wp + asinh + gamma
    2) stretch RGB using SAME bp/wp/asinh/gamma
    3) inject chroma into stretched luminance (LRGB-style)
- Optional "hot pixel dab" on luminance only, using sigma and a 3x3 median replacement.
  (This targets static sparkle without smearing larger structure.)

Usage (PowerShell example):
& "c:\Astrophotography\PFRSentinel\venv\Scripts\python.exe" `
  "c:\Astrophotography\PFRSentinel\scripts\colorize_from_lum.py" `
  "C:\Users\Paul Fox-Reeks\AppData\Local\PFRSentinel\raw_debug\lum_20260105_003229.fits" `
  "C:\Users\Paul Fox-Reeks\AppData\Local\PFRSentinel\raw_debug\raw_20260105_003229.fits" `
  --black_pct 5 --white_pct 99.9 --asinh 30 --gamma 1.05 `
  --color_strength 1.20 --chroma_clip 0.55 --blue_suppress 0.92 --desaturate 0.05 --blue_floor 0.020 `
  --corner_roi 50 --corner_margin 5 `
  --hp_dab 1 --hp_k 12 `
  --out_dir "colorize_out" --out_name "final_morecolor_v3.png"

Notes:
- Expects color FITS to be (3,H,W) or (H,W,3). If it's 2D Bayer, this script errors.
- This file uses a raw docstring to avoid Windows path unicode escape issues.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from astropy.io import fits
import imageio.v3 as iio


# ----------------------------
# IO / normalization
# ----------------------------

def load_fits(path: str) -> np.ndarray:
    return np.asarray(fits.getdata(path))


def to_hwc_rgb(color: np.ndarray) -> np.ndarray:
    """Accept (3,H,W) or (H,W,3) and return (H,W,3)."""
    if color.ndim == 3 and color.shape[0] == 3:
        return np.transpose(color, (1, 2, 0))
    if color.ndim == 3 and color.shape[2] == 3:
        return color
    raise ValueError(
        f"Unexpected color shape: {color.shape}. "
        "Expected a 3-channel FITS (3,H,W) or (H,W,3), not a 2D Bayer mosaic."
    )


def normalize_if_int(arr: np.ndarray) -> tuple[np.ndarray, dict]:
    """
    Normalize to float32 0..1.

    - integer: divide by dtype max
    - float: if not 0..1-ish, scale by p99.9
    """
    dbg = {"dtype": str(arr.dtype), "raw_min": float(np.min(arr)), "raw_max": float(np.max(arr))}

    if np.issubdtype(arr.dtype, np.integer):
        denom = float(np.iinfo(arr.dtype).max)
        out = arr.astype(np.float32) / denom
        dbg["denom"] = denom
        return np.clip(out, 0, 1), dbg

    out = arr.astype(np.float32)
    p999 = float(np.percentile(out, 99.9))
    dbg["p999"] = p999
    if p999 > 1.5:
        out = out / (p999 + 1e-8)
        dbg["scaled_by_p999"] = True
    else:
        dbg["scaled_by_p999"] = False
    return np.clip(out, 0, 1), dbg


def luminance_from_rgb(rgb01: np.ndarray) -> np.ndarray:
    """Rec.709 luminance from (H,W,3) float 0..1 -> (H,W)."""
    return (
        0.2126 * rgb01[..., 0] +
        0.7152 * rgb01[..., 1] +
        0.0722 * rgb01[..., 2]
    )


# ----------------------------
# Corner ROI "overscan"
# ----------------------------

def _mad_sigma(x: np.ndarray) -> float:
    """Robust sigma estimate from MAD."""
    x = x.astype(np.float32, copy=False).ravel()
    med = np.median(x)
    mad = np.median(np.abs(x - med))
    return float(1.4826 * mad + 1e-8)


def corner_rois(lum01: np.ndarray, roi: int = 50, margin: int = 5) -> tuple[np.ndarray, dict]:
    """
    Return concatenated corner pixels from 4 ROIs.

    roi: size of square ROI (50 => 50x50)
    margin: offset from edge (5 => start at 5px in from each edge)
    """
    h, w = lum01.shape
    r = int(roi)
    m = int(margin)

    if h < (2 * (m + r + 1)) or w < (2 * (m + r + 1)):
        raise ValueError(f"Image too small for roi={roi} margin={margin}: shape={lum01.shape}")

    # Slices: [start : start+r]
    y0, y1 = m, m + r
    x0, x1 = m, m + r
    y2, y3 = h - m - r, h - m
    x2, x3 = w - m - r, w - m

    tl = lum01[y0:y1, x0:x1]
    tr = lum01[y0:y1, x2:x3]
    bl = lum01[y2:y3, x0:x1]
    br = lum01[y2:y3, x2:x3]

    vals = np.concatenate([tl.ravel(), tr.ravel(), bl.ravel(), br.ravel()]).astype(np.float32, copy=False)

    dbg = {
        "roi": r,
        "margin": m,
        "n_vals": int(vals.size),
        "tl_med": float(np.median(tl)),
        "tr_med": float(np.median(tr)),
        "bl_med": float(np.median(bl)),
        "br_med": float(np.median(br)),
        "all_min": float(np.min(vals)),
        "all_p10": float(np.percentile(vals, 10)),
        "all_p50": float(np.percentile(vals, 50)),
        "all_p90": float(np.percentile(vals, 90)),
        "all_max": float(np.max(vals)),
    }
    return vals, dbg


def estimate_bias_sigma_from_corners(lum01: np.ndarray, roi: int, margin: int) -> tuple[float, float, dict]:
    vals, dbg = corner_rois(lum01, roi=roi, margin=margin)
    bias = float(np.median(vals))
    sigma = _mad_sigma(vals)
    dbg["bias"] = bias
    dbg["sigma_mad"] = sigma
    return bias, sigma, dbg


# ----------------------------
# Stretch
# ----------------------------

def stretch_mono(
    mono01: np.ndarray,
    black_pct: float,
    white_pct: float,
    asinh_strength: float,
    gamma: float,
) -> tuple[np.ndarray, dict]:
    """Stretch mono 0..1 using bp/wp percentiles + asinh + gamma."""
    mono01 = np.clip(mono01.astype(np.float32), 0, 1)

    bp = float(np.percentile(mono01, float(black_pct)))
    wp = float(np.percentile(mono01, float(white_pct)))
    if wp <= bp + 1e-8:
        wp = bp + 1e-3

    x = (mono01 - bp) / (wp - bp + 1e-8)
    x = np.clip(x, 0, 1)

    s = float(asinh_strength)
    x = np.arcsinh(s * x) / np.arcsinh(s)

    g = float(gamma)
    if g != 1.0:
        x = np.clip(x, 0, 1) ** (1.0 / g)

    dbg = {
        "black_pct": float(black_pct),
        "white_pct": float(white_pct),
        "black_point": float(bp),
        "white_point": float(wp),
        "asinh_strength": float(s),
        "gamma": float(g),
    }
    return np.clip(x, 0, 1), dbg


def stretch_rgb_using_lum_points(rgb01: np.ndarray, bp: float, wp: float, asinh_strength: float, gamma: float) -> np.ndarray:
    """Apply same bp/wp + asinh + gamma to RGB (0..1)."""
    rgb01 = np.clip(rgb01.astype(np.float32), 0, 1)
    x = (rgb01 - float(bp)) / (float(wp) - float(bp) + 1e-8)
    x = np.clip(x, 0, 1)

    s = float(asinh_strength)
    x = np.arcsinh(s * x) / np.arcsinh(s)

    g = float(gamma)
    if g != 1.0:
        x = np.clip(x, 0, 1) ** (1.0 / g)

    return np.clip(x, 0, 1)


# ----------------------------
# Luma-only hot pixel "dab" (optional)
# ----------------------------

def median3x3_mono(img: np.ndarray) -> np.ndarray:
    img = img.astype(np.float32, copy=False)
    p = np.pad(img, 1, mode="reflect")
    neigh = [
        p[0:-2, 0:-2], p[0:-2, 1:-1], p[0:-2, 2:],
        p[1:-1, 0:-2], p[1:-1, 1:-1], p[1:-1, 2:],
        p[2:,   0:-2], p[2:,   1:-1], p[2:,   2:]
    ]
    return np.median(np.stack(neigh, axis=0), axis=0).astype(np.float32)


def hot_pixel_dab_lum(lum01: np.ndarray, sigma: float, k: float = 12.0, max_luma: float = 0.35) -> tuple[np.ndarray, dict]:
    """
    Replace only extreme outliers with local median.

    Criteria (conservative):
    - only operate where lum is relatively dark (avoid killing true highlights)
    - mark a pixel as hot if it's much brighter than its local median by k*sigma

    lum01 should already be bias-subtracted and clipped to 0..1.
    """
    lum01 = np.clip(lum01.astype(np.float32), 0, 1)
    med = median3x3_mono(lum01)

    thr = float(k) * float(sigma)
    dark_mask = lum01 < float(max_luma)
    hot = dark_mask & ((lum01 - med) > thr)

    out = lum01.copy()
    out[hot] = med[hot]

    dbg = {
        "sigma": float(sigma),
        "k": float(k),
        "threshold": float(thr),
        "max_luma": float(max_luma),
        "hot_pixels": int(np.count_nonzero(hot)),
        "hot_frac": float(np.count_nonzero(hot) / hot.size),
    }
    return out, dbg


# ----------------------------
# Color / chroma
# ----------------------------

def blue_suppress_chroma(rgb01: np.ndarray, strength: float, blue_bias_floor: float = 0.020) -> np.ndarray:
    """
    Reduce blue cast by suppressing blue chroma selectively.
    Only affects pixels where blue chroma is meaningfully dominant (> blue_bias_floor).
    """
    rgb01 = np.clip(rgb01.astype(np.float32), 0, 1)
    y = luminance_from_rgb(rgb01)[..., None]
    c = rgb01 - y

    blue_c = c[..., 2]
    mask = blue_c > float(blue_bias_floor)

    scale = 1.0 - float(np.clip(strength, 0, 1))
    c[..., 2][mask] *= scale

    return np.clip(y + c, 0, 1)


def inject_chroma_into_luminance(lum_stretched01: np.ndarray, rgb_stretched01: np.ndarray, color_strength: float, chroma_clip: float) -> np.ndarray:
    lum_stretched01 = np.clip(lum_stretched01.astype(np.float32), 0, 1)
    rgb_stretched01 = np.clip(rgb_stretched01.astype(np.float32), 0, 1)

    y = luminance_from_rgb(rgb_stretched01)[..., None]
    chroma = rgb_stretched01 - y

    cc = float(max(chroma_clip, 0.0))
    if cc > 0:
        chroma = np.clip(chroma, -cc, cc)

    out = lum_stretched01[..., None] + float(color_strength) * chroma
    return np.clip(out, 0, 1)


def desaturate_global(rgb01: np.ndarray, amount: float) -> np.ndarray:
    rgb01 = np.clip(rgb01.astype(np.float32), 0, 1)
    y = luminance_from_rgb(rgb01)[..., None]
    a = float(np.clip(amount, 0, 1))
    return np.clip((1 - a) * rgb01 + a * y, 0, 1)


# ----------------------------
# Debug / mode (optional)
# ----------------------------

def classify_mode_from_lum(lum01: np.ndarray) -> dict:
    lum01 = np.clip(lum01.astype(np.float32), 0, 1)
    p1 = float(np.percentile(lum01, 1))
    p50 = float(np.percentile(lum01, 50))
    p99 = float(np.percentile(lum01, 99))
    dr = p99 - p1

    if p50 < 0.03 and dr < 0.01:
        return {"mode": "ROOF_CLOSED", "reason": "low median + tight dynamic range"}
    if p50 < 0.08:
        return {"mode": "OPEN_ROOF_NIGHT", "reason": "night scene but brighter than roof-closed"}
    return {"mode": "DAYTIME", "reason": "higher median luminance"}


# ----------------------------
# Main
# ----------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("lum_fits")
    ap.add_argument("color_fits")
    ap.add_argument("--out_dir", default="colorize_out")
    ap.add_argument("--out_name", default=None)

    # Stretch params
    ap.add_argument("--black_pct", type=float, default=5.0)
    ap.add_argument("--white_pct", type=float, default=99.9)
    ap.add_argument("--asinh", type=float, default=30.0)
    ap.add_argument("--gamma", type=float, default=1.05)

    # Color controls (your working preset)
    ap.add_argument("--color_strength", type=float, default=1.20)
    ap.add_argument("--chroma_clip", type=float, default=0.55)
    ap.add_argument("--blue_suppress", type=float, default=0.92)
    ap.add_argument("--blue_floor", type=float, default=0.020)
    ap.add_argument("--desaturate", type=float, default=0.05)

    # Corner ROI overscan
    ap.add_argument("--corner_roi", type=int, default=50)
    ap.add_argument("--corner_margin", type=int, default=5)

    # Luma-only hot pixel dab (optional)
    ap.add_argument("--hp_dab", type=int, default=0, help="1=enable hot pixel dab on LUM before stretch")
    ap.add_argument("--hp_k", type=float, default=12.0, help="threshold multiplier (k*sigma), 10-18 typical")
    ap.add_argument("--hp_max_luma", type=float, default=0.35, help="only dab where luma is below this")

    args = ap.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.out_name is None:
        args.out_name = f"final_bp{args.black_pct:g}_wp{args.white_pct:g}_asinh{args.asinh:g}_g{args.gamma:g}.png"

    # Load + normalize
    lum = load_fits(args.lum_fits)
    col = load_fits(args.color_fits)

    rgb = to_hwc_rgb(col)
    rgb01, rgb_norm_dbg = normalize_if_int(rgb)
    lum01, lum_norm_dbg = normalize_if_int(lum)

    # Estimate per-frame bias + sigma from corners (on lum01)
    bias, sigma, corner_dbg = estimate_bias_sigma_from_corners(
        lum01,
        roi=int(args.corner_roi),
        margin=int(args.corner_margin),
    )

    # Subtract bias from LUM ONLY
    lum01_corr = np.clip(lum01 - bias, 0, 1)

    mode_info = classify_mode_from_lum(lum01_corr)

    # Optional: hot pixel dab (on bias-corrected lum)
    hp_dbg = None
    if int(args.hp_dab) > 0:
        lum01_corr, hp_dbg = hot_pixel_dab_lum(
            lum01_corr,
            sigma=sigma,
            k=float(args.hp_k),
            max_luma=float(args.hp_max_luma),
        )

    # Stretch luminance (this is what drives bp/wp)
    lum_stretched01, lum_stretch_dbg = stretch_mono(
        mono01=lum01_corr,
        black_pct=args.black_pct,
        white_pct=args.white_pct,
        asinh_strength=args.asinh,
        gamma=args.gamma,
    )

    bp = float(lum_stretch_dbg["black_point"])
    wp = float(lum_stretch_dbg["white_point"])

    # Stretch RGB using SAME bp/wp (note: RGB is NOT bias-subtracted by request)
    rgb_stretched01 = stretch_rgb_using_lum_points(
        rgb01=rgb01,
        bp=bp,
        wp=wp,
        asinh_strength=args.asinh,
        gamma=args.gamma,
    )

    # Blue suppression + chroma inject
    if float(args.blue_suppress) > 0:
        rgb_stretched01 = blue_suppress_chroma(
            rgb_stretched01,
            strength=float(args.blue_suppress),
            blue_bias_floor=float(args.blue_floor),
        )

    out01 = inject_chroma_into_luminance(
        lum_stretched01=lum_stretched01,
        rgb_stretched01=rgb_stretched01,
        color_strength=float(args.color_strength),
        chroma_clip=float(args.chroma_clip),
    )

    if float(args.desaturate) > 0:
        out01 = desaturate_global(out01, amount=float(args.desaturate))

    out_u8 = (np.clip(out01, 0, 1) * 255.0).round().astype(np.uint8)
    out_path = out_dir / args.out_name
    iio.imwrite(out_path, out_u8)

    # Debug stats
    y_out = luminance_from_rgb(out01)
    chroma_mag = float(np.mean(np.abs(out01 - y_out[..., None])))

    debug = {
        "inputs": {"lum_fits": args.lum_fits, "color_fits": args.color_fits},
        "normalize": {"lum": lum_norm_dbg, "rgb": rgb_norm_dbg},
        "corner_overscan": corner_dbg,
        "bias_subtract": {"applied_to": "lum_only", "bias": float(bias), "sigma_mad": float(sigma)},
        "hot_pixel_dab": hp_dbg,
        "stretch": {
            "black_pct": float(args.black_pct),
            "white_pct": float(args.white_pct),
            "asinh_strength": float(args.asinh),
            "gamma": float(args.gamma),
            "black_point": bp,
            "white_point": wp,
        },
        "color": {
            "color_strength": float(args.color_strength),
            "chroma_clip": float(args.chroma_clip),
            "blue_suppress": float(args.blue_suppress),
            "blue_floor": float(args.blue_floor),
            "desaturate": float(args.desaturate),
        },
        "mode_info": mode_info,
        "output": {
            "path": str(out_path),
            "shape": list(out_u8.shape),
            "dtype": str(out_u8.dtype),
            "mean_abs_chroma_after": chroma_mag,
        }
    }

    with open(out_dir / "debug.json", "w", encoding="utf-8") as f:
        json.dump(debug, f, indent=2)

    print(f"Saved: {out_path}")
    print(f"Mode: {mode_info['mode']} ({mode_info['reason']})")
    print(f"Corner bias (median): {bias:.6f}   sigma(MAD): {sigma:.6f}")
    if hp_dbg:
        print(f"Hot-pixel dab: {hp_dbg['hot_pixels']} pixels (k={hp_dbg['k']}, thr={hp_dbg['threshold']:.6f})")
    print(f"bp/wp (post-bias LUM): {bp:.6f} / {wp:.6f}")
    print(f"Mean |chroma| after: {chroma_mag:.6f}")
    print("Tip: if corners still look lifted, increase --black_pct slightly (e.g. 6-8) or enable --hp_dab 1.")


if __name__ == "__main__":
    main()
