# CLAUDE.md — PFR Sentinel

PFR Sentinel is a dual-mode astrophotography monitoring app built for 24/7 unattended observatory use. It either (1) watches a directory for new images written by another capture program (e.g. NINA), or (2) captures directly from a ZWO ASI camera. Either way, it adds configurable metadata + weather overlays and pushes the result to multiple output sinks simultaneously (file, web, Discord).

Stack: Python 3.13, PySide6 6.10.2 (pinned) + qfluentwidgets 1.11.1 (Windows 11 Fluent Design), Pillow, OpenCV, watchdog, ONNX runtime for ML inference. Packaged as a Windows installer via PyInstaller + Inno Setup.

## Capture modes

1. **Directory Watch** — `services/watcher.py` (watchdog) detects new FITS/JPEG/PNG, waits for file stability, parses sidecar metadata, runs the processor.
2. **ZWO Camera** — `services/camera/zwo_camera.py` captures RAW8 Bayer frames, debayers (BGGR), and produces a PIL image + metadata dict for the processor.

## Output sinks (run simultaneously)

- **File** — saves to disk
- **Web** — HTTP server, `/latest` (image) and `/status` (JSON) endpoints
- **Discord** — periodic webhook posts with weather embeds

All output dispatch goes through `_push_to_output_servers()` in `ui/main_window/output.py`.

## Project structure

```
PFRSentinel/
├── ui/                         # PySide6 + qfluentwidgets UI
│   ├── main_window/            # FluentWindow + QStackedWidget navigation (window.py, capture.py, output.py, lifecycle.py …)
│   ├── system_tray_qt.py       # QSystemTrayIcon tray menu (no third-party backend)
│   ├── components/             # Reusable widgets (header, monitoring panel, status indicator)
│   ├── panels/                 # Pages (layout only) — monitoring, capture, output, overlays, timelapse, logs
│   ├── controllers/            # Business logic — capture, output, overlay, timelapse, ML prediction
│   └── theme/                  # tokens.py, styles.py, accent_themes.py, special_themes.py (seasonal packs), icons.py
├── services/                   # Core processing modules
│   ├── host_platform.py        # OS facts for user-facing wording (labels, SDK filename, file manager)
│   ├── reveal_in_file_manager.py # Explorer / Finder / xdg-open launch, one implementation
│   ├── font_loader.py          # PIL font resolution: Arial (Windows) → bundled Space Grotesk → DejaVu → default
│   ├── config.py               # Config class — load/save/merge; re-exports the defaults
│   ├── config_defaults.py      # DEFAULT_CONFIG + DEFAULT_CAMERA_PROFILE (data only)
│   ├── utils_paths.py          # get_app_data_dir() — per-platform app-data root; resource paths
│   ├── app_config.py           # App identity constants + canonical calibration paths
│   ├── logger.py               # Thread-safe queue logger (app_logger singleton)
│   ├── processor.py            # Image overlay engine — dual input: PIL Image OR file path
│   ├── watcher.py              # watchdog FileSystemEventHandler
│   ├── camera/                 # ZWO subpackage — zwo_camera.py (SDK wrapper, BGGR debayer, auto-exposure),
│   │                             camera_connection.py (SDK init, detection, USB reconnect),
│   │                             camera_calibration.py, camera_utils.py
│   ├── cleanup.py              # Disk space management (files only, never folders)
│   ├── gc_scheduler.py         # GUI-thread cyclic GC (automatic GC off, QTimer-driven)
│   ├── discord_alerts.py       # Discord webhook client
│   ├── weather.py              # OpenWeatherMap API, 10-min cache
│   ├── web_output.py           # HTTP server
│   ├── api_control.py          # Capture-control command logic (pure)
│   ├── api_auth.py             # Control-API bearer auth, Host allow-list
│   ├── web_control.py          # POST /capture/start|stop routes
│   ├── nina_plugin_install.py  # Install/update/remove the NINA plugin
│   ├── pe_version.py           # Windows PE FileVersion reader
│   ├── timelapse_writer.py     # ffmpeg stdin pipe, time-gated capture
│   ├── timelapse_window.py     # Recording-window maths (sun/fixed/always), local-date aware
│   ├── timelapse_window_forecast.py # Projected recording window for the Timelapse Status card
│   ├── capture_schedule_window.py # Capture schedule that follows the timelapse window ± margin
│   ├── ffmpeg_utils.py         # Shared ffmpeg detection
│   ├── diagnostics_bundle.py   # Support ZIP: logs + redacted config + frames (pure)
│   ├── frame_static_score.py   # Is a frame scene or only sensor noise? Reporting only — gates nothing
│   ├── raw_frame_export.py     # Cached frame → Bayer FITS + unprocessed PNG + JSON
│   ├── allsky/                 # All-sky fisheye calibration + overlay
│   ├── meteor/                 # Meteor detection — see METEOR_DETECTION_PLAN.md
│   ├── library/                # Image library index, sessions, retention
│   └── notifications/          # Notification dispatcher + backends (Discord, Hermes)
├── ml/                         # Scene classifiers (roof, sky conditions, stars, moon) — ONNX inference
├── tests/                      # pytest suite — see "Testing" below
├── docs/                       # Plans, design docs, references; docs/wiki/ is the user wiki source
├── archive/                    # Legacy Tkinter GUI (do not modify)
├── nina-plugin/                # NINA plugin (C#/.NET 8 + WPF) — see below
├── installer/                  # Inno Setup packaging
├── main.py                     # Entry point
└── version.py                  # __version__
```

