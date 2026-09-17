---
globs: "services/allsky/**/*.py"
description: All-sky fisheye calibration and overlay conventions
---

# All-Sky Module Rules

The `services/allsky/` package handles fisheye lens calibration and star/constellation/meteor overlays for an all-sky camera. Active development area — see `docs/ALLSKY_CALIBRATION_PLAN.md` for the full plan before structural changes.

## Calibration data
- Calibration JSON: `allsky_calibration.json` in the app-data root — resolve it via `services.app_config.get_calibration_path()` (built on `utils_paths.get_app_data_dir()`), never a hardcoded platform path. Illustrative: `%LOCALAPPDATA%\PFRSentinel\allsky_calibration.json` on Windows, `~/Library/Application Support/PFRSentinel/` on macOS, `~/.local/share/PFRSentinel/` on Linux. User-generated at runtime, never bundled.
- Required fields: `rms_residual`, `n_matches`, `calibrated_at`. Plus the lens model parameters (`a1`, `cx`, `cy`, `sky_r`, etc.).
- Calibration is **per physical installation** — depends on lens orientation. Never copy across machines.

## Calibration grid search
- Grid search tests **11 candidate `a1` values** (sky circle = 40–99% of half-frame) plus a small centre-offset grid.
- Match tolerance starts at 50px and **tightens to 10px over 8 iterations** — this prevents over-fitting on early bad matches. Don't change without understanding why.
- Wrong scale or wrong UTC time → grid search fails silently with no matches. When debugging "calibration didn't converge", check time/lat/lon inputs first, not the algorithm.

## Pole gate and model admission
- The measured celestial pole (`pole_finder`) is an **optional** constraint. Callers must treat `None` as normal, never as a failure — and never make the check mandatory: a hosting-site field of pier/mount lights produced six contradictory poles in one night (issue #10).
- `find_pole` is stateless. Cross-run trust lives in `pole_consensus.PoleHistory` (owned by `CalibrationService`); a multi-modal history means the pole is UNKNOWN, not "pick the stable one".
- `FisheyeModel.provenance == 'guided'` marks a human-anchored basin. It outranks the measured pole, and `model_admission` locks mirror / plate scale / pole position against it for automatic replacements. Don't add a pole veto to a guided path.
- The model on disk is judged too, not only candidates (`incumbent_evidence`): a trusted pole that agrees with it stamps it `'pole'` in place, and a basin escape is skipped while the incumbent still passes the bright-anchor gate on recent frames. An escape admitted with no evidence needs a material RMS gain (`model_replacement.ESCAPE_MIN_RMS_GAIN`), never a hair's-breadth one — 2026-09-05 lost a correct model on 8.03 vs 8.04 px. Automatic saves back up the previous file to `allsky_calibration.previous.json`.

## Coordinate frames
- RA/Dec → alt/az conversion uses observer lat/lon and UTC. Always pass UTC, not local time.
- Star catalogues (BSC5, Messier, NGC) supply RA/Dec in decimal degrees in the on-disk JSON. If you see HMS/DMS in source, it's a bug.

## Rendering
- Overlay rendering happens after the base image is composed. Don't draw stars onto the raw frame and then run brightness adjustments — the stars will get clipped.
- Constellation lines use the canonical Stellarium sky-cultures index. Don't hand-edit line definitions; pull from upstream.

## When in doubt
Read `docs/ALLSKY_CALIBRATION_PLAN.md` end-to-end. The calibration math is subtle and the plan documents the trade-offs.
