# All-Sky on a Hosting-Site Rig — Plan for Issue #93

> **Context:** Written 2026-09-25 from issue #93 (discussion #76, reporter @ibguthrie,
> v3.7.7, Death Valley Observatories hosting field, ASI676MC 3552×3552, output crop
> 2840×2840). Companion docs: [`ALLSKY_POLE_ANCHOR_PLAN.md`](ALLSKY_POLE_ANCHOR_PLAN.md)
> (pole gate, provenance, model admission — read it first),
> [`ALLSKY_RELIABILITY_PLAN.md`](ALLSKY_RELIABILITY_PLAN.md),
> [`ALLSKY_CALIBRATION_PLAN.md`](ALLSKY_CALIBRATION_PLAN.md).
> File:line references are as of `main` at `770ad39`; re-verify before editing.
>
> This is a **plan**, not a change log. It is written so that each work package can be
> handed to a coding agent as a self-contained brief and merged on its own.

## 0. Two facts that change the issue's proposals

### 0.1 Polaris is not visible on this rig

The reporter's videos show the pier covering the region where Polaris would be; the
"Polaris" label sits beside the orange pier LED on every clear night. So the pole
`pole_finder` reports at (1409, 2699) "on every run across Sep 17, 18 and 19" is **not
Polaris**. It is the LED. The mechanism is the documented residual risk in
`ALLSKY_POLE_ANCHOR_PLAN.md` ("a bright in-band light within ~200 px of a *hidden* pole can
still clear the floor; a stable single contaminant is unimodal by definition"), and the
numbers on this sensor make it certain:

| Quantity | Reference rig | Reporter's rig (a1 = 1097 px/rad) |
|---|---|---|
| Polaris radius from the pole | ~14.5 px | 12.4 px |
| Predicted Polaris arc over the 35-min pole window | ~2.2 px | **1.9 px** |
| Accepted drift band `DRIFT_BAND × arc`, floored at `STATIC_NOISE_FLOOR_PX` | 1.5–5.5 px | **1.5–4.8 px** |
| Logged drift of the accepted "pole" track | — | 3.6 px |

A saturated orange blob's centroid jitter reads 3.6 px of extent over 12 frames, which sits
inside the band. The rotation-support test then passes it because the true pole is close:
an equatorial pier's head is *on* the polar axis, so a light on it is within the ±200 px
window where support cannot discriminate. The finder is structurally unable to tell a
static light from Polaris on this rig, and Polaris is hidden anyway.

Consequences:

- Issue item A's "tighten the admission tolerance to 3σ of the pole history" would lock the
  model to the LED. **Do not do it against the current finder's output.**
- Whether the saved model's pole (100 px from the LED) is right or wrong cannot be decided
  from the LED. The chance-level evidence in §0.2 is what condemns the model.
- The true pole must come from **the moving stars**, not from a stationary source. That is
  the user's suggestion and it is also the southern-hemisphere solution the pole-anchor
  plan deferred. Package 4 below is that work.

### 0.2 The saved model is a chance fit, and the code has no way to notice

From the issue and confirmed in code:

- 606 matches over 60 frames at a 16 px final tolerance ≈ 10/frame, which is ~1.0× the
  chance expectation on the Sep 23 sky; the RMS of 10.9 px is `tol/√2` — what uniform
  scatter inside the tolerance produces (`chance_matches.chance_median_residual`).
- `model_quality` (`calibration_quality.py:88-92`) has no chance or tolerance input, so this
  fit is rated **Good** for as long as it lives.
- `final_tol_px` and `chance_expected` exist today only as ad-hoc attributes set with
  `setattr` in `multi_calibrate.py:353,720`. They are not dataclass fields, so they never
  reach `allsky_calibration.json`, are dropped by `dataclasses.replace`, and a loaded model
  has neither.
- The incumbent is re-judged only by `incumbent_anchor_health`, which returns `None` on
  moonlit or obstructed buffers (`incumbent_evidence.py:108-147`) — so on Sep 23 the amber
  caution stayed silent.
- `model_replacement` rule 2 (escape + `evidence`) bypasses the RMS guard. `evidence` means
  only "a trusted pole existed this run or the incumbent had authority"
  (`model_admission.admission_evidence`) — nothing about the candidate's own merit. On Sep 19
  the trusted pole was the LED, so the bypass fired for a chance fit.

## 1. Findings

| # | Finding | Where | Package |
|---|---|---|---|
| H1 | `pole_finder` accepts a jittering static light as Polaris when the predicted arc is at the noise floor; the drift band is an extent measure with no time coherence | `pole_finder.py:294-319` | 4 |
| H2 | There is no pole estimate from the rotating field; the pole exists only if Polaris is detected | `pole_finder.py` | 4 |
| H3 | The measured pole is used only as a 140 px pass/fail gate and a mirror hint; never as a solve constraint. `_coarse_orientation_candidates` searches 2016 cells per mirror with no pole seed | `multi_calibrate.py:126-218`, `calibration_validate.py:460` | 5 |
| H4 | `PoleEstimate` carries no positional uncertainty; `PoleHistory.consensus` returns the latest run's position, not a cluster mean or scatter | `pole_finder.py:130-151`, `pole_consensus.py:223-288` | 4 |
| H5 | `model_quality` cannot distinguish a chance fit; `final_tol_px` / `chance_expected` are not persisted | `calibration_quality.py:71-94`, `fisheye.py:56-72` | 3 |
| H6 | The incumbent is never scored against chance on the live buffer | `calibration_workers.py:82-91` | 3 |
| H7 | Rule 2 bypass needs no candidate merit beyond the per-run gates it already passed | `model_replacement.py:137-142` | 3 |
| H8 | Only the ML roof classifier can say Closed; no external roof state can be read | `observing_window.py:115-137`, `ascom_safety.py` | 2 |
| H9 | `SkyMaskHistory` wipes itself after 15 misses and the renderer falls back to a raw grayscale `> 40` test that passes everything on a moonlit frame or nothing on a dark stretch | `label_stability.py:82-87`, `overlay_renderer.py:211-213` | 1 |
| H10 | No frame-content sanity gate: a 0.06 s exposure of a lit roof ceiling gets sky labels if the roof classifier says Open | `overlay_renderer.py:268-297` | 1 |
| H11 | `ui/panels/allsky_settings.py::get_config` rebuilds the `allsky_overlay` dict from scratch, so any new config key is wiped on the first panel edit | `allsky_settings.py:481-534`, `ui/main_window/settings.py:152-166` | 1 (fix), 2 (avoid) |
| H12 | The label stabilizer is a process-wide singleton never reset in production, shared by watch mode, camera mode and reprocesses | `label_stability.py:180-188` | 1 |
| H13 | `sample_images/` (the 130 real reference frames + `multi_calibration.json`) is gitignored and absent from CI containers; real-frame validation only runs on a developer machine | `.gitignore:75` | 4, 5 |

Also noted, out of scope for these packages: `.claude/rules/ml.md` says the inference path
is dev-gated and names `ui/controllers/ml_prediction.py` as the production interface; both
are wrong (`services/ml_service.py` runs in production whenever `ml_models.enabled`).
`docs/dev/ALLSKY_OVERLAY.md:117` still says the mask vote depth/hold is 3 (it is 15).

## 2. Work packages

Five packages, each a separate PR that merges on its own. Dependencies are listed; the
order below is the recommended merge order but only package 5 has a hard dependency.

| PR | Package | Size | Depends on |
|---|---|---|---|
| 1 | Overlay gate and sky-mask persistence (issue D + E) | S | — |
| 2 | External roof-state source (issue C) | M | — |
| 3 | Chance-aware quality and incumbent re-judging (issue B) | M | — |
| 4 | Pole from the rotating field, static-light rejection (issue A, measurement half) | L | — |
| 5 | Pole-constrained solve and admission (issue A, solver half) | L | 3, 4 |

Packages 3 and 4 both touch `calibration_workers.py` in a few lines; whichever merges
second rebases. Everything else is disjoint at the file level.

Every package: new logic goes in **new modules** (`.claude/rules/python-general.md`,
"Module design"). `multi_calibrate.py` (733) and `calibration_service.py` (736) are at the
750 hard cap; `calibration_validate.py` (555) and `overlay_renderer.py` (521) are past the
600 target. Additions to any of these are limited to wiring lines; anything algorithmic is
a new file. Each package updates the CLAUDE.md test table and the matching `docs/wiki/`
page with a `> **New in the next release** — not available in version 3.7.7 or earlier.`
note (the reporter is on 3.7.7; check `version.py` at PR time).

### Package 1 — Overlay gate and sky-mask persistence

**Goal.** Labels never appear on a frame that is not plausibly a night sky, and never
vanish wholesale because the sky mask died on a moonlit night.

**E — sanity gate** (new `services/allsky/overlay_gate.py`, pure):

- `frame_is_plausible_sky(metadata, config) -> (bool, reason)` evaluated in
  `render_allsky_for_preview` after the observing-window check, independent of the ML
  roof verdict.
- Rule 1, exposure floor: parse `metadata['EXPOSURE']` (camera mode writes `"30.0s"`,
  `zwo_capture_worker.py:206-240`; watch mode carries the sidecar's value, format varies —
  parse `s` / `ms` / bare seconds, mirror `ui/components/telemetry_bar.py:92-106` and move
  that parser into the new module so both use one). Exposure below
  `allsky_overlay.min_exposure_s` (default **1.0**, `0` disables) → no overlay. Video 2 was
  0.06 s; the reporter's clear nights were 10–30 s. Unparseable or absent exposure never
  blocks.
- Rule 2, static frame: `metadata['_ML_RESULTS']['frame_is_static']` true → no overlay
  (the frame is sensor noise; `frame_static_score` already computed it).
- Do **not** change the twilight threshold in `observing_window.py` (−6°). Astronomical
  night (−18°) would blank the overlay through the whole of nautical twilight and all
  summer at high latitudes; the −6° gate plus the exposure floor covers video 2.
- Log at INFO on each transition (gated → not gated and back), never per frame.
- Watch mode: `WatchControllerQt` (`watch_controller.py:77-88`) passes only `OUTPUT_CROP`
  to the renderer; forward `extras['metadata']` (already produced at `processor.py:246`)
  so the exposure is visible. `tests/test_watch_controller_crop.py` asserts the current
  shape — update it.

**D — mask persistence** (`label_stability.py`, `overlay_renderer.py`, new
`services/allsky/sky_region.py`):

- `SkyMaskHistory.update(None)` must **never reset to `None`** while it holds a vote. After
  `MASK_HOLD_FRAMES` misses the vote becomes *stale*: still returned, flagged
  (`is_stale` property), refreshed by the next real mask. A stale vote is discarded only on
  a shape change or after `MASK_STALE_MAX_FRAMES` (suggest 240 ≈ 2 h at 30 s) — the
  equipment silhouette is physical and does not move between frames.
- Remove the raw-grayscale fallback (`overlay_renderer.py:211-213`). With no vote at all,
  the visibility region is the model's own sky circle: `sky_region.model_sky_disc(model,
  w, h)` built from `a1 · (π/2) · (1 − SKY_TRIM_FRACTION)` (the inverse of
  `calibration_validate.a1_from_sky_radius`, see pole-anchor plan P7), translated for
  `OUTPUT_CROP` the same way the model is. Labels may then land on equipment for the first
  frames of a session; that is better than none.
- Sparse frames vote positively only: a frame with 3–9 detections (below the current
  `< 10 → None` floor at `overlay_renderer.py:72-73`) adds sky evidence where it has
  detections and abstains elsewhere, instead of counting as a miss. Implement as a separate
  `add_partial(mask)` on the history that increments the sky count without incrementing
  the frame count for uncovered pixels (the vote is already a running sum,
  `label_stability.py:95-113`). A frame with < 3 detections is a miss.
- Moon: exclude a disc around the Moon's projected pixel (`planets.get_all_positions`,
  radius ≈ 0.08·sky_r) from the *frame count* denominator, so glare that blanks detections
  near the Moon does not vote "not sky" there.
- H12: call `reset_label_stability()` from `CalibrationService.set_model` /
  `clear_model` and on capture start (`ui/main_window/capture.py`), so a new model or
  session starts clean. Reprocesses (`SAME_CAPTURE_KEY`) must not advance the vote twice.
- H11: `allsky_settings.get_config` must merge over the loaded dict instead of rebuilding
  it (it currently drops `utc_offset_hours`, `planets.colors`,
  `constellations.edge_fade_px`, and would drop `min_exposure_s`).
- Bright-star magnitude: leave the default (`bright_stars.max_magnitude` 3.0). Add a wiki
  line that mag 2.0 leaves about ten stars in the whole sky and is too few for
  `top_n` 15 on a moonlit night.

**Tests** (`tests/test_allsky_overlay_gate.py`, extend `test_allsky_label_stability.py`,
`test_allsky_rendering.py`): exposure parsing for every format; 0.06 s blocks, 10 s
passes, absent passes, `0` disables; static frame blocks; a 40-frame run of `None` masks
keeps the last vote and marks it stale; a real mask after a stale run replaces it; sparse
frames only add; Moon disc excluded from the denominator; no vote → model disc, not
grayscale; `get_config` round-trips every key in `DEFAULT_CONFIG['allsky_overlay']`;
watch-mode metadata forwarded. Existing `TestRendererStability` (`@pytest.mark.slow`)
must still pass.

**Wiki:** `All-Sky-Overlay.md` "When the overlay is drawn", "Stable labels from frame to
frame", Troubleshooting table; `docs/dev/ALLSKY_OVERLAY.md:117`.

### Package 2 — External roof-state source

**Goal.** A rig whose roof state is known to something else (NINA's safety monitor, the
observatory's own status file) can feed that state to Sentinel, overriding or replacing
the ML roof classifier, so every roof consumer follows the truth.

**Config** (`config_defaults.py`, `ml_models.roof_source`):

```
"roof_source": {
    "source": "ml",            # "ml" | "file"
    "file_path": "",
    "format": "auto",          # "auto" | "text" | "json"
    "json_key": "roof",        # dotted path for JSON, e.g. "observatory.roof.state"
    "open_values": ["OPEN", "open", "true", "1"],
    "closed_values": ["CLOSED", "closed", "false", "0"],
    "max_age_s": 600            # older file → state unknown (never "Open")
}
```

`"file"` works with `ml_models.enabled` **off** — that is the reporter's case if they
stop trusting the classifier. Every `ml_models.enabled` check that gates a roof consumer
(`observing_window.py:116`, `image_processor.py:349`, `window.py:682`) needs
"or roof_source is file" — put that in one helper, `roof_source.is_configured(config)`.

**Reader** (new `services/roof_state_source.py`, pure, no Qt):

- `read_roof_state(cfg, now) -> RoofReading(open: Optional[bool], confidence, source,
  age_s, error)`; `None` = unknown. Cache on `(path, mtime_ns, size)` like
  `overlay_renderer._load_model` / `watcher.py:121-126`; a read costs nothing when the
  file has not changed.
- Text format: the ASCOM writer's own output (`ascom_safety.py:129-145`,
  `Roof Status: OPEN|CLOSED`), and NINA's GenericFile safety-monitor conventions. Match
  the configured trigger strings case-insensitively on any line; first match wins.
- JSON format: `json_key` dotted path; value compared against `open_values` /
  `closed_values` after `str().strip()`; booleans and numbers accepted.
- Staleness: `mtime` older than `max_age_s` → unknown, with `error = "stale"`. Unknown
  never maps to Open.
- Loop guard: refuse (log WARNING once, state unknown) when `file_path` resolves to the
  same file as `ml_models.ascom_safety_file.file_path` while that writer is enabled —
  Sentinel would read its own verdict back (`docs/NINA_INTEGRATION_PLAN.md:144`).

**Injection.** The verdict must reach all three carriers the survey found
(`image_processor.py:349-377`): `metadata['ROOF_STATUS']` as `"Open (100%)"` /
`"Closed (100%)"` / `"N/A"`, `metadata['_ML_RESULTS']['roof_status'|'roof_confidence']`,
and `main_window.last_ml_results`. Confidence is **1.0** for a file reading — the ASCOM
FSM only writes SAFE above `min_confidence`. External state wins over ML when configured;
when the file is unknown/stale, fall back to ML if enabled, else `N/A`. Same injection in
`services/processor.py:197-211` for watch mode. Add `roof_source` to the results dict so
the status strip can show "Roof: Closed (file)".

**UI.** In `ui/panels/image_processing_ml.py` (the ML card, which owns its config I/O
today — keep to that pattern, do not add a controller for three widgets): source combo,
path + Browse, format combo, JSON key field, shown only when source = file. Tooltip warns
about pointing it at Sentinel's own safety file.

**Tests** (`tests/test_roof_state_source.py`, extend `test_observing_window.py`,
`test_image_processor.py`, `test_ascom_safety.py`): text and JSON parsing; unknown on
missing/garbled/stale; mtime cache; loop guard; file Closed suppresses sky features with
ML off; file Open with ML saying Closed → Open; stale file with ML on → ML verdict; ASCOM
FSM writes SAFE from a file Open; watch mode injection.

**Wiki:** `ML-Models.md` new section "Roof state from a file" under "Using the Results",
cross-linked from `NINA-Integration.md` and `All-Sky-Overlay.md` ("The roof must not be
reported closed"). Ask the reporter for a sample of the DVO JSON before finalising
`json_key` defaults (open question §5).

### Package 3 — Chance-aware quality and incumbent re-judging

**Goal.** A chance-level fit is never rated Good, the incumbent is re-scored against the
live buffer, and the escape bypass demands merit from the candidate.

**Persist the fit's own tolerance and chance ratio** (`fisheye.py`, `multi_calibrate.py`):

- Add dataclass fields `final_tol_px: float = 0.0` and `chance_ratio: float = 0.0` to
  `FisheyeModel` (legacy files load them as 0.0 = unknown; pattern in
  `tests/test_allsky_reliability.py:172-176`). Replace the `setattr` sites at
  `multi_calibrate.py:353,720` with field assignment (line-neutral). `chance_ratio =
  n_matches / max(expected, 1)`. Guided models: `final_tol_px` = the anchor limit used,
  `chance_ratio` = 0 (not applicable; `is_guided` already exempts them).

**Quality** (`calibration_quality.py`, new `calibration_fit_merit.py` for the rule):

- `fit_is_credible(model) -> (bool, reason)`: false when `final_tol_px > 0 and
  rms_residual > CREDIBLE_RMS_FRACTION × final_tol_px` (**0.6**; chance sits at 0.707,
  the #10 rig's real fits at 7.6–7.9 px on ~16 px = 0.48) or when `chance_ratio` is known
  and `< CHANCE_MARGIN` (2.0). Unknown (0.0) fields never fail a model — legacy files keep
  their rating until re-fitted.
- `model_quality` caps a non-credible model at **PRELIMINARY** regardless of n / n_images.
  The 606-star model rates Preliminary; the #10 rig's 4561-match, 7.8 px model stays
  Excellent. Badge tooltip carries the reason.

**Re-judge the incumbent** (new `services/allsky/incumbent_chance.py`; wiring in
`calibration_workers.py:82-91`):

- `score_incumbent(model, frames, tol_px) -> IncumbentScore(n_matches, expected, ratio,
  rms)` using `multi_calibrate._build_all_matches` at `18 × tol_scale(median_sky_r)` and
  `chance_matches.estimate_chance`. Runs in `_RefineWorker.run` next to
  `corroborate_incumbent` (off the GUI thread, same frames the candidate sees). Emit on
  `result_ready` / a new `incumbent_scored` signal.
- `CalibrationService` keeps a two-run streak; two consecutive chance-level scores
  (`ratio < CHANCE_MARGIN`) → `calibration_attention` level **`misaligned`** and the
  quality badge drops to Preliminary in the UI *without rewriting the file* (the file's
  rating is "from when it was saved" — the badge already says so), even when anchor health
  is `None`. Cleared by one credible score. Expose as `incumbent_chance_level: bool` on the
  attention call (`calibration_attention.py:42`).
- Extend rule 3 in `model_replacement.py`: `incumbent_discredited = failed_anchors or
  chance_level_twice` (guided incumbents still exempt).

**Tighten rule 2** (`model_replacement.py:137-142`): the evidence bypass also requires
`fit_is_credible(candidate)` — i.e. the candidate's *own* RMS/tolerance ratio and chance
ratio, not only that a pole or authority existed. A candidate that fails it goes through
the normal RMS guard. Update the docstring table at the top of the module.

**Status string** (`calibration_service.py:620-623,692-695,717-720`): when the incumbent
is chance-level, the restored status reads "Calibrated: 606 stars, RMS=10.9px
(preliminary — matches at chance level)".

**Tests** (`test_allsky_calibration_quality.py`, `test_allsky_model_replacement.py`, new
`test_allsky_incumbent_chance.py`, `test_calibration_service_workers.py`,
`test_allsky_quality_badge.py`): the reporter's JSON (values in the issue, `final_tol_px`
16, ratio 1.0) rates Preliminary; the #10 rig's numbers stay Excellent; legacy file with
0.0 fields unchanged; round-trip through `save`/`load`; rule 2 refuses a non-credible
candidate; incumbent scored on a synthetic buffer where its projections are random →
ratio ≈ 1; streak of two → `misaligned` even with anchor health `None`; one good score
clears it; badge and status text.

**Wiki:** `All-Sky-Overlay.md` "Quality levels" (new sentence: a fit whose matches are no
better than chance is capped at Preliminary), "When the badge turns amber" (new trigger),
"Why an automatic calibration may be rejected".

### Package 4 — Pole from the rotating field, static-light rejection

**Goal.** A trustworthy pole on rigs where Polaris is hidden, contaminated, or below the
horizon, with an uncertainty the solver and the gate can use; and static lights removed
from every pool that feeds the pole and the fit. This is the package that answers the
user's observation in §0.1.

**4a — Track coherence in `pole_finder`** (new `services/allsky/track_coherence.py`):

- For a stationary candidate's hits `(t, x, y)`, regress position on time. Report
  `progression` = length of the fitted displacement over the window and `scatter` = RMS
  residual about the line. Polaris progresses monotonically (progression ≈ predicted arc,
  scatter ≈ 0.3–0.5 px); a static light has progression ≈ 0 and scatter ≈ its jitter.
- Accept a candidate only when `progression ≥ COHERENCE_MIN_RATIO × scatter` (suggest 2.0)
  **and** progression is inside the drift band. The extent measure at
  `pole_finder.py:313-315` stays as a pre-filter.
- When the predicted arc over the available span is below `2 × STATIC_NOISE_FLOOR_PX`
  (3 px; true on the reporter's rig for any window under ~55 min), the finder withholds
  rather than decides: it needs a longer window before Polaris is separable from noise.
  The buffer holds 60 frames; use the widest span it has (`_MAX_SAMPLE_FRAMES` stays 12,
  sampled across the full span). Log the reason once per run.
- A withheld estimate is a `None` from `find_pole` — callers already treat that as normal.

**4b — Static-light pool** (new `services/allsky/static_lights.py`):

- `find_static_lights(frames, sky_r) -> list[(x, y)]`: detections present at the same
  pixel (± `CLUSTER_TOL`) in ≥ 3 frames spanning ≥ 45 min whose track fails the coherence
  test in 4a. The coherence test, not a raw ±2.5 px rule, is what keeps Polaris (2–3 px of
  real motion over 45 min on this rig) out of the static list.
- `strip_static(frames, lights) -> frames'` returns frame dicts with those detections
  removed. Applied in `_RefineWorker.run` before `find_pole` and before
  `refine_from_detections`, and in `_detect_frame`'s caller for the bootstrap. Static
  lights count against `n_det` in `chance_matches.frame_pool` today; removing them lowers
  the chance expectation honestly.
