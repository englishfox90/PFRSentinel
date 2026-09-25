# All-Sky Overlay System

Astronomical annotations (constellation lines, planet labels, DSO markers, AltAz grid) overlaid on each all-sky camera frame. Requires a one-time fisheye lens calibration; after that, overlays render automatically on every frame in both Watch and Camera capture modes.

---

## Architecture

```
services/allsky/
├── __init__.py            Public API: render_allsky_overlay()
├── coords.py              Pure-numpy RA/Dec ↔ AltAz, GMST, Bennett refraction
├── planets.py             Meeus Keplerian elements — Mercury–Neptune, Moon, Sun
├── catalogs.py            Loads star_data/ JSON+CSV; module-level cache
├── fisheye.py             FisheyeModel dataclass — projection + JSON persistence
├── calibration.py         Grid-search initial match → scipy.optimize.least_squares fit
├── star_centroid.py       OpenCV blob detection + weighted-moment sub-pixel centroids
├── label_collision.py     Edge-anchored label placement (no overlaps, off the stars)
├── label_stability.py     Frame-to-frame hysteresis: mask vote, sticky top-N, slot memory
├── render_grid.py         AltAz grid, horizon circle, cardinal labels
├── render_constellations.py  IAU/Dien constellation lines + abbreviation labels
├── render_objects.py      Planet circles, Messier diamonds, NGC crosses
└── overlay_renderer.py    Orchestrates all layers; entry point for pipeline

ui/
├── panels/allsky_settings.py      Settings panel: calibration status + layer toggles
└── controllers/allsky_controller.py  Background CalibrationWorker QThread; config save

scripts/
└── allsky_debug.py        CLI calibration + debug tool (see Usage below)

star_data/                 Read-only catalog data (bundled in installer)
├── bsc5-short.json        Yale BSC5 ~9100 stars (V ≤ 6.5)
├── messier_list.json      110 Messier objects
├── NGC.csv                OpenNGC full catalog + addendum (semicolon-delimited)
└── constellations.json    Western IAU constellation lines + embedded HIP coords
```

---

## Pipeline Integration

The overlay is a **transparent post-processing step** — it never changes the pipeline signatures:

```
add_overlays(image_input, overlays, metadata, ...)
    │
    ├── reads metadata['__allsky_config']  (injected by callers below)
    │
    └── calls render_allsky_overlay(img, config, metadata)
```

Two callers inject the config before `add_overlays()`:

| Mode | File | Location |
|---|---|---|
| Camera capture | `ui/controllers/image_processor.py` | `_process_task()` before `add_overlays()` |
| Watch mode | `services/processor.py` | `_inject_allsky_metadata()` called before each `add_overlays()` |

Config flows from `config['allsky_overlay']` + `config['weather']` (for lat/lon/elevation).

---

## Fisheye Lens Model

**Projection:** `r_px = a1·θ + a3·θ³ + a5·θ⁵`

where θ is the angle from the optical axis in radians (0 = zenith, π/2 = horizon).

**Parameters stored in calibration JSON:**

| Field | Description |
|---|---|
| `cx`, `cy` | Optical centre in pixels |
| `a1` | Linear radial coefficient (px/rad). ≈ `sky_radius_px / (π/2)` |
| `a3`, `a5` | Higher-order correction terms |
| `roll` | Camera rotation (radians) |
| `axis_alt`, `axis_az` | True sky direction of the optical axis (degrees) |
| `rms_residual` | Median residual of the fit (pixels) |
| `n_matches` | Number of matched stars used |
| `calibrated_at` | ISO 8601 UTC timestamp |

**Calibration JSON location:** `%LOCALAPPDATA%\PFRSentinel\allsky_calibration.json`

---

## Calibration Algorithm