## Architecture patterns

### Data flow
- **Watch mode**: `watcher.py` → file stable → parse sidecar → `processor.py` → `_push_to_output_servers()`
- **Camera mode**: `camera/zwo_camera.py` → debayer → PIL Image + metadata → `camera_controller.py` → `processor.py` → outputs

### Threading
- Camera capture, watcher observer, Discord poster, and web server all run on background threads.
- All GUI updates flow through Qt signals/slots or `QMetaObject.invokeMethod()` — never touch widgets from worker threads.
- Logger uses a queue to avoid race conditions.

### UI architecture
- `ui/panels/` are layout only.
- `ui/controllers/` own business logic and threading.
- Communication is Qt signals/slots.

### Processor entry point
`services/processor.py` `add_overlays()` is dual-input — accepts a file path string OR an in-memory PIL image. Both modes route through the same code.

### Overlay tokens
Standard: `{CAMERA}`, `{EXPOSURE}`, `{GAIN}`, `{TEMP}`, `{RES}`, `{FILENAME}`, `{SESSION}`, `{DATETIME}`
Weather (requires `weather.api_key` + `weather.location` in config): `{WEATHER}`, `{WEATHER_ICON}`, `{TEMP}`, `{HUMIDITY}`, `{PRESSURE}`, `{WIND_SPEED}`

### Config
- Lives at `<app-data root>/config.json`. Always resolve the root through `services.utils_paths.get_app_data_dir()` — never build a platform path or read `LOCALAPPDATA` / `APPDATA` yourself.
- The root is per-platform (illustrative — the helper is the source of truth): `%LOCALAPPDATA%\PFRSentinel` on Windows, `~/Library/Application Support/PFRSentinel` on macOS, `~/.local/share/PFRSentinel` on Linux. Logs sit at `<app-data root>/logs` on every platform.
- Loaded with a merge-against-`DEFAULT_CONFIG` pattern, so new keys land safely on old configs.
- Keys are nested. Output flags live under `output_config.*`, camera settings under `camera_profiles[<clean_name>]`.

### Module discipline
New functionality gets a new file when it has a distinct responsibility — don't just append to the nearest existing file. The panel/controller/service split enforces SRP by layer; apply the same thinking *within* each layer. When in doubt, name the new module after what it *does* (`image_stretch`, `token_substitution`, `compass_geometry`), not what calls it. See `.claude/rules/python-general.md` → "Module design" for the full checklist.

### ML module
Phase 1–3 complete; Phase 4 future:
- **Phase 1**: Roof open/closed (CNN, 100% on test set)
- **Phase 2**: Sky conditions (85.3%), stars (91.2%), moon (100%)
- **Phase 3**: Dev-mode integration that saves calibration JSON + FITS per frame
- **Phase 4** (future): Stretch recipe prediction
- All inference is local via ONNX. Production interface: `ui/controllers/ml_prediction.py`.