- Persist nothing; the list is recomputed per run (cheap: it is the same clustering
  `_stationary_candidates` already does).

**4c — Pole from rotation** (new `services/allsky/pole_from_rotation.py`):

The sky rotates about the pole at the sidereal rate. With the lens's radial function
known to first order (a1 from the sky circle via `a1_from_sky_radius`, or from the
incumbent when it passed the full gate set — never from a distrusted one, per pole-anchor
plan P7) and the optical centre from the sky circle, every detection maps to a unit
vector in camera coordinates. Between two frames Δt apart, real stars satisfy
`v₂ = R(axis, ω·Δt) · v₁`. The axis is the pole direction; its pixel is the projection of
that axis through the same radial function. This is the "near-pole-restricted flow fit"
the pole-anchor plan deferred, done in angle space so fisheye curvature does not bias it
the way the pixel-space rigid fit did (the plan measured 100+ px of bias there).

- Input: frame pairs ≥ 15 min apart from the buffer, static lights already stripped.
- Search: coarse grid over axis direction (alt 20–90° in 2°, az 0–360° in 2°, both
  mirrors) scoring each candidate by the number of frame-1 vectors that land within
  `tol` of a frame-2 vector after rotation (KD-tree, same pattern as
  `_coarse_orientation_candidates`); refine the best with `scipy.optimize` on the
  support-weighted residual; report the peak's width as `sigma_px`. Also scan a1 over
  0.8–1.3× the sky-circle estimate — the correct scale gives a sharper peak, and the
  peak's a1 is a free plate-scale measurement for package 5.