1. **Star detection** — OpenCV blob detection on background-subtracted image; weighted-moment sub-pixel centroid refinement (~0.3px accuracy)
2. **Catalog lookup** — BSC5 stars (V ≤ 6.5) projected to AltAz for current observer location and UTC time
3. **Grid search over `a1`** — Tests 11 candidate values (sky circle = 40%–99% of half-frame) plus a small centre-offset grid. Picks the model with most initial matches at 50px tolerance
4. **Iterative fit** — `scipy.optimize.least_squares` (TRF method) refines all 8 parameters. Tolerance tightens from 50px → 10px over 8 iterations
5. **Acceptance** — Requires ≥ `min_matches` (default 8) with median residual ≤ 15px

**Failure modes** (all raise `CalibrationError` with a human-readable message):
- Fewer than `min_matches` stars detected
- Fewer than `min_matches` catalog stars above the horizon (check lat/lon + UTC)
- Grid search finds < 3 matches (wrong scale or time)
- Final RMS > `max_residual_px` after fit

---

## Label Stability

Each frame is rendered from scratch, and three stages are noisy at their
margin: star detection (a 4σ star blinks in and out), the visibility mask built
from those detections (an object near a disc edge flips between sky and
obstruction), and the fixed `top_n` budget (one object dropping out promotes
the next-ranked one, and both swap back a frame later). Issues #31 and #13
report the result: labels popping on and off in the timelapse, and the whole
overlay vanishing on frames with fewer than 10 detections.

`label_stability.py` keeps the small amount of state that lets consecutive
frames agree. It is process-wide (`get_label_stabilizer()`) because the
renderer serves one live frame stream; `reset_label_stability()` clears it.

| Piece | What it does | Constant |
|---|---|---|
| `SkyMaskHistory` | Majority vote over the last N detection masks; a frame with no usable mask reuses the last vote for up to M frames, after which the vote is `is_stale` and the equipment map takes over. Frames with 3–9 detections vote sky inside their discs only (`add_partial`); the Moon's glare disc is left out of the denominator | `MASK_VOTE_DEPTH = 15`, `MASK_HOLD_FRAMES = 15` |
| `StickySelection` | An object already on screen stays eligible up to `top_n + margin` and competes with a `margin`-rank bonus, so a newcomer must out-rank it by more than the margin (a rising Moon still displaces it) | `RANK_MARGIN = 3` |
| `LabelGrid(slot_memory=…)` | The candidate slot a label used last frame is tried first, so a new neighbour does not flip it to the other side of its star | — |

A lasting change (a telescope parked across the field) is adopted after about
eight frames. A sustained detection failure never wipes the vote: it goes stale,
and the first real mask afterwards replaces the history rather than being
out-voted by it. The stabilizer is reset at capture start and whenever the
calibration model is set or cleared; a reprocess of the same capture
(`observing_window.SAME_CAPTURE_KEY`) reads the vote without advancing it.

### Visibility plane and the equipment map

`sky_region.visibility_plane` decides where labels may go on each frame:

| Source | When | Module |
|---|---|---|
| Fresh vote ∧ equipment map | the vote is fresh and a map exists — a pixel must be sky in both | `sky_region` |
| Fresh vote | no map yet | `label_stability` |
| Equipment map | the vote is stale | `obstruction_map` |
| Model sky disc `a1 · (π/2) · (1 − SKY_TRIM_FRACTION)`, translated for the output crop | neither exists (first frames of a first session) | `sky_region.model_sky_disc` |