## Working on this codebase

Detailed conventions are split by file type and live in `.claude/rules/`. **Read the relevant rule file before non-trivial changes.**

| When editing… | Read… |
|---|---|
| Any `.py` file | [`.claude/rules/python-general.md`](.claude/rules/python-general.md) — logging, imports, config, file-size cap, PostHog |
| `ui/panels/**` | [`.claude/rules/ui-panels.md`](.claude/rules/ui-panels.md) — UI only, no business logic |
| `ui/controllers/**` | [`.claude/rules/ui-controllers.md`](.claude/rules/ui-controllers.md) — threading, signals/slots |
| `services/**` | [`.claude/rules/services.md`](.claude/rules/services.md) — config, cleanup, processing pipeline order |
| `services/camera/**` | [`.claude/rules/services-camera.md`](.claude/rules/services-camera.md) — BGGR debayer, exposure units, disconnect cleanup, per-camera profiles |
| `services/allsky/**` | [`.claude/rules/allsky.md`](.claude/rules/allsky.md) — calibration, coordinate frames |
| `tests/**` | [`.claude/rules/tests.md`](.claude/rules/tests.md) — pytest markers, fixtures |
| `ml/**` | [`.claude/rules/ml.md`](.claude/rules/ml.md) — ONNX inference conventions |
| `nina-plugin/**` | [`.claude/rules/nina-plugin.md`](.claude/rules/nina-plugin.md) — C#/WPF, NINA API, build + deploy |

Two hooks enforce the most-violated rules automatically:
- `.claude/hooks/check_file_size.py` (PreToolUse) — blocks Edit/Write that would exceed the per-file size cap. Covers `.py`, and `.cs`/`.xaml` in `nina-plugin/`.
- `.claude/hooks/check_panel_purity.py` (PostToolUse) — warns when business-logic markers appear in `ui/panels/`.

Slash commands: `/audit-size`, `/audit-tests`, `/pre-commit-check`. Reviewer subagent: `pfr-reviewer`.

## Development

```powershell
# Quick start
.\start.bat

# Manual
.\venv\Scripts\Activate.ps1
python main.py

# Build
.\build_sentinel.bat              # PyInstaller exe (Python 3.13 needs an email module workaround)
.\build_sentinel_installer.bat    # Inno Setup installer
```

## Testing

```powershell
# Default — skip hardware/network tests
pytest -m "not requires_camera and not requires_network and not requires_ml_models"

# Full suite
pytest
```

### Continuous integration