- Output: a `PoleEstimate` (add `sigma_px: float` and `source: 'polaris' | 'rotation'`
  fields; defaults keep existing constructors working) with `east_left` from the
  rotation sign, `n_frames`, `span_minutes`, window bounds.
- Requirements: ≥ 8 frames spanning ≥ 45 min, ≥ 40 stripped detections per frame in the
  median; below that, `None`.
- `find_pole` becomes the orchestrator: Polaris path first (4a); if it withholds, the
  rotation path; if both give an estimate they must agree within `3·sigma_px + 20 px` or
  the result is `None` with a WARNING naming both pixels (a disagreeing Polaris is the
  LED case; a disagreeing rotation fit is a contaminated field).
- `PoleHistory`: add `sigma_px` to the consensus (cluster scatter across independent
  windows, floored at the per-run sigma) and a `mean` position instead of "latest". The
  75 % dominant-mode rule and the vote ledger stay.

**4d — Buffer dump for replay** (new `services/allsky/buffer_dump.py`, small):

- On escape exhaustion (`EscapeBackoff.record_fruitless` reaching the threshold) and on
  every dev-mode escape, write the 60-frame buffer (`dt`, `detected`, sky circle, frame
  size — no images) to `<app-data>/allsky/buffer_<stamp>.json`, and include the newest one
  in the diagnostics bundle (`services/diagnostics_bundle.py`). This is what lets us
  replay packages 4 and 5 against the reporter's actual sky without shipping a build.
  Keep at most 5 files.

