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

## 0. What changes the issue's proposals

### 0.1 Polaris is not visible on this rig

The reporter's videos show the pier and mount covering the region where Polaris would
be; the "Polaris" label sits beside the lights on that hardware on every clear night. So
the pole `pole_finder` reports at (1409, 2699) "on every run across Sep 17, 18 and 19" is
**not Polaris**. It is one of those lights (which one cannot be told from a still, since
the crop offset is unknown; it does not matter). The mechanism is the documented residual risk in
`ALLSKY_POLE_ANCHOR_PLAN.md` ("a bright in-band light within ~200 px of a *hidden* pole can
still clear the floor; a stable single contaminant is unimodal by definition"), and the
numbers on this sensor make it certain:

| Quantity | Reference rig | Reporter's rig (a1 = 1097 px/rad) |
|---|---|---|
| Polaris radius from the pole | ~14.5 px | 12.4 px |
| Predicted Polaris arc over the 35-min pole window | ~2.2 px | **1.9 px** |
| Accepted drift band `DRIFT_BAND × arc`, floored at `STATIC_NOISE_FLOOR_PX` | 1.5–5.5 px | **1.5–4.8 px** |
| Logged drift of the accepted "pole" track | — | 3.6 px |

A saturated light's centroid jitter reads 3.6 px of extent over 12 frames, which sits
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

### 0.3 What a frame from this rig shows (2026-09-22 23:30 local, 20.66 s, gain 180)

- **Roughly half the sky disc is equipment.** A ring of telescopes and mounts occupies the
  outer 30–45 % of the radius on every side, with gaps. Consequences: `measure_sky_circle`
  scans to the equipment silhouettes, not the horizon, so `a1_from_sky_radius` and the
  optical centre it seeds are unreliable here (the pole-anchor plan saw the same on a
  less obstructed rig: r ≈ 1250 vs 1563 true). Package 4c must therefore solve centre and
  scale jointly with the axis, seeded from the circle, not take them from it. The
  bright-anchor gate also finds many of its top-12 stars behind scopes, which is why
  anchor health reads `None` so often on this rig.
- **The pole region is covered.** The model puts Polaris and Kochab at the bottom of the
  crop, on the mount and pier hardware that carries several lights, one orange pair and
  at least one white. No star near the pole is ever detected; the pole can only come from
  the motion of stars far from it, where lens error matters most. That is the accuracy
  limit for package 4c and the reason its `sigma_px` must be honest rather than
  optimistic.
- **Many static lights, not one.** White and orange lights on mounts around the whole
  ring, plus a bright white rectangular object near the Alderamin label. Package 4b's
  static-light stripping is a pool-wide operation on this rig, not a single-LED fix.
- **The Moon's glare disc is large.** At 20 s the Moon is a saturated blob with a halo
  about 0.12–0.15 of the sky radius across; the label beside it is washed out. Package 1's
  Moon exclusion radius should be derived from the saturated region (or default to
  0.15·sky_r), not the 0.08 first suggested below.
- Exposure and gain on the clear nights are 10–30 s / 180; the 0.06 s of video 2 is two
  orders of magnitude away. The 0.5 s floor in package 2 has a wide margin on both sides
  for this rig.

### 0.4 What `sentinel.log.2026-09-23` shows

- **Every refinement that day was at chance**: 62 consecutive rejections at 1.0–1.5×
  (e.g. "644 matches vs 618 expected by chance at tol=16.5px over 60 frames"), and all 24
  bootstrap candidates across four escapes likewise (0.93–1.49×). The joint fit converges
  on *anything* to RMS ≈ 11.5 px at 16 px tolerance — chance's `tol/√2`. The incumbent,
  with the same arithmetic, was never scored.
- **The pole was "found" only in the pre-dawn window.** Withheld at 00:07–03:14 (best
  support 1.3–1.9× at scattered positions), found at 03:46, 04:17, 04:50 and 05:21 at
  (1409, 2699) ± 1 px with support 2.7–3.9×, withheld again at 05:52 at the same pixel at
  2.49× (just under the 2.5 floor), withheld all evening. Same pixel as Sep 17–19. The
  candidate is stationary and bright enough to be in-band at every hour; it clears the
  support floor only when the 30 s max-exposure frames give it the most votes. That is a
  light near the hidden pole, not Polaris, and the floor is doing what it can.
- **The roof classifier read Open all night** (0–6 h and 18–24 h, 729 + 127 predictions),
  Closed once at 06:39 at 99 %. There is no closed-roof night in this log; video 2 (Sep 21)
  is not covered. The evening ramp shows what a closed lit roof looks like to the exposure
  loop: 0.21 ms at 18:38, 8 ms at 19:00, then 12–17 s once dark.
- **The 60-frame buffer spanned 13.7 h across the day gap.** The 19:08 and 19:43 escapes
  fitted "60 frames, 824.9 min span": frames from 05:xx–06:xx (dawn, 847 ms) survived the
  daytime suppression and were fitted together with the evening's. The buffer is FIFO by
  count only (`calibration_service.py:273-275`). Not the cause of anything here, but a
  pool that mixes two nights' conditions makes every chance estimate and the rotation fit
  noisier than it need be.
- **Meteor hot mask sits at ~40 % of the frame** — an independent measure of how much of
  the disc is equipment and glare on this rig.

### 0.5 The maintainer's reference rig has the same solver failure, protected by a guided model

Read from the reference rig's own logs (Sep 17, 18, 20; Sep 19 was gated out all night
and Sep 22–24 had no capture). The saved model is the guided calibration of 2026-09-15
(7 anchors, RMS 4.10 px, a1 = 1259, `provenance = 'guided'`), the labels are right, and
the file has not been written since. Underneath that:

- **35 automatic refinements, 35 rejections, none genuine.** Every fit seeded from the
  good guided model walked away from it: final RMS 7.7–9.9 px on a 12–13 px tolerance
  (0.64–0.83 of the tolerance — the chance band), with the bright anchors hundreds of
  pixels off (Vega 524 px, Altair 347 px, Deneb 275 px). Sep 17–18 failed the anchor
  gate (0/3 frames, 2–3 of 12 anchors within 26 px); Sep 20 failed the chance gate
  outright (1.0–1.5×). Refinement from a correct seed cannot hold the basin on this rig,
  so it is not only the reporter's rig where the joint fit fails — theirs is merely
  unprotected.
- **Every basin escape started at the wrong scale.** All six bootstrap candidates per
  escape carried a1 = 783 (0.62× the true 1259): `a1_from_sky_radius` on this obstructed
  aperture, exactly as pole-anchor plan P7 recorded in July. The guided lock caught the
  one candidate that survived the other gates ("a1 = 787 is 0.84× the guided model's
  plate scale"). Package 5's scale scan and package 4c's solved a1 are not optional.
- **The escape buffer spanned 1069 minutes** across the day gap (H14, confirmed on a
  second rig).
- **The pole finder works here.** Polaris is visible: the estimate sits at
  (1367–1376, 456–466), walking 12 px around the pole over six hours as Polaris does,
  support 2.7–3.5×, `east_left = True`. This is what a genuine pole track looks like and
  is the positive control for package 4a's coherence test.
- The final tolerance on this rig is 12 px, not the 16 px of the reporter's, because the
  measured sky circle is smaller (obstructed): the same `tol_scale` mechanism, the same
  P7 finding.

Consequence for validation: **the reference rig is the first place package 0 runs.** A
buffer dump from it, judged against its guided model, is ground truth for packages 4 and
5 — a solver that cannot re-find this rig's guided model from its own frames is not
fixed. The July field test already showed `sample_images/` no longer describes this
camera's orientation.

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
| H8 | The roof verdict is the only content signal that can suppress the overlay; a frame with no stars at all (lit roof, overcast) still gets labels when the classifier says Open. Star analysis (`analyze_stars`) runs only *after* the gate, so it cannot inform it | `observing_window.py:115-137`, `star_detection.py:154-156` | 2 |
| H9 | `SkyMaskHistory` wipes itself after 15 misses and the renderer falls back to a raw grayscale `> 40` test that passes everything on a moonlit frame or nothing on a dark stretch | `label_stability.py:82-87`, `overlay_renderer.py:211-213` | 1 |
| H9b | No equipment map is persisted: the 15-frame vote is the only obstruction knowledge, it starts from nothing every session, and calibration's detection pool (`_detect_frame`) never uses it — glints and lights on the equipment feed the fit and the pole finder | `label_stability.py:180`, `calibration_service.py:359-402` | 1, 4 |
| H10 | No frame-content sanity gate: a 0.06 s exposure of a lit roof ceiling gets sky labels if the roof classifier says Open | `overlay_renderer.py:268-297` | 1 |
| H11 | `ui/panels/allsky_settings.py::get_config` rebuilds the `allsky_overlay` dict from scratch, so any new config key is wiped on the first panel edit | `allsky_settings.py:481-534`, `ui/main_window/settings.py:152-166` | 1 (fix), 2 (avoid) |
| H12 | The label stabilizer is a process-wide singleton never reset in production, shared by watch mode, camera mode and reprocesses | `label_stability.py:180-188` | 1 |
| H14 | The calibration buffer is FIFO by count with no age limit; after a day gap it fits dawn frames with evening frames (824 min span on the reporter's rig, 1069 min on the reference rig) | `calibration_service.py:273-275` | 4 |
| H13 | `sample_images/` (the 130 real reference frames + `multi_calibration.json`) is gitignored and absent from CI containers; real-frame validation only runs on a developer machine | `.gitignore:75` | 4, 5 |

Also noted, out of scope for these packages: `.claude/rules/ml.md` says the inference path
is dev-gated and names `ui/controllers/ml_prediction.py` as the production interface; both
are wrong (`services/ml_service.py` runs in production whenever `ml_models.enabled`).
`docs/dev/ALLSKY_OVERLAY.md:117` still says the mask vote depth/hold is 3 (it is 15).

## 2. Work packages

Six packages, each a separate PR that merges on its own. Dependencies are listed; the
order below is the recommended merge order but only package 5 has a hard dependency.

| PR | Package | Size | Depends on |
|---|---|---|---|
| 0 | Buffer dump for replay (ships first, in a dev build) | S | — |
| 1 | Equipment map and label persistence (issue D, user's "healing mask") | M | — |
| 2 | Observable-sky gate: roof verdict corroborated by star evidence (issue C + E) | M | — |
| 3 | Chance-aware quality and incumbent re-judging (issue B) | M | — |
| 4 | Pole from the rotating field, static-light rejection (issue A, measurement half) | L | — |
| 5 | Pole-constrained solve and admission (issue A, solver half) | L | 3, 4 |

Packages 3 and 4 both touch `calibration_workers.py` in a few lines; whichever merges
second rebases. Package 4 reads the equipment map from package 1 through one function
that accepts `None`, so it builds and tests without it. Everything else is disjoint at
the file level.

Every package: new logic goes in **new modules** (`.claude/rules/python-general.md`,
"Module design"). `multi_calibrate.py` (733) and `calibration_service.py` (736) are at the
750 hard cap; `calibration_validate.py` (555) and `overlay_renderer.py` (521) are past the
600 target. Additions to any of these are limited to wiring lines; anything algorithmic is
a new file. Each package updates the CLAUDE.md test table and the matching `docs/wiki/`
page with a `> **New in the next release** — not available in version 3.7.7 or earlier.`
note (the reporter is on 3.7.7; check `version.py` at PR time).

### Package 0 — Buffer dump for replay

**Goal.** Make the reporter's sky replayable here before packages 4 and 5 are written.
Ships first in a dev build; the reporter runs one clear night and sends a diagnostics
bundle.

- New `services/allsky/buffer_dump.py`: `dump_buffer(frames, path)` writes the
  `CalibrationService` buffer (and, once package 4 adds it, the long-baseline ring) (`dt`, `detected`, `sky_cx/cy/r`, `image_width/height`,
  `above_horizon` names only — no images) as JSON to
  `<app-data>/allsky/buffer_<stamp>.json`; keeps the newest 5 files. Path via a new
  `app_config.get_allsky_buffer_dir()`.
- Triggers: escape exhaustion (`EscapeBackoff.record_fruitless` reaching the threshold),
  every basin escape when dev mode is on, and a **Dump calibration buffer** button on the
  All-Sky settings card (so the reporter can do it on demand without waiting for an
  escape).
- `services/diagnostics_bundle.py` includes the newest dump under `allsky/`.
- `scripts/dev/allsky/replay_buffer.py`: loads a dump and runs `find_pole`,
  `refine_from_detections` and `estimate_chance` against it, printing what the service
  would have logged. This is the harness packages 4 and 5 validate on.
- `scripts/dev/allsky/library_to_buffer.py` (§7.2): an image-library night → the same
  dump format, so the replay harness has real frames before any dev build ships.
- Tests: `tests/test_allsky_buffer_dump.py` — round-trip, cap of 5, no image data, bundle
  inclusion (extend `test_diagnostics_bundle.py`); the library loader on a two-frame
  synthetic library folder.
- Wiki: `Diagnostics-Export.md` (new file listed), `All-Sky-Overlay.md` (the button).

### Package 1 — Equipment map and label persistence

**Goal.** Sentinel learns where the equipment is, keeps that knowledge across sessions and
nights, heals it slowly as scopes move, and uses it for two things: labels are never placed
on equipment, and (package 4) detections on equipment never enter the calibration pool.
Labels never vanish wholesale because one night's sky mask died.

**Equipment map** (new `services/allsky/obstruction_map.py`, pure numpy + JSON/NPZ I/O):

- `ObstructionMap`: a float32 "sky probability" plane at reduced resolution (longest edge
  `MASK_VOTE_MAX_EDGE` = 512, as the vote already is), stamped with the frame size and
  `OUTPUT_CROP` it was built for. Values: 0 = equipment, 1 = sky, 0.5 = unknown (initial).
- `update(sky_mask, frame_is_observable)`: an exponential moving average per pixel with
  time constant `HEAL_FRAMES` (suggest 600 frames ≈ 5 h of 30 s frames, i.e. a moved
  scope is forgotten over about two nights). Update **only** on frames the observable-sky
  gate (package 2) passed and whose per-frame mask had ≥ 10 detections — a cloudy or
  moonlit frame with no detections must not teach the map that the sky is equipment.
  Positive evidence (a detection disc) pulls toward 1; a pixel inside the sky circle with
  no detection within `2 × disc radius` on a frame that had ≥ 40 detections elsewhere
  pulls toward 0. The Moon's glare disc (radius from the saturated blob, default
  0.15·sky_r; see §0.3) is excluded from the negative update.
- `sky(threshold=0.5) -> bool mask` and `is_sky(x, y)` at full resolution (nearest
  neighbour upsample), used by the renderer and by package 4.
- Persistence: `<app-data>/allsky_obstruction.npz` via a `save()` no more than once per
  10 min and on capture stop; `load()` on `CalibrationService` start; discarded (not
  rescaled) when the stored frame size or crop differs, and cleared by **Reset
  Calibration** and by a new `Reset equipment map` button next to it. Resolve the path
  through `services.app_config` (add `get_obstruction_map_path()`), never a literal path.
- The map is a *prior*, the per-night vote is the *evidence*: in the renderer the
  visibility plane is `vote if the vote is fresh else map`; when both exist, a pixel must
  be sky in both.

**Label persistence** (`label_stability.py`, `overlay_renderer.py`):

- `SkyMaskHistory.update(None)` must **never reset to `None`** while it holds a vote. After
  `MASK_HOLD_FRAMES` misses the vote becomes *stale* (`is_stale` property) and the map
  takes over; a fresh mask replaces it. Shape change still resets.
- Remove the raw-grayscale fallback (`overlay_renderer.py:211-213`). With neither a vote
  nor a map, the visibility region is the model's own sky disc: `a1 · (π/2) · (1 −
  SKY_TRIM_FRACTION)` (the inverse of `calibration_validate.a1_from_sky_radius`, pole-anchor
  plan P7), translated for `OUTPUT_CROP` like the model. Labels may then sit on equipment
  for the first frames of a first session; better than none, and the map fixes it within
  the hour.
- Sparse frames vote positively only: 3–9 detections (below today's `< 10 → None` floor at
  `overlay_renderer.py:72-73`) add sky evidence where they have detections and abstain
  elsewhere, via a new `add_partial(mask)` that increments the sky count without the frame
  count for uncovered pixels (the vote is a running sum, `label_stability.py:95-113`).
  Under 3 detections is a miss.
- Moon: exclude the glare disc from the frame-count denominator (same radius rule as
  above) so glare that blanks detections near the Moon does not vote "not sky" there.
- H12: `reset_label_stability()` on `CalibrationService.set_model` / `clear_model` and on
  capture start (`ui/main_window/capture.py`); reprocesses (`SAME_CAPTURE_KEY`) must not
  advance the vote twice.
- H11: `allsky_settings.get_config` merges over the loaded dict instead of rebuilding it
  (it drops `utc_offset_hours`, `planets.colors`, `constellations.edge_fade_px` today and
  would drop any new key).
- Bright-star magnitude: leave the default (`bright_stars.max_magnitude` 3.0). Add a wiki
  line that mag 2.0 leaves about ten stars in the whole sky and is too few for `top_n` 15
  on a moonlit night.

**Tests** (new `tests/test_obstruction_map.py`; extend `test_allsky_label_stability.py`,
`test_allsky_rendering.py`, `test_allsky_settings_config.py`): map converges to the
synthetic equipment silhouette within `HEAL_FRAMES` and forgets a moved scope in ~2×; a
run of no-detection frames leaves the map untouched; Moon disc excluded; round-trip
through `save`/`load` with size/crop stamps, mismatch discards; 40 `None` masks keep the
vote and mark it stale; a real mask after a stale run replaces it; sparse frames only add;
no vote and no map → model disc, never grayscale; labels never placed where the map says
equipment; `get_config` round-trips every key in `DEFAULT_CONFIG['allsky_overlay']`.
`TestRendererStability` (`@pytest.mark.slow`) stays green.

**Wiki:** `All-Sky-Overlay.md` "Equipment avoidance" (new: the map, what heals it, the
reset button), "Stable labels from frame to frame", Troubleshooting;
`docs/dev/ALLSKY_OVERLAY.md:117`.

### Package 2 — Observable-sky gate: roof verdict corroborated by star evidence

**Goal.** Sky features (overlay, calibration feed, star analysis) run only when the frame
is plausibly a night sky with stars in it. The ML roof classifier stays the roof source —
no external file — but its verdict is corroborated by what the frame contains, so a
misread Open on a lit roof, or an overcast sky, no longer draws labels. This absorbs the
issue's E (exposure floor) and replaces its C.

**Frame evidence** (new `services/sky_evidence.py`, pure; computed once per frame in
`image_processor._process_task` *before* the gate, on the same stretched frame the
calibration feed uses, and stored as `metadata['_SKY_EVIDENCE']`):

- `star_count`: `star_centroid.detect_stars` inside the sky circle (the same call
  `_detect_frame` and `_detect_sky_mask` make today — share the result through the
  metadata so the frame is scanned once, not three times), with the equipment map from
  package 1 applied when present (detections on equipment are not stars).
- `exposure_s`: parsed from `metadata['EXPOSURE']` (camera mode `"30.0s"`; watch mode
  the sidecar's string — parse `s` / `ms` / bare seconds; move
  `ui/components/telemetry_bar._fmt_exposure`'s parser into the new module so both use
  one). Absent or unparseable → `None`, never blocks.
- From ML when present: `roof_status`, `roof_confidence`, `stars_visible`,
  `frame_is_static` (`ml_service.py:222-232`).

**Decision** (`observing_window._evaluate`, after the −6° twilight gate, which stays):

1. `frame_is_static` → not observable ("sensor noise only"). Covers a dark closed roof.
2. `exposure_s < allsky_overlay.min_exposure_s` (default **0.5**; `0` disables; a plain
   key in `config.json` so it can be edited there as well as on the card) → not
   observable. Covers the lit roof at 0.06 s (video 2) and dusk (8 ms at 19:00 in the log).
   The clear nights ran 10–30 s.
3. ML roof `Closed` on two consecutive frames → not observable (unchanged).
4. ML roof `Open` (or ML off) but **no star evidence** on `NO_STARS_CONFIRM_FRAMES`
   consecutive frames (suggest 3): `star_count < min_star_detections` (default 15; the
   ceiling texture reached ≥ 10 on video 2, real skies here give 40–200) and
   `stars_visible` is not `True` → not observable ("roof reads Open but no stars are
   detected"). Covers the lit roof *and* overcast — labels on cloud are as wrong as labels
   on a ceiling, and the calibration feed should not see either.
5. Recovery: `star_count ≥ 2 × min_star_detections` on two consecutive frames → observable
   again. Hysteresis avoids flicker on the threshold.

Rule 4 is deliberately model-free. The bright-anchor check (`validate_bright_anchors`,
"the function that finds the bright stars") would be the sharper test, but it needs a
credible model, and on this rig the model is at chance (§0.2); until package 3 marks the
model credible it would suppress the overlay on every open night. When
`fit_is_credible(model)` is true, a later step can add anchor hits as a third corroborant.

What the gate governs is unchanged: overlay, calibration feed, star analysis. The meteor
gate, Discord roof alerts, timelapse roof mode and the ASCOM safety file keep following
the roof verdict alone — "no stars" is not "roof closed", and the safety file's meaning
must not drift.

**Reporting.** `metadata['_observing_window_reason']` carries the rule that fired; the
status strip's Sky tile shows "No stars detected" / "Exposure too short" the way it shows
"Roof closed" today (`ui/components/status_strip.py:324-364`); one INFO line per
transition, never per frame.

**Watch mode.** `WatchControllerQt` (`watch_controller.py:77-88`) passes only
`OUTPUT_CROP` to the renderer; forward `extras['metadata']` (built at `processor.py:246`)
so exposure and star count reach the gate. `tests/test_watch_controller_crop.py` asserts
the current shape — update it.

**Config** (`config_defaults.py`, `allsky_overlay`): `min_exposure_s: 0.5`,
`min_star_detections: 15`. Exposed on the All-Sky settings card (two spin boxes under
"When the overlay is drawn"); package 1's `get_config` fix must land first or these keys
are wiped on the first panel edit (H11) — if package 2 merges first, include the fix here.

Two real fixtures exist. §7.4's 500 frames carry the observatory's own roof state from
NINA, exposure and cloud cover — the calibration set for `min_star_detections` and the
confirm-frame counts. And §7.1's Sep 17 23:59 frame, roof Closed at
100 % **at a 13 s exposure** — the exposure floor does not fire, the roof verdict and
the no-stars rule must. Package 2 is not done until that frame is blanked by rule 4 with
the roof verdict removed from its metadata.

**Tests** (new `tests/test_sky_evidence.py`; extend `test_observing_window.py`,
`test_status_strip_static.py`, `test_image_processor.py`): exposure parsing for every
format; 0.06 s blocks, 10 s passes, absent passes, `0` disables; static blocks; Open + 3
no-star frames blocks, 2 does not; recovery needs 2 frames at 2×; a `Closed` streak
still blocks with plenty of detections (the classifier's Closed is not overridden — see
open question 3); ML off with no stars blocks; reason string and Sky tile text; the frame
is scanned once (spy on `detect_stars`); meteor and ASCOM paths unaffected by rule 4.

**Wiki:** `All-Sky-Overlay.md` "When the overlay is drawn" (rewrite: sun, roof, static,
exposure, stars), `ML-Models.md` "Skipping Sky Features While the Roof Is Closed",
`Live-Monitoring.md` Sky tile texts.

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
- Skip the score (report `None`, not a strike) when the buffer's median detection count
  is below `SCORE_MIN_DETECTIONS` (40): a cloudy or moon-washed buffer says nothing about
  the model.
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

**4b — Pool hygiene: static lights, obstruction, detector filters, frame ring**
(new `services/allsky/static_lights.py` and the modules named below):

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
- Equipment map (package 1): `strip_obstructed(frames, obstruction_map)` drops detections
  where the map says equipment, applied at the same point. Takes `None` and returns the
  frames unchanged, so this package builds without package 1; lights on parked scopes
  then never reach the pole finder, the fit, or the chance estimate.
- Buffer hygiene (H14): `feed_frame` keeps two sets — the 60-frame rolling buffer as
  today, plus a **long-baseline ring** (new `services/allsky/frame_ring.py`): one frame
  per `LONG_RING_SPACING_MIN` (10), up to `LONG_RING_LEN` (36, i.e. six hours), same night
  only — a frame more than `NIGHT_GAP_HOURS` (6) after the previous entry starts a new
  ring. The rotation-pole fit (4c) and the bootstrap search (package 5) take the ring;
  refinement keeps the rolling buffer. Package 0's dump grows to include the ring.
- Detector filters (new `services/allsky/detection_filters.py`, applied in
  `_detect_frame` and `_detect_sky_mask` after `detect_stars`; also takes
  `ignore_rects` for burned-in text boxes, used by the library replay in §7): drop components whose
  bounding box aspect exceeds 3.2 with the long side over 6 px × scale (streaks, edges);
  drop components whose local background minimum within `EDGE_R` (10 px × scale) falls
  below `DARK_FLOOR` (35/255 on the stretched frame) — glints on silhouette rims and
  cables. Both rules are measured on the reporter's frame stills (§0.3) and the reference
  frames before the constants are fixed.
- Exposure-midpoint timestamps (reliability plan F7): the capture worker records the
  exposure start; `feed_frame` receives `start + exposure/2` instead of `datetime.now()`.
  Watch mode uses the FITS `DATE-OBS` when present, else the file's mtime minus half the
  sidecar exposure.

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
  `_coarse_orientation_candidates`); refine the best with `scipy.optimize` over
  (axis, a1, cx, cy) on the support-weighted residual, a1 bounded to 0.7–1.5× and the
  centre to ±0.1·sky_r of the sky-circle seed; report the peak's width as `sigma_px`.
  The sky circle is a **seed only** — on an obstructed rig it is not a measurement
  (§0.3). The correct scale gives a sharper peak, and the refined a1 / centre are a free
  plate-scale and optical-centre measurement for package 5.
- Output: a `PoleEstimate` (add `sigma_px: float` and `source: 'polaris' | 'rotation'`
  fields; defaults keep existing constructors working) with `east_left` from the
  rotation sign, `n_frames`, `span_minutes`, window bounds.
- Requirements: ≥ 8 frames spanning ≥ 45 min, ≥ 40 stripped detections per frame in the
  median; below that, `None`.
- Hemisphere from the sign of the site latitude (already required for calibration); no
  user setting. The rotation path has no `lat ≥ 20` restriction — the southern pole is
  found the same way. Southern operation is validated on the synthetic fixture only until
  a southern user sends a buffer dump; say so in the module docstring and the wiki.
- `find_pole` becomes the orchestrator: Polaris path first (4a); if it withholds, the
  rotation path; if both give an estimate they must agree within `3·sigma_px + 20 px` or
  the result is `None` with a WARNING naming both pixels (a disagreeing Polaris is the
  LED case; a disagreeing rotation fit is a contaminated field).
- `PoleHistory`: add `sigma_px` to the consensus (cluster scatter across independent
  windows, floored at the per-run sigma) and a `mean` position instead of "latest". The
  75 % dominant-mode rule and the vote ledger stay.

**4d** — moved to package 0.

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

**5b — Orientation search by roll vote** (`orientation_search.py`; see §6):

- For each (axis_alt, axis_az, a1, mirror) cell, catalogue stars and detections at the
  same radius from the centre (± `tol_r` ≈ 0.017·w) pair up; each pair implies one roll
  (`φ_catalogue − φ_detection`); a 360-bin histogram (3-bin window) picks the roll. Cell
  score is `(peak − pairs·3/360) / √max(1, pairs·3/360)` — excess over chance in σ, so a
  small scale that crowds the sky cannot win on random pairings (issue #33's failure at
  its source). Roll is never gridded.
- Frames: the long-baseline ring's first, middle and last (package 4), so a wrong
  orientation cannot line up with stars that have moved; fall back to the rolling
  buffer's sampled frames when no ring exists.
- Scale: scan `a1` over 0.7–1.5× the seed in ×1.05 steps (the seed is the rotation fit's
  plate scale when `source == 'rotation'`, else `a1_from_sky_radius`). The reporter's rig
  needs this: §0.3 shows the sky-circle seed is not a measurement there.
- With a trusted pole the axis is known: invert the radial function at the pole pixel to
  get θ, φ, and `(axis_alt, axis_az)` follow from requiring `altaz_to_pixel(|lat|, 0|180)`
  to land there. The search is then scale × mirror with roll voted — a few dozen cells
  instead of 2016 × 2 — and each cell can afford every ring frame. Without a pole the
  full axis grid (alt 0–90° in 3°, az step widened by 1/cos(alt) near the zenith) runs
  with the same vote; that is the unseeded path and it must stand on its own.
- Verify the top candidates (≈ 40) as today through `_fit_and_validate`, preceded by a
  **centre offset vote**: every catalogue star brighter than mag 3 votes for its (dx, dy)
  to each detection within reach in a 4 px-binned 2-D histogram; the peak corrects
  `cx, cy` before the first match. Then the tolerance schedule.

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

**5d′ — Catalogue epoch** (`coords.py`): precess J2000 to the frame date (IAU 1976 is
enough; ≈ 0.36° in 2026, ≈ 7 px at a1 = 1097 and not a rigid rotation the fit can
absorb). Test against a known star's published apparent position. This is part of the
residual floor every fit has been paying and belongs before the tolerance is tightened.

**5e — Rival test on escape** (`bootstrap_selection.py`): after `select_bootstrap_winner`,
if a second survivor whose matched star set (`matched_stars` names) overlaps the winner's
by < 50 % has `chance_excess ≥ 0.5 ×` the winner's, the escape yields **no** candidate
("two incompatible solutions explain the frames"). Logged with both orientations.

**Consider, after the field results** (not committed): one more tightening step to
≈ 0.003·w when the fit supports it; resolution-scaled RMS tiers (≈ 2 px per 1080 px).
**Not in this package**: other lens projection families — the a3/a5 polynomial covers
them to first order and a3/a5 pinning already flags a lens the polynomial cannot fit.

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
once package 0 ships in a dev build: leave capture running for a clear night so the
buffer dump exists, then send a diagnostics bundle. Turning off **Skip Sky
Features When Roof Closed** is *not* the answer (it would draw on the closed roof more,
not less); package 2's exposure floor and no-stars rule are.

## 5. Decisions (maintainer, 2026-09-25)

1. **Six PRs.** The buffer dump is package 0 and ships first in a dev build so the
   reporter's sky can be replayed before the pole work is written.
2. **Exposure floor 0.5 s**, `allsky_overlay.min_exposure_s` in `config.json`, editable
   there and on the All-Sky card; `0` disables.
3. **A Closed roof verdict stays authoritative.** Detected stars never override it in
   package 2. A model-based override (bright-anchor hits through a model that package 3
   marks credible) is a follow-up after package 3.
4. **Equipment map `HEAL_FRAMES = 600`.** The map removes places, never features: labels
   and calibration keep running; only the observable-sky gate can suppress them.
5. **`CREDIBLE_RMS_FRACTION`**: measured on every log to hand (§0.5), RMS / final
   tolerance is:

   | Fit | RMS / final tol |
   |---|---|
   | Reference rig guided model, 7 anchors (13 px limit) | 0.32 |
   | Last genuine automatic fit, #10 rig, 2026-09-05 (n = 4561, 3/3 anchors) | 0.48 |
   | Chance-band fits, reference rig, Sep 17–20 (35 fits) | 0.64–0.83 |
   | Chance-band fits, reporter's rig, Sep 23 (62 fits) | 0.65–0.72 |

   No automatic fit has been admitted on any rig since Sep 5. 0.6 leaves 0.04 to the
   lowest chance fit; **0.55** sits midway between the last genuine fit and the first
   chance fit, and a false "credible" is the expensive error. Proposed 0.55, awaiting the
   maintainer's confirmation; the constant carries this table in its comment.
6. **Both hemispheres**, hemisphere from the latitude sign, southern path validated on
   synthetic data only until real southern data exists.

## 6. Cross-check against an independent all-sky solver (2026-09-25)

A self-calibrating pier-camera solver that aligns a fixed camera to the sky from star
detections and frame times alone was read end to end and compared with this plan. It never
uses the pole, Polaris, or a sky-circle estimate; it succeeds on its rigs because of a
handful of choices, listed here with what each one means for us. Adopted items are folded
into the packages above.

| Technique | What it does | Ours today | Verdict |
|---|---|---|---|
| **Roll by vote, not by grid** | For each (axis, scale, parity) a catalogue star at radius *r* pairs with every detection at the same radius ± tol; each pair implies one roll; a 360-bin histogram picks it. Search cost is axis × scale, not axis × scale × roll. | `_coarse_orientation_candidates` grids roll in 15° steps (24 cells) at one fixed scale. | **Adopt** in package 5 `orientation_search.py`. With a known pole the axis is fixed and the vote alone finds roll. |
| **Score = excess over chance in σ** | Cell score is `(peak − pairs·3/360) / √chance`, not the raw count, so a small scale that crowds the sky into a small circle cannot win on random pairings. | Raw match count in the coarse grid; `chance_matches` bolted on after the fit (issue #33). | **Adopt** in package 5. This is the fix for #33's class of failure at its source. |
| **Scale searched, not assumed** | Focal length scanned geometrically (×1.03 steps over a 10× range) and five projection families. | `a1 = a1_from_sky_radius(sky_r)` — an estimate that §0.3 shows is unreliable on an obstructed rig; no scale search. | **Adopt the scale scan** (0.7–1.5× the seed, ×1.05 steps) in package 5. Projection families stay out: our a3/a5 polynomial covers them to first order. |
| **Frames hours apart** | The search uses the first, middle and last dark frames of the night; verification nine spread across it. A wrong orientation cannot line up with stars that have moved 45°. | 60 consecutive frames over ~50 min. | **Adopt** as a long-baseline ring in package 4 (below). Also improves the rotation-pole fit: longer arcs, sharper axis. |
| **Centre by offset vote** | Before matching, every bright catalogue star votes for its (dx, dy) to every nearby detection in a 2-D histogram; the peak is the centre error. Robust where nearest-neighbour matching fails at loose tolerance. | LM with cx/cy bounded ±100 px of the seed. | **Adopt** as the first verify step in package 5 `joint_fit.py`. Cheap. |
| **Rival test** | A second candidate explaining the frames with a mostly different star set at ≥ half the winner's count fails the run. | None. | Already in package 5e (from the issue). Keep. |
| **Static-light drop** | Detections at the same pixel ± 2.5 px in ≥ 2 frames ≥ 45 min apart are removed. | None. | Package 4b already; ours uses the track-coherence test so Polaris (2–3 px real motion here) survives. Keep ours. |
| **Detector rejects glints and streaks** | Components adjacent to dark regions (silhouette edges, cables) are skipped via a min-filter of the local background; elongated components (aspect > 3.2 and > 6 px) are skipped; area caps scale with resolution. | Local background and area caps only. | **Adopt** in package 4 as `detection_filters.py`. The dark-neighbourhood rule complements the equipment map: the map covers the interior of equipment, this covers its rim. |
| **Exposure-midpoint timestamps** | Every frame carries its mid-exposure UTC; a wrong clock is named as the one failure the solver cannot detect. | `feed_frame(..., datetime.now(utc))` at feed time (`image_processor.py:408`); reliability plan F7, deferred since June. 30 s exposure + processing ≈ 20–30 s late ≈ 0.12° of rotation ≈ 2 px at 1000 px from the pole. | **Adopt** in package 4: stamp the capture midpoint from the capture worker. |
| **Precession to date** | Catalogue J2000 positions precessed to the frame epoch. | Refraction yes (`coords.py`, Bennett), precession **no**: 26 years ≈ 0.36° ≈ 7 px at a1 = 1097, not a rigid rotation the fit can absorb. | **Adopt** in package 5 (`coords.py`, IAU 1976 or equivalent, tested against a known star). Part of the residual floor every fit here has been paying. |
| **Tight final tolerance and resolution-scaled RMS** | Final match at ≈ 0.0026·w; accept only RMS ≤ 2 px per 1080 px of frame (≈ 5.3 px at 2840, ≈ 6.6 at 3552). | Final tolerance 18 px × `tol_scale` ≈ 0.005·w; tiers accept 12–15 px. | **Consider** in package 5: one more tightening step to ≈ 0.003·w when the fit supports it. Do not change the tiers until package 3's credibility rule has run on real fits. |
| **Nightly self-check, two strikes** | Match the saved model against one frame per night; fail if < 25 % of the count it predicts; two consecutive failures → recalibrate; skip the check on a frame with too few detections ("cloudy: nothing to judge by"). | Package 3's incumbent chance score with two consecutive chance-level runs. | Equivalent; **adopt the cloudy guard** (skip scoring when the buffer's median detection count is under the floor) in package 3. |
| **Second-night confirmation** | A new calibration is checked against frames from a different night before it is trusted. | Provenance rungs within a night. | Optional later: a `'confirmed'` stamp when a model passes the incumbent score on a later night. Not in these packages. |
| **Detection at reduced resolution** | Frames reduced to ~2.5 MP before detection. | Full frame, area thresholds scaled. | Not adopted; our cost is acceptable and full-resolution centroids are worth keeping for a 16 px → 10 px tolerance. |

One structural observation: that solver needs no pole because its search is global,
chance-scored and multi-hour. Our plan reaches the same robustness by two routes that
reinforce each other — the rotation-derived pole (package 4) is the same information as
multi-hour frames voting together, and the pole-seeded roll vote (package 5) is the
global search with two of its three orientation angles already known. If package 4's pole
is withheld on a rig, package 5's search must still work unseeded, which is exactly the
roll-vote-plus-scale-scan path.

## 7. Test data (2026-09-25)

Real frames matter more than synthetic ones for this work, and the reference rig produces
a usable set without any code change. What exists, what it is good for, and what it is
not.

### 7.1 Reference-rig image library

The app-data folder of the reference rig (synced to Google Drive, `PFRSentinel/Library/`)
holds the image library: one JPEG per processed capture, in per-night folders
(`2026-09-17` … `2026-09-21` at the time of writing), plus `library.db` (SQLite, table
`images`: `captured_at`, `exposure`, `gain`, `temp`, `camera`, `weather`, `roof`,
`condition`, `clouds`, `star_count`, `seeing`, `fwhm` per frame).

- **Format:** the finished output image — 750 px longest edge (`library.max_dimension`),
  JPEG quality 85, stretched, with the weather / roof / camera text and logo burned in.
  Not linear, not full resolution, and the text boxes are static high-contrast features.
- **Retention is 7 days** (`library.retention_days`) and pruning runs in-app every 15 min,
  so these nights vanish as soon as the app is next started. **Copy the per-night
  folders and `library.db` out now** to a permanent, gitignored location
  (`sample_images/reference_rig_2026-09/` is the convention this repo already ignores).
- **Ground truth for the same nights:** the guided model of 2026-09-15 (§0.5) scaled by
  `model_in_frame` to 750 px, and the reference rig's logs for Sep 17, 18 and 20 (pole
  estimates, every rejected fit's parameters).

**Good for:**

- Package 2 fixtures: a real closed-roof frame at a 13 s exposure (Sep 17 23:59:46, roof
  Closed 100 %) that the exposure floor cannot catch and the no-stars rule must; dusk
  frames from Sep 19 (sun gate); moonlit and clear frames from Sep 17–18. Each carries
  its `roof`, `exposure` and `star_count` in `library.db`, so tests can be built from the
  database rows instead of hand labels.
- Package 1: the equipment ring at 750 px over several nights, for the map's convergence
  and healing tests on real silhouettes.
- Packages 4 and 5, low-resolution regression: build the buffer format from a library
  night (detections from `detect_stars` on each JPEG, `dt` from `captured_at`,
  `exposure` from the row) and require the solver to re-find the guided model's
  orientation: axis within 1°, mirror correct, a1 within 5 % of 1259 × (750 / 3552), the
  projected pole within 8 px at 750 px (≈ 40 px full-res). A solver that fails this on
  the maintainer's own rig is not fixed.
- Package 4a, negative control: at 750 px the predicted Polaris arc over 35 min is
  about 0.4 px, far below the static noise floor — the finder **must withhold** on these
  frames rather than pick a light. (The full-resolution logs show it finding Polaris
  correctly, §0.5, which is the positive control.)

**Not good for:** RMS / tolerance tuning (JPEG centroids at 750 px), package 3's chance
thresholds, or anything at the 16 px scale of the full sensor. Burned-in text must be
masked in any replay: add `ignore_rects` to the detector filters (package 4) for the
overlay boxes, whose positions are known from the overlay config.

### 7.2 Package 0 gets a library loader

Add `scripts/dev/allsky/library_to_buffer.py` to package 0: reads a library night folder
plus `library.db`, runs `detect_stars` on each frame, and writes the same JSON the buffer
dump produces (`dt`, `detected`, sky circle, frame size, exposure). The replay harness
then has real data from the first day, and the same script converts any future user's
library into a replayable night without a dev build.

### 7.3 Full-resolution frames

The library never holds full-resolution frames. The reference rig's file sink and the
raw-frame diagnostics export are the sources for those; one clear night of saved
full-resolution output on the reference rig (a few hundred frames at 3552 px) is the
dataset packages 4 and 5 should be tuned on. Package 0's buffer dump captures the
detections without the images and is enough for the solver; the images are needed only
for the detector filters (package 4) and the equipment map (package 1).

### 7.4 Reference-rig ML contribution set: 500 labelled frames with the real roof state

`PFRSentinel/ml_contribution/` on the reference rig (Drive-synced) holds the opt-in
community-data collection: **500 samples**, 2026-06-26 to 2026-07-18, one every 30 min
day and night, camera ASI676MC. Each sample is `samples/lum_<stamp>.fits` (256 × 256
luminance, downscaled from the 3552 px frame) plus `calibration_<stamp>.json` with:

- `roof_state` **from NINA's safety monitor** (`source: nina_api`, `roof_open`,
  `is_safe`, device "Building 8") — the observatory's own roof truth, not a classifier;
- `exposure`, `gain`, bit depths; `stretch.median_lum`, `is_dark_scene`, percentiles,
  corner analysis; `moon_context` (illumination, up/down); `weather_context`
  (cloud cover %, condition); `time_context`.

**Good for — package 2 above all.** This is a labelled set for the observable-sky gate:
run `detect_stars` on each 256 px FITS and tabulate detections against the NINA roof
state, exposure and cloud cover. It answers, on real data, whether `min_star_detections`
separates a closed roof from open sky and where cloud sits, and it measures the ML roof
classifier's agreement with the observatory's roof (the number behind decision 3). At
256 px only the bright stars survive, which is the regime the no-stars rule has to work
in anyway. Package 2's tests should carry a small slice of it (a dozen FITS + sidecars,
a few hundred KB) as a real fixture next to the synthetic ones.

**Caveat:** `time_context` in these sidecars predates the western-site fix
(`test_time_context.py`, "never night in the afternoon"): the sample at 13:56 UTC on
July 18 says `is_astronomical_night: true` two hours after sunrise. Recompute night
from `timestamp` with today's `compute_time_context`; do not trust the stored flag.

**Not good for:** anything about calibration or the equipment map — 256 px is a
fourteenth of the sensor. The camera also moved on 2026-07-02 (pole-anchor plan), so the
set straddles two orientations.

This folder is not subject to library pruning, but it is the only copy: keep it with the
other test data.

## 8. Resource budget (maintainer requirement, 2026-09-25)

Calibration runs on a 24/7 observatory PC beside the capture loop, the stretch pipeline,
ML inference and the timelapse encoder. Nothing in these packages may make that box
noticeably busier. Concrete limits, checked in review and reported by each package with
the measurement behind it:

| Path | Budget | How |
|---|---|---|
| Threads | **No new threads, pools or processes.** All calibration work runs on the existing single `_RefineWorker` thread at its existing cadence (120 s cooldown, doubling to 30 min; escapes on their own back-off). The GUI thread gets signals only. | Review: grep for `Thread`, `Pool`, `multiprocessing`, `QThread` in the diff. |
| Per-frame work on the capture path (packages 1, 2) | ≤ 30 ms per frame at 3552 px on this container's CPU, single core; detection on a plane downscaled to ≤ 1500 px longest edge; the map update at ≤ 512 px. No new full-frame copies retained beyond the call. | `time.perf_counter()` around the call in a test; report p50/p95 over 50 frames. |
| Incumbent score (package 3) | ≤ 1 s per refinement run; one match pass at the final tolerance, no re-fit. | Measured in the test on a 60-frame synthetic buffer. |
| Rotation pole fit (package 4) | ≤ 10 s per run on 36 ring frames × 200 detections; coarse grid iterates and keeps top-K, never materialises the full score tensor; one KD-tree per frame pair, built once; float32. | Timed test; peak RSS delta ≤ 50 MB (`resource.getrusage`). |
| Orientation search (package 5) | Pole-seeded path ≤ 5 s; unseeded roll-vote grid ≤ 30 s and only on cold start / escape, never on a routine refinement. | Timed tests for both paths. |
| Memory | Buffers hold detection lists, never images: 60 rolling + 36 ring frames × ≤ 200 detections is under 1 MB. The equipment map is one 512² float32 plane (1 MB). Dumps and the map are written at most once per 10 min. | Review + `tracemalloc` in the heaviest test. |
| Allocation churn | Prefer in-place numpy on preallocated arrays inside the hot loops; the refine worker's allocation rate is what tripped the cyclic collector in issue #31 (`services/gc_scheduler.py`). | Review. |
| Cadence | No new timers. Package 1 saves on the existing 10-min throttle and on capture stop; package 0 dumps on exhaustion / escape / button only. | Review. |

A package that cannot meet its line says so in its report with the number, and the
reviewer decides between a cheaper algorithm and a raised budget — never a silent
overrun.