There is no raw-grayscale fallback any more: it passed lit equipment on a
moonlit frame and nothing on a dark one (issue #93, H9).

`obstruction_map.ObstructionMap` is a float32 sky-probability plane on the
vote's 512 px grid, stamped with the output frame size and `OUTPUT_CROP`
(a different stamp starts a new map, never a rescaled one). Frames with
≥ 10 detections pull their discs toward sky; frames with ≥ 40 pull pixels of
the model disc more than two disc radii from every detection toward
equipment, the Moon's glare disc excepted. The update is a running mean up
to `HEAL_FRAMES = 600` frames of evidence per pixel and an EMA with that
time constant after, so a moved scope is forgotten over about two nights.
Only frames the preview path rendered (past the observing-window check;
package 2's observable-sky gate will supply the verdict) teach it. It is
saved to `<app-data>/allsky_obstruction.npz` at most every 10 minutes, at
capture stop and at shutdown; loaded by `AllSkyController` at startup; cleared
by **Reset Calibration** and **Reset Equipment Map**. Saves are atomic
(temp file + `os.replace`) and serialised by their own lock; a truncated or
foreign file is logged and ignored at startup. Resource budget (plan §8),
measured idle on the development container: the in-place update on
preallocated planes p50 2.15 ms / p95 6.8 ms at 3552 px (budget 10 ms); the
plane selection, including the one full-resolution upsample, p50 10.8 ms /
p95 15.5 ms (budget 30 ms). The timed tests log these and assert only a 5x
ceiling, so a loaded runner never fails them. No new threads or timers.

---

## Config Schema

```json
"allsky_overlay": {
    "enabled": false,
    "calibration_file": "",
    "constellations": {
        "enabled": true, "lines": true, "labels": true,
        "color": "#4488FF", "line_width": 1, "label_size": 12, "opacity": 180
    },
    "messier": {
        "enabled": true, "color": "#FF8844",
        "marker_size": 8, "label_size": 10, "opacity": 200
    },
    "ngc": {
        "enabled": false, "min_magnitude": 8.0, "color": "#88FF44",
        "marker_size": 6, "label_size": 9, "opacity": 150
    },
    "planets": {
        "enabled": true, "label_size": 14, "marker_size": 10, "opacity": 255,
        "colors": { "Mercury": "#B0B0B0", "Venus": "#FFFFCC", "Mars": "#FF6644",
                    "Jupiter": "#FFCC88", "Saturn": "#FFDDAA",
                    "Uranus": "#88DDFF", "Neptune": "#4466FF", "Moon": "#FFFFEE" }
    },
    "grid": {
        "enabled": true, "horizon": true, "altitude_rings": true,
        "altitude_step": 30, "azimuth_lines": true, "cardinal_labels": true,
        "color": "#336633", "line_width": 1, "label_size": 14, "opacity": 120
    }
}
```

Also added to the `"weather"` section:
```json
"elevation": ""   // Observer elevation in metres (for refraction correction)
```

And to `"discord"`:
```json
"post_calibration": false   // Post Discord notification on calibration success
```

---

## CLI Debug Tool

`scripts/allsky_debug.py` — run calibration on a saved image without launching the full app.

```powershell
# Star detection only (fast — check star positions on output image)
python scripts\allsky_debug.py "path\to\image.jpg" --detect-only

# Full calibration
python scripts\allsky_debug.py "path\to\image.jpg" `
    --lat 31.5475 --lon -99.3817 `
    --utc "2026-04-09 05:35:00" `
    --cx 375 --cy 375

# Hint the sky circle radius (pixels) to skip grid search
python scripts\allsky_debug.py "path\to\image.jpg" `
    --lat 31.5475 --lon -99.3817 `
    --utc "2026-04-09 05:35:00" `
    --sky-radius 340

# Render overlay using an existing calibration file
python scripts\allsky_debug.py "path\to\image.jpg" `
    --overlay-only --cal "%LOCALAPPDATA%\PFRSentinel\allsky_calibration.json"
```

**Output image:** `<input_name>_debug.jpg`
- Green circles = detected stars
- Yellow crosses = catalog star projected positions (after calibration)

**Key arguments:**

| Argument | Default | Purpose |
|---|---|---|
| `--lat`, `--lon` | from config | Observer coordinates (decimal degrees; W longitude is negative) |
| `--utc` | now | UTC datetime `'YYYY-MM-DD HH:MM:SS'` |
| `--cx`, `--cy` | image centre | Optical centre pixel guess |
| `--sky-radius` | grid search | Known sky circle radius in pixels; `a1 ≈ radius / (π/2)` |
| `--min-matches` | 8 | Relax to 6 for partial-sky or moon-bright frames |
| `--max-mag` | 6.5 | Catalog magnitude limit (increase to 7.0 for more candidates) |
| `--detect-only` | false | Skip calibration; just annotate detected stars |
| `--overlay-only` | false | Skip calibration; render overlay with existing `--cal` file |

---

## Best Practices for Calibration

### Ideal calibration frame
- **Raw / unresized output** from the camera (before resize, JPEG compression, or overlay baking)
- **Long exposure** (20–30s) to capture more stars
- **Clear night** with the roof fully open
- **No Moon** or Moon below horizon (moonlight washes out faint stars)
- **Telescope parked** (less central obstruction)

### Coordinate conversion reference
| Format | Decimal formula |
|---|---|
| `N 31° 32' 51"` | `31 + 32/60 + 51/3600` = **31.5475** |
| `W 99° 22' 54"` | `-(99 + 22/60 + 54/3600)` = **-99.3817** |
| CDT (UTC-5) → UTC | Add 5 hours |
| CST (UTC-6) → UTC | Add 6 hours |

### Diagnosing failures
1. Run `--detect-only` — check the debug image to verify green circles land on real stars (not noise/telescope mount)
2. Check the "Catalog Preview" output — if fewer than 10 catalog stars are shown above the horizon, the UTC time or lat/lon is wrong
3. If detection finds < 10 stars, the image is too compressed/resized; use a raw frame
4. If grid search still fails, provide `--sky-radius` (measure the sky circle radius in pixels from the image)
5. Try `--min-matches 6` for difficult frames

---

## Planet Position Accuracy

Planet positions use Meeus Keplerian elements (Chapter 33, "Astronomical Algorithms" 2nd ed.) — no network dependency.

| Body | Method | Accuracy |
|---|---|---|
| Mercury, Venus, Mars | Truncated VSOP87 + equation of centre | ~1–2 arcmin (2000–2050) |
| Jupiter, Saturn, Uranus, Neptune | Meeus Keplerian elements | ~2–5 arcmin |
| Moon | Meeus Chapter 47 simplified series | ~5–10 arcmin |
| Sun | Meeus Chapter 25 | ~1 arcmin |

Sufficient for labelling on an all-sky camera image (typical pixel scale ~0.5°/px at mag 5).

---

## Build Notes

**`scipy`** is now included in the PyInstaller build (removed from `excludes` in `PFRSentinel.spec`). Adds approximately 30 MB to the installer.

**`star_data/`** files are bundled as `added_files` in `PFRSentinel.spec`:
```python
('star_data/bsc5-short.json', 'star_data'),
('star_data/messier_list.json', 'star_data'),
('star_data/NGC.csv', 'star_data'),
('star_data/constellations.json', 'star_data'),
```

The calibration JSON (`allsky_calibration.json`) is stored in `%LOCALAPPDATA%\PFRSentinel\` and **not** bundled — it is user-specific and generated at runtime.

---

## Tests

```powershell
# Run all allsky tests
.\venv\Scripts\python.exe -m pytest tests\test_allsky_coords.py tests\test_allsky_calibration.py tests\test_allsky_rendering.py -v

# Run full suite (should show 0 regressions)
.\venv\Scripts\python.exe -m pytest
```

| Test file | Tests | Covers |
|---|---|---|
| `test_allsky_coords.py` | 20 | Julian Date, GMST, AltAz round-trips, Bennett refraction, ecliptic→equatorial |
| `test_allsky_calibration.py` | 12 | FisheyeModel projection, JSON persistence, synthetic star detection |
| `test_allsky_rendering.py` | 25 | LabelGrid, each render layer, full pipeline with/without calibration |
| `test_allsky_label_stability.py` | 27 | Mask vote and hold, sticky top-N (incl. the #31 boundary-flicker regression), slot memory, renderer on synthetic frames with an injected low-detection frame |
