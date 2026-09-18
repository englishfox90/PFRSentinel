"""
Per-match diagnostic list for the multi-image joint fit.

Split out of multi_calibrate.py (file-size cap) — same format as the
single-image calibration diagnostics, just built from every frame's matches.
"""
import numpy as np


def collect_diagnostics(all_matches, model, frames) -> list:
    """Build per-match diagnostic list (same format as single-image calibration)."""
    diag = []
    for img_idx, img_matches in enumerate(all_matches):
        dt_label = frames[img_idx]['dt'].isoformat() if img_idx < len(frames) else ''
        for (dx, dy), star, (alt, az) in img_matches:
            cat_px  = model.altaz_to_pixel(alt, az)
            res_px  = float(np.hypot(dx - cat_px[0], dy - cat_px[1])) if cat_px else 999.0
            diag.append({
                'name':       star.get('name', ''),
                'vmag':       float(star.get('vmag', 0.0)),
                'alt':        float(alt),
                'az':         float(az),
                'frame_time': dt_label,
                'detected_px': (float(dx), float(dy)),
                'catalog_px':  (float(cat_px[0]), float(cat_px[1])) if cat_px else None,
                'residual_px': res_px,
            })
    diag.sort(key=lambda s: s['residual_px'])
    return diag