**Validation** — two layers because of H13:

- **CI, synthetic** (new `tests/allsky_synth.py` helper + `tests/test_pole_from_rotation.py`,
  `test_static_lights.py`, `test_track_coherence.py`): simulate a buffer from a known
  `FisheyeModel` (use the reference model's parameters from `ALLSKY_POLE_ANCHOR_PLAN.md`
  and the reporter's from the issue) and the real bright-star catalog at real sidereal
  times; add centroid jitter (σ 0.4 px), a static LED with σ 1.2 px jitter 100 px from the
  pole, and **remove Polaris**. Acceptance: rotation pole within 25 px of the true pole on
  both rigs; Polaris path returns `None` (not the LED) on the reporter's rig; with Polaris
  restored both paths agree; a field of 3 static lights and one slewing light yields the
  same answer; `sigma_px` covers the true error in ≥ 90 % of 20 seeded runs.
- **Developer machine, real** (extend `scripts/dev/allsky/validate_calibration.py` or add
  `pole_replay.py`): run on `sample_images/` (130 frames, Polaris truth (1718, 646)) with
  Polaris detections masked out; report the error per rolling window. Also run on the
  reporter's buffer dump when 4d has produced one. Record the numbers in this document
  under a "Field results" heading before package 5 relies on `sigma_px`.

**Tests to keep green:** `test_allsky_pole_finder.py`, `test_allsky_pole_consensus.py`,
`test_allsky_calibration_validate.py`, `test_calibration_service_workers.py`,
`test_allsky_reliability.py`, `test_allsky_anchor_gate_real.py` (skips without data).

**Wiki:** `All-Sky-Overlay.md` "Why an automatic calibration may be rejected" — the pole
bullet: no longer "Polaris stays still", now "the stars circle a point; PFR Sentinel finds
that point from their motion, and from Polaris when it is visible", works in either
hemisphere, and lights on the pier are ignored.

### Package 5 — Pole-constrained solve and admission

**Goal.** The pole pixel (with uncertainty) is a constraint in the fit and a calibrated
admission tolerance, not a flat 140 px gate; and a solve with a known pole searches roll
only. Depends on package 3 (`final_tol_px`, `chance_ratio` fields) and package 4
(`sigma_px`, `source`).

**5a — Extraction first** (behaviour-neutral commit, re-exports kept):
`_coarse_orientation_candidates` → `services/allsky/orientation_search.py`;
`_joint_iterative_fit` + `_build_all_matches` + `_joint_rms` →
`services/allsky/joint_fit.py`. `multi_calibrate.py` drops to ~350 lines and can take
the wiring below. Run the full all-sky test set before and after; identical results.

**5b — Pole-seeded orientation search** (`orientation_search.py`):

- With a trusted pole (consensus not `None`), the axis direction in camera coordinates
  is known: invert the radial function at the pole pixel to get the pole's θ, φ; the
  model's `(axis_alt, axis_az)` follow from requiring `altaz_to_pixel(|lat|, 0|180)` to
  land there. The search is then **roll × mirror** (24 × 2 cells, or 24 × 1 with the
  consensus `east_left`) instead of 2016 × 2, and each cell can afford the full 60-frame
  scoring instead of 8 sampled frames. Keep the old full grid as the no-pole path.
- Seed `a1` from the rotation fit's plate scale when `source == 'rotation'`.

**5c — Pole pseudo-observation in the joint fit** (`joint_fit.py`, `residuals(p)` at
today's `multi_calibrate.py:633-660`): append
`w · (altaz_to_pixel(|lat|, 0|180) − (pole.x, pole.y)) / sigma_px` with
`w = POLE_WEIGHT · √N` (same scaling as the ridge prior), only when a trusted pole exists.
The fit cannot then walk away from the pole during the tolerance schedule; a solve that
wants to sit 100 px off a 5 px pole pays for it.

**5d — Calibrated admission tolerance** (`calibration_validate.py`, `model_admission.py`):

- `validate_pole` returns the distance and gets a tolerance argument. Tolerance =
  `max(3 · sigma_px + POLE_TOL_MODEL_ERROR_PX, POLE_TOL_FLOOR_PX)` scaled, where the
  model-error allowance (suggest 30 px reference; the known-good reference model projects
  the NCP 50–70 px from Polaris because of regional lens error — re-measure after 5c,
  which should shrink it) and the floor (40 px) are constants with the measurement behind
  them in the docstring. `POLE_TOL_REF_PX = 140` remains the value used when `sigma_px` is
  unknown (a `PoleEstimate` from before package 4, or a Polaris-only estimate whose
  `sigma_px` is not yet trusted).
- The model-vs-model basin veto in `model_admission._basin_veto` keeps 140 px; it compares
  two models, not a measurement.

**5e — Rival test on escape** (`bootstrap_selection.py`): after `select_bootstrap_winner`,
if a second survivor whose matched star set (`matched_stars` names) overlaps the winner's
by < 50 % has `chance_excess ≥ 0.5 ×` the winner's, the escape yields **no** candidate
("two incompatible solutions explain the frames"). Logged with both orientations.

**Not in this package** (listed in the issue as options; each is its own follow-up if the
field results say the solver still fails): roll voting by radius pairs, bootstrapping from
three frames hours apart, resolution-scaled RMS tiers, nightly self-check, detector
rejection of edge-adjacent/elongated components, other lens families.

**Tests:** `test_allsky_multi_calibrate.py` (extraction neutrality: same outputs on the
existing fixtures), new `test_orientation_search.py` (pole-seeded path finds the true
roll on the synthetic buffer from package 4 in ≤ 48 fits; no-pole path unchanged),
`test_joint_fit.py` (pseudo-observation pulls a 100 px-off seed to within `3σ`; with no
pole the residual vector is identical to before), `test_allsky_calibration_validate.py`
(tolerance from sigma; floor; unknown sigma → 140), `test_allsky_model_admission.py`,
`test_allsky_multi_calibrate.py` rival test. Real-data run on `sample_images/` and the
reporter's buffer dump, numbers recorded here.

**Wiki:** `All-Sky-Overlay.md` "Automatic calibration" and the pole bullet.

## 3. Agent briefs

Each package should be run by one coding agent on its own branch from `main`, with this
document, the issue text, `ALLSKY_POLE_ANCHOR_PLAN.md` and the relevant `.claude/rules/`
files as required reading. The brief for each is the package section above plus:

- Read `.claude/rules/allsky.md`, `python-general.md`, `services.md`, `tests.md` before
  the first edit; `ui-panels.md` for packages 1 and 2.
- Use the code-review-graph MCP tools before grepping (CLAUDE.md).
- New algorithmic code goes in the new modules named above; the at-cap files take wiring
  only. The size hook blocks anything else.
- Every constant introduced carries the measurement or reasoning behind it in a comment,
  in the house style of `pole_finder.py:60-125`.
- Run `pytest -m "not requires_camera and not requires_network and not requires_ml_models"`
  and `ruff check .` before every push; run `python scripts/ci/check_file_sizes.py`.
- Update the CLAUDE.md test table and the wiki page in the same PR.
- Do not open the PR against `main` until the reviewer (`pfr-reviewer` subagent, then
  the human) has seen the diff. Packages 4 and 5 additionally need the developer-machine
  validation numbers pasted into the PR description.

Review checklist for the coordinator, per PR: threading (nothing new on the GUI thread
heavier than today's `find_pole` ≈ 35 ms; the incumbent scoring and rotation fit belong
in `_RefineWorker`), file caps, no behaviour change in the extraction commit of package 5,
every new config key survives `allsky_settings.get_config`, wiki note present, test table
updated, `requires_windows` / `slow` markers where appropriate.

## 4. Remediation for the reporter, now

Unchanged from the issue: install the dev build and run **Guided Calibration**, which
stamps `provenance = 'guided'` and locks the basin against automatic replacement. Add,
once package 4d ships in a dev build: leave capture running for a clear night so the
buffer dump exists, then send a diagnostics bundle. Until package 2 ships, turn off
**Skip Sky Features When Roof Closed** is *not* the answer (it would draw on the closed
roof more, not less); the exposure floor in package 1 is.

## 5. Open questions for the maintainer

1. Package split: five PRs as above, or six with package 4 divided into measurement (4a–4c)
   and buffer dump (4d)? 4d is small and unblocks field validation early; it could also
   ride in package 3.
2. `min_exposure_s` default 1.0 s: is there a supported rig that shows stars below 1 s at
   its normal gain? If so, lower the default and rely on the static-frame rule.
3. Package 2: a sample of the DVO roof JSON from the reporter, to fix the default
   `json_key` and value lists. And whether NINA on that rig writes its safety-monitor
   state to a file at all (the GenericFile monitor reads, it does not write), which
   decides whether the wiki should describe a NINA-side writer.
4. Package 3 `CREDIBLE_RMS_FRACTION = 0.6`: confirm against the last month of refinement
   logs on the reference rig that no admitted model sits above 0.6 (the #10 rig's were
   0.48).
5. Package 4: the hemisphere the rotation fit is validated for. The synthetic fixture can
   simulate a southern site; the only real data is northern. Ship for both with the
   southern path marked as validated on synthetic data only, or gate it to `lat ≥ 20`
   like today's finder until a southern user reports?