`.github/workflows/ci.yml` runs on every push to `main` and every pull request:
ruff (syntax errors, undefined names, redefinitions — see `ruff.toml`), the
file-size audit (`scripts/ci/check_file_sizes.py`; caps and frozen exceptions
live in `scripts/ci/size_policy.py`, shared with the local size hook), `pip-audit` against the installed packages, and the default pytest
run with one pytest-xdist worker per core. The test job is a matrix over
`windows-latest`, `macos-latest` and `ubuntu-latest` (issue #38) — Windows is
still the shipping platform, the other two prove the port stays honest. Tests
that only make sense on Windows carry `@pytest.mark.requires_windows` and are
skipped elsewhere by `tests/conftest.py`, so the pytest command is identical on
every runner. `pip-audit` runs on the Windows job alone; the dependency set is
the same everywhere. Dev tooling is pinned in `requirements-dev.txt`.

The three test jobs are named **`Tests and dependency audit (Windows)`**,
`Tests (macOS)` and `Tests (Linux)`, and `main`'s ruleset ("main: CI must pass" —
a repository ruleset, not classic branch protection) requires each one by that
exact string. Renaming any of them leaves the required check stuck on
"Expected — waiting for status to be reported", blocking every merge. **Don't
rename a job without changing the ruleset in the same PR.** The Windows name is
longer because `pip-audit` runs there alone. All three became required with
cross-platform Phase 1 (#37): Directory Watch mode runs from source on macOS and
Linux, so a failure on those runners is a user-facing regression, not information.

The ruleset's required checks are `Lint and size audit`, the three test jobs and
`claude-review`, plus a code scanning rule: CodeQL results must be in, and a PR
may not add alerts at `error` or security severity `high_or_higher`. Auto-merge
waits for exactly these and nothing else — the dev build and spec parses can
still be running when a PR merges.
`build.yml` is deliberately not required: it skips docs-only PRs, so a required
build check would never report on them. `claude-review` only makes a merge wait
for the review to post; it passes whatever the review finds. Any check added here
must report on *every* PR (skipped counts as passing, absent does not).
When CI fails on a same-repo PR, `claude-ci-fix.yml` has Claude open a fix PR
against that branch. CodeQL and Dependabot are enabled at the repo level.

`.github/workflows/build.yml` makes unsigned dev builds. Every merge to `main`
(or a dispatch with `publish` ticked) replaces the single rolling `dev-latest` prerelease and posts
the build to that release cycle's "Dev builds: X.Y.Z" Discussions thread, with the
PRs merged since the last release (text: `scripts/ci/dev_build_notes.py`; PR list:
`dev_build_changelog.py`; thread lifecycle: `dev_build_discussion.py`). The updater polls `/releases/latest`, which
excludes prereleases, so production installs never see it. Every build here is
stamped `X.Y.Z-dev.N` (`scripts/ci/set_dev_version.py`: next patch after the
newest `vX.Y.Z` tag, N = commits since it; exe FileVersion `X.Y.Z.N`), and the
updater ranks `-dev` below the release it leads up to.

`.github/workflows/build.yml` produces **dev builds only**, on every pull
request and on manual dispatch. Every artifact has `DEV_MODE_AVAILABLE=True`
(raw FITS/TIFF exports, calibration JSON, ML prediction compiled in), written
into `services/dev_mode_config.py` by `scripts/ci/set_build_channel.py` before
PyInstaller runs. **The `PFRSENTINEL_DEV_MODE` environment variable cannot do
this** — it is read when `dev_mode_config` is imported, which for a frozen app
is on the user's machine at launch, so setting it in a CI job changes nothing
about the artifact. PRs get the app folder; dispatch also builds the Inno Setup
installer by default, which is the only regular exercise `installer/PFRSentinel.iss`
gets.

There is **no production build in CI, and no tag trigger on `build.yml`**.
Release builds are signed, and signing cannot run on a hosted runner:
`scripts/Connect-SimplySign.ps1` drives the SimplySign Desktop GUI with
synthetic keystrokes (needs an interactive desktop session), and `CERTUM_OTP_URI`
is the TOTP seed for the signing identity. An unsigned production artifact would
be a release candidate nobody can release. **Cut releases locally with
`build_sentinel_installer.bat`**, which signs `PFRSentinel.exe` *before* Inno
Setup packages it and signs the installer afterwards — the order matters, so a
CI-built installer cannot simply be signed after the fact. Use
`python scripts/ci/set_build_channel.py production` there in place of the manual
checklist.

Tags still run `tag-pushed.yml`, which now asserts `version.py` matches the tag.
`claude-release-notes.yml` chains off it and only fires on success, so a
mismatched tag blocks the draft rather than producing notes for a version no
artifact will carry.

The Windows build job also compiles the NINA plugin with `dotnet` (its only
automated check — there is no C# test suite) and asserts the exe's FileVersion
matches `version.py`.

macOS and Linux get `scripts/ci/check_spec_parses.py` instead of a build: it
executes the spec with the PyInstaller classes stubbed, which catches a
Windows-only import or a one-OS `datas` entry in seconds. Actual mac/Linux
packaging is issue #41. PyInstaller is pinned in `requirements-build.txt`, kept
apart from `requirements-dev.txt` so the test matrix doesn't install it three
times.

Other Claude workflows: `claude-code-review.yml` reviews every non-draft PR
(Dependabot PRs excluded); `claude-dependabot-assess.yml` reads the upstream
changelog for each Dependabot bump and leaves a SAFE / CHECK / HOLD comment;
`claude-release-notes.yml` drafts the GitHub release for a pushed `v*` tag in
the house style (never publishes); `claude-issue-triage.yml` labels and
acknowledges new issues. `.claude/` (rules, hooks, commands, agents, skills) is
tracked so these runs read the same conventions a local session does.

Two traps in the Claude workflows, both of which fail **green**:
- `claude-code-action` skips any PR that edits the workflow file it runs from
  (it must match `main`), so a change to `claude-code-review.yml` can only be
  tested after it merges. The skipped job still passes.
- The review plugin launches its agents in the background. Without
  `CLAUDE_CODE_DISABLE_BACKGROUND_TASKS=1` the main session ends its turn to
  "wait", the run ends with them, and the job passes having posted nothing. A
  passing `claude-review` check is not evidence a review happened — look for
  the comment.

### User wiki

The GitHub wiki is published from `docs/wiki/`, which is the source of truth — the
wiki itself has no pull requests, so edits are reviewed here. `wiki-publish.yml`
mirrors `docs/wiki/` to the wiki on every push to `main` that touches it, using the
`WIKI_TOKEN` secret (a fine-grained token with Contents: Read and write;
`GITHUB_TOKEN` cannot push to a wiki). It refuses to run if the wiki was edited
in the browser since the last sync, so copy such edits into `docs/wiki/` first.
`claude-wiki-update.yml` chains off Tag Pushed and opens a PR updating `docs/wiki/`
for what changed since the previous tag, branched from the tag so it documents
the code that shipped. **Merging that PR publishes the wiki** — merge it when
the release is published. When a change alters something users see, update the
matching `docs/wiki/` page in the same PR — it goes live when the PR merges, so put
`> **New in the next release** — not available in version X.Y.Z or earlier.`
under the heading; the tag-time agent removes those notes once the feature ships.

| Test file | Tests | Covers |
|-----------|-------|--------|
| `test_auto_exposure.py` | 21 | `camera_utils` — brightness, clipping, exposure logic |
| `test_camera.py` | 14 | `zwo_camera` — SDK, config, debayering (3 need `requires_camera`) |
| `test_camera_auto_recovery.py` | 20 | `camera_auto_recovery` off — one plain reconnect, no ladder/USB/SDK reset (`camera_reconnect`), capture loop stops instead of retrying (cold-open quirk still absorbed, handle released on the fatal exit), controller schedules no restart or USB reset; on = unchanged |
| `test_discord.py` | 32 | `discord_alerts` — webhooks, embeds (mocked) |
| `test_image_output.py` | 18 | `processor` — overlays, stretch, output formats |
| `test_output_crop.py` | 59 | `output_crop` — box normalisation/clamping/evenness, centring, fit-to-circle, proportional rescale across frame sizes, PIL crop, metadata round-trip, DEFAULT_CONFIG integration |
| `test_settings.py` | 11 | `config` — JSON save/load, merge, defaults |
| `test_webserver.py` | 13 | `web_output` — HTTP server, ETag, status JSON (`requires_network`) |
| `test_ml_classifiers.py` | 6 | `ml.roof_classifier` / `ml.sky_classifier` + production `ui/controllers/ml_prediction.py` — ONNX load + inference smoke tests (`requires_ml_models`) |
| `test_api_auth.py` | 43 | `api_auth` — bearer compare, Host allow-list, token minting, redaction |
| `test_api_control.py` | 51 | `api_control` — command validation, idempotency, `wait` semantics, OpenAPI catalog |
| `test_web_control.py` | 33 | `web_control` — control routes, auth matrix, no-CORS regression guard |
| `test_capture_command_bridge.py` | 11 | `CaptureCommandBridge` + headless handler — GUI-thread marshalling |
| `test_diagnostics_bundle.py` | 9 | `diagnostics_bundle` — secret/location redaction, log-age filter, ZIP contents + summary |
| `test_raw_frame_export.py` | 7 | `raw_frame_export` — Bayer FITS round-trip, unprocessed PNG, scalar metadata |
| `test_zwo_camera_capture_now.py` | 2 | `zwo_camera` — one-shot `request_immediate_capture` wake used by the diagnostics export |
| `test_update_checker.py` | 12 | `update_checker` — prereleases/drafts and the dev asset are never offered; `-dev` ranks below its release |
| `test_dev_build_notes.py` | 12 | `scripts/ci/dev_build_notes.py` — dev tag stays off `v*`, release/discussion text states the risks and carries the change list |
| `test_dev_build_changelog.py` | 7 | `scripts/ci/dev_build_changelog.py` — PRs since the last release via commits, grouping, delta since the previous dev build, API failure never blocks |
| `test_dev_build_discussion.py` | 9 | `scripts/ci/dev_build_discussion.py` — one thread per release cycle, retire/reopen, never edits a post (GITHUB_TOKEN is refused updateDiscussion), never touches people's threads |
| `test_set_dev_version.py` | 11 | `scripts/ci/set_dev_version.py` — dev version base/counter, Windows FileVersion, spec parses the same way |
| `test_timelapse_window.py` | 13 | `timelapse_window` — sun window on the LOCAL calendar date (real astral, injected zones), fixed fallback, per-day cache |
| `test_capture_schedule_window.py` | 39 | `capture_schedule_window` — timelapse-derived capture window ± margin, gate two-day logic, roof/always fallbacks, labels |
| `test_zwo_schedule_gate.py` | 16 | `zwo_camera` — schedule gate vs legacy HH:MM path, fail-open, `scheduled_window_label` |
| `test_api_status_schedule.py` | 11 | `api_status.build_schedule` — status API schedule block for fixed and timelapse sources, with and without a camera |
| `test_schedule_window_source_ui.py` | 5 | `_schedule_window_source` — window-source rows: load, visibility, signals (offscreen Qt) |
| `test_timelapse_window_forecast.py` | 26 | `timelapse_window_forecast` — open/next window, inclusive edges, twilight depths on the local night, fixed-time fallback notes, clock-change durations, no log spam |
| `test_timelapse_status_card.py` | 5 | `TimelapseStatusCard` — session line, projected window line, open-video button (offscreen Qt) |
| `test_output_crop_card.py` | 25 | `OutputCropCard` / `CropBoxEditor` / `OutputCropController` — drag/resize/spin clamping, config round-trip, thumbnail cap + active gating, Fit-to-sky (offscreen Qt) |
| `test_watch_controller_crop.py` | 4 | `WatchControllerQt` — the output crop on `img.info` reaches the all-sky preview renderer; `extras` forwarded on `image_processed` |
| `test_watch_crop_cache.py` | 3 | `_on_watch_image_processed` — watch mode caches the pre-resize clean frame for Calibrate Now, not the cropped output |
| `test_watcher.py` | 2 | `ImageFileHandler` — `process_image` extras reach the callback |
| `test_headless_runner_crop.py` | 3 | `HeadlessRunner._process_and_save` — output crop after resize, before overlays |
| `test_system_tray_qt.py` | 10 | `SystemTrayQt` — native `QSystemTrayIcon` menu state, show/hide, capture gating, `TrayUnavailableError` leaves the window visible (offscreen Qt) |
| `test_reveal_in_file_manager.py` | 15 | `reveal_in_file_manager` — explorer / `open -R` / `xdg-open` argv per platform, missing path and launch failure never raise |
| `test_font_loader.py` | 5 | `font_loader` — bundled Space Grotesk resolves off Windows, Arial never tried there, fallback outcome cached |
| `test_ffmpeg_utils.py` | 15 | `ffmpeg_utils` — PATH first, then winget / Homebrew / distro candidates per platform; winget probe never spawns off Windows |
| `test_windows_only_ui.py` | 5 | `FfmpegInstallCard` copyable install command off Windows; `MissingCameraNotice` hides Revive where there is no USB reset API (offscreen Qt) |
| `test_update_dialog_platform.py` | 3 | `UpdateDialog` — download hidden and GitHub made primary off Windows; installer launch is a no-op there (offscreen Qt) |
| `test_special_themes.py` | 17 | `special_themes` — pack patches accent + neutrals and re-derives aliases, switching off leaves nothing behind, status colours untouchable, neutrals no brighter than Sand, every pack glyph exists in the bundled MDI font, font bundled in the spec; nav rail swaps/restores icons, every sprite state paints in both painter sets, `AppearanceCard` chip/swatch signals and chip restyle on load, `AppBar.show_sending` hold cancelled by a newer frame (offscreen Qt) |
| `test_scroll_safe_spinbox.py` | 10 | `ScrollSafeSpinBox` / `ScrollSafeDoubleSpinBox` — wheel ignored unless the box holds click/Tab focus; page-handed focus never arms it, a click on the child line edit does (offscreen Qt) |
| `test_frame_static_score.py` | 18 | `frame_static_score` — pure noise scores 0.25 at any brightness / bit depth, dim scenes stay scenes, gradients and LEDs don't fool it, unmeasurable input is `None`, full-res plane never copied |
| `test_ml_service.py` | 11 | `ml_service` — `median_lum` bit-depth normalisation, per-frame memory ceiling (`requires_ml_models`), static score reported in results, never changes the roof reading, logged on transition only; time context from the shared service with the configured location cached, daytime defaults when it fails |
| `test_status_strip_static.py` | 10 | `StatusStrip` — static frame keeps the roof reading and marks it unreliable, Sky reads "Too much static", clears on the next good frame even when that frame carries no roof or sky verdict (classifier off, prediction failed, no ML results), a no-verdict frame between good ones still keeps the last reading, warning glyph exists (offscreen Qt) |
| `test_image_processing_panel_stretch.py` | 4 | `ImageProcessingPanel` Auto Stretch rows — Target Median slider reaches the engine floor, Dark Threshold enabled only with Dark Scene Color Fix (offscreen Qt) |
| `test_allsky_label_stability.py` | 34 | `label_stability` + renderer — 15-frame sky-mask vote rides out an exposure change yet still adopts a lasting one, vote kept at reduced resolution and matching a recount, sticky top-N, slot memory; sky mask unchanged when the same sky is exposed less, equipment-edge stars still claim less sky |
| `test_observing_window.py` | 24 | `observing_window` — twilight gate, roof gate and its opt-out, roof must read Closed on two consecutive frames, several callers in one frame count once, a reprocess of the same capture never advances or clears the count, nor does a caller with no roof verdict (Watch mode's overlay render) |
| `test_allsky_guided_hints.py` | 9 | `guided_hints` — three anchors place the other bright stars within a click-snap, capped to the brightest, mis-identified or rotated anchors withhold the hints with a reason, a frame with almost no detections never accuses the anchors |
| `test_allsky_guided_residuals.py` | 3 | `guided_calibration` — per-anchor residuals on the solved model (renamed / excluded) and on the `CalibrationError` of a failed solve |
| `test_allsky_calibration_attention.py` | 12 | `calibration_attention` + `CalibrationService.attention_changed` — caution only when refinements keep failing AND the saved model can't be confirmed; a healthy model stays silent; cleared by a new model or a reset |
| `test_guided_calibration_session.py` | 19 | `GuidedCalibrationSession` — passing solve held unsaved, failure rows name the suspect (one, not all, on a wrong-basin failure), stale hint results dropped and superseded or closing suggestion solves cancelled, every request is answered even when the handler raises; `commit_guided_calibration` reports a failed file write and waits for Calibrate Now (offscreen Qt) |
| `test_guided_calibration_wiring.py` | 2 | `_open_guided_calibration` — the real main-window wiring releases the dialog and both prep frames on cancel and on save (a closure over the dialog leaked them) (offscreen Qt) |
| `test_allsky_guided_dialog.py` | 22 | `GuidedCalibrationDialog` — frame gets the room, stays open through solve / failure with every star kept and the suspect selected, review before save, a refused save can be retried without solving again, suggested names, confirm before discarding (offscreen Qt) |
| `test_star_pick_canvas.py` | 13 | `StarPickCanvas` — clicks in original image pixels at any zoom, wheel zoom about the cursor, clamped pan, drag never picks, picking can be switched off, view cached at the display pixel ratio (offscreen Qt) |
| `test_time_context.py` | 22 | `time_context` — `is_astronomical_night` from sun elevation at absolute instants: flips at astronomical dusk/dawn, never night in the afternoon at a western site (the date-clamp bug), same flag from any host zone, high-latitude summer and polar day never raise, no-location clock fallback, per-day sun-times cache, `ui/controllers` shim re-exports |
| `test_allsky_quality_badge.py` | 11 | `QualityBadge` amber "unconfirmed" / "check alignment" state, panel caution text, controller status line and badge level after a guided save (offscreen Qt) |

Standalone (not in pytest suite):
- `ml/test_classifier.py` — interactive accuracy eval against a user-specific labelled dataset (walks `D:/Pier Camera ML Data`). Use this to validate a new model checkpoint, not for CI.
- `scripts/dev/test_usb_reset.py` — interactive USB reset, requires camera

## Key dependencies

| Package | Purpose |
|---------|---------|
| PySide6 6.10.2 (pinned in requirements.txt) | Qt6 bindings |
| qfluentwidgets 1.11.1 | Fluent Design components |
| opencv-python | Bayer debayering |
| Pillow | Image processing |
| watchdog | Directory monitoring |
| zwoasi | ZWO SDK wrapper |
| onnxruntime | ML inference |
| astral | Sunset/sunrise for timelapse windows |
| requests | Weather API + Discord webhooks |
| ffmpeg (external) | Timelapse |
| PyInstaller 6.17.0 | Standalone executable |

## Active plans

- [`docs/CODE_QUALITY_PLAN.md`](docs/CODE_QUALITY_PLAN.md) — code quality + structure roadmap
- [`docs/ALLSKY_CALIBRATION_PLAN.md`](docs/ALLSKY_CALIBRATION_PLAN.md) — read before touching all-sky calibration
- [`docs/METEOR_DETECTION_PLAN.md`](docs/METEOR_DETECTION_PLAN.md) — meteor detection rework for the long-exposure regime; read before touching `services/meteor/`
- [`docs/FEATURE_HARDENING_PLAN.md`](docs/FEATURE_HARDENING_PLAN.md) — prioritized hardening backlog (web server, ASCOM roof safety file, timelapse) from the 2026-06-28 deep review; P0/P1/P2 + sizing + file:line pointers
- [`docs/ALLSKY_POLE_ANCHOR_PLAN.md`](docs/ALLSKY_POLE_ANCHOR_PLAN.md) — pole-anchor (Polaris) ground truth + model admission gates; fixes the wrong-basin model that poisons refinement
- [`docs/NINA_INTEGRATION_PLAN.md`](docs/NINA_INTEGRATION_PLAN.md) — NINA dockable widget + capture control API + sequencer instructions; read before touching the web control/API surface

Developer-facing technical reference (feature design, build/release tooling, vendor SDK) lives in [`docs/dev/`](docs/dev/README.md). End-user content is on the project wiki, whose source is [`docs/wiki/`](docs/wiki/Home.md).

<!-- code-review-graph MCP tools -->
## MCP Tools: code-review-graph

**IMPORTANT: This project has a knowledge graph. ALWAYS use the
code-review-graph MCP tools BEFORE using Grep/Glob/Read to explore
the codebase.** The graph is faster, cheaper (fewer tokens), and gives
you structural context (callers, dependents, test coverage) that file
scanning cannot.

### When to use graph tools FIRST

- **Exploring code**: `semantic_search_nodes` or `query_graph` instead of Grep
- **Understanding impact**: `get_impact_radius` instead of manually tracing imports
- **Code review**: `detect_changes` + `get_review_context` instead of reading entire files
- **Finding relationships**: `query_graph` with callers_of/callees_of/imports_of/tests_for
- **Architecture questions**: `get_architecture_overview` + `list_communities`

Fall back to Grep/Glob/Read **only** when the graph doesn't cover what you need.

### Key Tools

| Tool | Use when |
|------|----------|
| `detect_changes` | Reviewing code changes — gives risk-scored analysis |
| `get_review_context` | Need source snippets for review — token-efficient |
| `get_impact_radius` | Understanding blast radius of a change |
| `get_affected_flows` | Finding which execution paths are impacted |
| `query_graph` | Tracing callers, callees, imports, tests, dependencies |
| `semantic_search_nodes` | Finding functions/classes by name or keyword |
| `get_architecture_overview` | Understanding high-level codebase structure |
| `refactor_tool` | Planning renames, finding dead code |

### Workflow

1. The graph auto-updates on file changes (via hooks).
2. Use `detect_changes` for code review.
3. Use `get_affected_flows` to understand impact.
4. Use `query_graph` pattern="tests_for" to check coverage.
