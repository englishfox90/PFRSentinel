# scripts/dev/allsky/

One-off experiments and dev utilities for the all-sky fisheye calibration system. None of these are called at runtime by the app — they're archaeological / ad-hoc tools used during calibration development.

Prefer the production calibration path in `services/allsky/` for any runtime work. Read `docs/ALLSKY_CALIBRATION_PLAN.md` before touching calibration.

| Script | Purpose |
|--------|---------|
| `allsky_debug.py` | Interactive debug tool — run calibration on a single image with verbose output |
| `allsky_gridcal.py` | Grid-search fisheye calibration (original) |
| `allsky_manual_cal.py` | Manual-correspondence calibration from user-supplied pixel↔star pairs |
| `allsky_multi.py` | Multi-image calibration experiment |
| `allsky_multi_cal.py` | Multi-image **joint** calibration — fits one shared lens model across a sequence |
| `allsky_rectify.py` | Rectify fisheye image to gnomonic (TAN) projection |
| `allsky_v5_cal.py` | Cross-sky calibration v5 — fixes clustered-stars bias from v2 |
| `analyze_calibration_data.py` | Batch stats over calibration JSON files |
| `analyze_modes.py` | Mode classification stats for FITS-file threshold tuning |
| `backfill_calibration.py` | One-off — add missing fields to historical calibration JSONs |
| `library_to_buffer.py` | Image-library night (+ `library.db`) → calibration buffer dump, the same JSON the app writes (issue #93) |
| `replay_buffer.py` | Replay a buffer dump through pole finder, joint fit, chance gate, admission and replacement — prints what the service would have logged (issue #93) |
| `plate_solve_export.py` | Export a crop of an all-sky FITS for external plate solving |
| `validate_calibration.py` | Regression tests calibration quality against sample FITS set |
