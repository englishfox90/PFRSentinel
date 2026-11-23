# CLAUDE.md — PFR Sentinel

PFR Sentinel is a dual-mode astrophotography monitoring app built for 24/7 unattended observatory use. It either (1) watches a directory for new images written by another capture program (e.g. NINA), or (2) captures directly from a camera — a ZWO ASI over USB, or any ASCOM Alpaca camera on the network. Either way, it adds configurable metadata + weather overlays and pushes the result to multiple output sinks simultaneously (file, web, Discord).

Stack: Python 3.13, PySide6 6.11.2 (pinned) + qfluentwidgets (`PySide6-Fluent-Widgets` ≥ 1.11.3; Windows 11 Fluent Design), Pillow, OpenCV, watchdog, ONNX runtime for ML inference. Packaged as a Windows installer via PyInstaller + Inno Setup.

## Capture modes

1. **Directory Watch** — `services/watcher.py` (watchdog) detects new FITS/JPEG/PNG, waits for file stability, parses sidecar metadata, runs the processor.
2. **Camera** — `capture_mode == "camera"`, with `camera_source` picking the backend (`services/camera/camera_source.py`); both are `CaptureSessionBase` sessions driven by the same shared capture loop and produce a PIL image + metadata dict for the processor.
   - **ZWO** (`camera_source: "zwo"`, default) — `services/camera/zwo_camera.py` captures RAW8/RAW16 Bayer frames through the ZWO SDK and debayers (BGGR).
   - **Alpaca** (`camera_source: "alpaca"`, issue #43) — `services/camera/alpaca/alpaca_camera.py` drives an ASCOM Alpaca camera over HTTP (ASCOM Remote, a vendor's Alpaca server); Bayer pattern and bit depth come from the device, no USB reset, no SDK. See `docs/ALPACA_CAMERA_PLAN.md`.

## Output sinks (run simultaneously)

- **File** — saves to disk
- **Web** — HTTP server, `/latest` (image) and `/status` (JSON) endpoints
- **Discord** — periodic webhook posts with weather embeds

All output dispatch goes through `_push_to_output_servers()` in `ui/main_window/output.py`.

**Imaging conditions** — every processed frame (camera and Directory Watch) feeds `services/imaging_conditions/` right after star detection; `GET /conditions` (same bearer auth as the control routes) serves `imaging_ok` with hysteresis, reason codes and the measurements, and `/status` carries a compact `conditions` block. The clear-sky star baseline is learned on each installation — never ship star counts. Settings live on the **NINA & ASCOM** page (with the control API token, the NINA plugin card and the roof SafetyMonitor / safety file — config keys unchanged); the Sensitivity preset (`presets.py`) only sets the threshold keys, which stay authoritative, and Balanced must equal `DEFAULT_CONFIG`. Live Monitoring's conditions panel (opened by the IMAGING tile) reads `conditions_history` and `nina_checkin`. See `docs/wiki/Imaging-Conditions.md`.

**NINA target** — the NINA plugin POSTs the running sequencer target to `/nina/target` (same bearer auth as the control routes); `services/allsky/render_target.py` draws it on the all-sky overlay, and the pushed target goes stale after `nina_target.stale_after_s`. Headless mode accepts the POST but renders no overlay.

## Project structure

```
PFRSentinel/
├── ui/                         # PySide6 + qfluentwidgets UI
│   ├── main_window/            # FluentWindow + QStackedWidget navigation (window.py, capture.py, output.py, lifecycle.py,
│   │                             alpaca_camera.py — Discover / Test Connection wiring and the "Alpaca · not connected" pill …,
│   │                             navigation.py — splitter, sections, the NINA & ASCOM page and the links into it)
│   ├── system_tray_qt.py       # QSystemTrayIcon tray menu (no third-party backend)
│   ├── components/             # Reusable widgets (header, monitoring panel, status indicator)
│   ├── panels/                 # Pages (layout only) — monitoring, capture, output, overlays, timelapse, logs;
│   │                             _zwo_connection_card.py / _alpaca_connection_card.py, _camera_capability_gate.py;
│   │                             nina_ascom_panel.py (plugin, control_api_card, imaging_conditions_card, roof_safety_card),
│   │                             conditions_live_panel.py (beside the preview on Live Monitoring)
│   ├── controllers/            # Business logic — capture, output, overlay, timelapse, ML prediction;
│   │                             alpaca_discovery_controller.py, alpaca_identity.py (identity/ranges kept per address),
│   │                             camera_wedge_recovery.py (wedged-thread + USB-reset recovery, ZWO-only USB steps),
│   │                             nina_ascom_controller.py (control API + roof-safety edits), imaging_conditions_controller.py
│   └── theme/                  # tokens.py, styles.py, accent_themes.py, special_themes.py (seasonal packs), icons.py
├── services/                   # Core processing modules
│   ├── host_platform.py        # OS facts for user-facing wording (labels, SDK filename, file manager)
│   ├── reveal_in_file_manager.py # Explorer / Finder / xdg-open launch, one implementation
│   ├── autostart.py            # Start at login: Task Scheduler (Windows) → autostart_macos.py (LaunchAgent, relaunch on crash) / autostart_linux.py (XDG)
│   ├── launch_command.py       # argv that starts this copy (frozen .app/exe or source) — LaunchAgent, XDG entry, restart waiter
│   ├── app_restart.py          # Self-restart via a detached waiter (PowerShell / sh) once this PID exits
│   ├── font_loader.py          # PIL font resolution: Arial (Windows) → bundled Space Grotesk → DejaVu → default
│   ├── zwo_sdk_library.py      # ZWO SDK library name + search paths per OS; the only place that may name it
│   ├── config.py               # Config class — load/save/merge; re-exports the defaults
│   ├── config_defaults.py      # DEFAULT_CONFIG + DEFAULT_CAMERA_PROFILE (data only)
│   ├── utils_paths.py          # get_app_data_dir() — per-platform app-data root; resource paths
│   ├── app_config.py           # App identity constants + canonical calibration paths
│   ├── logger.py               # Thread-safe queue logger (app_logger singleton)
│   ├── processor.py            # Image overlay engine — dual input: PIL Image OR file path
│   ├── watcher.py              # watchdog FileSystemEventHandler
│   ├── camera/                 # ZWO subpackage — zwo_camera.py (SDK wrapper, BGGR debayer, auto-exposure),
│   │                             camera_white_balance.py (ASI_WB_R/B per mode, low-gain warning),
│   │                             camera_connection.py (SDK init, detection, USB reconnect),
│   │                             camera_calibration.py, camera_utils.py,
│   │                             linux_usb_preflight.py (udev rule + usbfs_memory_mb warnings);
│   │                             backend-neutral session seam: driver.py (ExposureHandle + SdkConstants protocols),
│   │                             session.py (CaptureSession protocol), capabilities.py (CameraCapabilities per source),
│   │                             errors.py, capture_session_base.py (schedule gate, start/stop/join, auto-exposure wrappers),
│   │                             capture_loop.py (shared recovery loop), frame_metadata.py (one metadata builder),
│   │                             session_params.py (config → start params), session_factory.py (build_session per source),
│   │                             camera_source.py (camera_source, alpaca_settings, active_camera_identity, profile key),
│   │                             recovery_wording.py (per-backend recovery lines + WARNING levelling for the shared loop)
│   │   └── alpaca/             # ASCOM Alpaca backend — alpaca_http.py (client, timeouts, error mapping),
│   │                             alpaca_imagebytes.py, alpaca_sensor.py (Bayer phase / flip / MaxADU bit depth),
│   │                             alpaca_discovery.py (UDP 32227 + Management API), alpaca_device.py (native API + zwoasi facade),
│   │                             alpaca_connection.py (per-device lock, UniqueID gate), alpaca_camera.py (session),
│   │                             alpaca_capture_frame.py, alpaca_probe.py (Test Connection), alpaca_protocol.py,
│   │                             alpaca_download.py (deadline + abort-guarded image download, 64 MB JSON cap),
│   │                             alpaca_frame_budget.py (download deadline + watchdog budget from measured link speed),
│   │                             alpaca_shared_camera.py (per-frame reassert, FrameSkipped on another client's image),
│   │                             alpaca_static_info.py, alpaca_transport_text.py (plain-words network errors),
│   │                             alpaca_diagnostics.py (summary facts), alpaca_warnings.py (once / rate-limited warnings)
│   ├── cleanup.py              # Disk space management (files only, never folders)
│   ├── gc_scheduler.py         # GUI-thread cyclic GC (automatic GC off, QTimer-driven)
│   ├── discord_alerts.py       # Discord webhook client
│   ├── weather.py              # OpenWeatherMap API, 10-min cache
│   ├── web_output.py           # HTTP server
│   ├── api_control.py          # Capture-control command logic (pure)
│   ├── api_auth.py             # Control-API bearer auth, Host allow-list
│   ├── web_control.py          # POST /capture/start|stop routes
│   ├── nina_target_store.py    # Thread-safe store of the target NINA is imaging, with staleness
│   ├── alpaca_safety/          # ASCOM Alpaca SafetyMonitor: own RoofSafetyFSM feed → state (stale → UNSAFE) → HTTP + UDP discovery server
│   ├── api_target.py           # POST /nina/target validation + OpenAPI catalog (pure)
│   ├── web_target.py           # The /nina/target route behind the control-API auth
│   ├── imaging_conditions/     # Per-frame "good enough to image?" verdict for NINA: frame_signals (metadata → roof/sky/stars/FWHM),
│   │                             frame_rules (reason codes, all star rules relative), star_baseline (per-rig clear-sky count per Moon bin,
│   │                             stamped camera + frame size + lens, persisted under <app-data>/imaging_conditions/), plate_scale (a1 → arcsec),
│   │                             verdict_hysteresis, conditions_store (thread-safe, stale at read time), feed (the pipelines' one-line hook),
│   │                             presets (Relaxed / Balanced / Strict; star floor 10 points under the cloud line), conditions_history
│   │                             (12 h ring + paused/resumed events, events.json kept 24 h), nina_checkin (last /conditions request, by User-Agent)
│   ├── api_conditions.py       # GET /conditions contract catalog + OpenAPI (pure); the plugin depends on its field names
│   ├── web_conditions.py       # The /conditions route behind the control-API auth
│   ├── nina_plugin_install.py  # Install/update/remove the NINA plugin
│   ├── headless_alpaca.py      # Headless runner's Alpaca branch (open, identity, one-reconnect pause with recovery off)
│   ├── pe_version.py           # Windows PE FileVersion reader
│   ├── timelapse_writer.py     # ffmpeg stdin pipe, time-gated capture
│   ├── timelapse_window.py     # Recording-window maths (sun/fixed/always), local-date aware
│   ├── timelapse_window_forecast.py # Projected recording window for the Timelapse Status card
│   ├── capture_schedule_window.py # Capture schedule that follows the timelapse window ± margin
│   ├── ffmpeg_utils.py         # Shared ffmpeg detection
│   ├── diagnostics_bundle.py   # Support ZIP: logs + redacted config + frames (pure)
│   ├── frame_static_score.py   # Is a frame scene or only sensor noise? Reporting only — gates nothing
│   ├── raw_frame_export.py     # Cached frame → Bayer FITS + unprocessed PNG + JSON
│   ├── performance.py          # get_process_memory(): working set vs private bytes vs peak
│   ├── resource_monitor.py     # Rate-limited memory/CPU log line + growth warning (every build) + tracemalloc hotspots (dev builds); fed by ui/controllers/resource_monitor_controller.py
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
├── scripts/dev/alpaca_camera_sim.py # ASCOM Alpaca camera simulator (HTTP + discovery; dev only) — scripts/dev/README.md
├── main.py                     # Entry point
└── version.py                  # __version__
```

## Architecture patterns

### Data flow
- **Watch mode**: `watcher.py` → file stable → parse sidecar → `processor.py` → `_push_to_output_servers()`
- **Camera mode**: `session_factory.build_session` → `camera/zwo_camera.py` (ZWO SDK) or `camera/alpaca/alpaca_camera.py` (Alpaca HTTP) → shared `capture_loop.py` → debayer → PIL Image + metadata → `camera_controller.py` → `processor.py` → outputs

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
- **Phase 2**: Sky conditions (93.2%), stars (93.5%), moon (94.1%) — ImageNet-pretrained ResNet18 trunk since 2026-09-27 (`ml/sky_backbone_net.py`, trained by `ml/train_sky_backbone.py`); the from-scratch CNN in `ml/train_sky_classifier.py` is kept for comparison and scores ~82% on the same split
- **Phase 3**: Dev-mode integration that saves calibration JSON + FITS per frame
- **Phase 4** (future): Stretch recipe prediction
- All inference is local via ONNX. Production interface: `services/ml_service.py` (`MLService.analyze_image`, `get_ml_service()` singleton), on whenever `ml_models.enabled` is set, in every build. `ui/controllers/ml_prediction.py` is a separate dev-only loader for the calibration JSON export, gated by `is_dev_mode_available()` along with `dev_mode_utils.py`, the Dev Mode card and the resource monitor's `diagnostics.memory_trace` option (the monitor itself runs in every build).

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
./build_sentinel_macos.sh         # macOS: PFR Sentinel.app + .dmg (Apple silicon, ad-hoc signed)
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
against that branch. It reads the log of the attempt the event names and stops
if a newer attempt already exists, so a manual re-run started in the same minute
no longer hands it an empty log (PR #113, 2026-09-29). CodeQL and Dependabot are enabled at the repo level.

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

Linux gets `scripts/ci/check_spec_parses.py` instead of a build: it
executes the spec with the PyInstaller classes stubbed, which catches a
Windows-only import or a one-OS `datas` entry in seconds. macOS gets a real
dev build (`macOS dev build`, Apple-silicon runner) that runs
`build_sentinel_macos.sh` — the same script a local Mac build uses — and
uploads the `.dmg`. It lays the `.dmg` window out with `dmgbuild` (`scripts/ci/build_dmg_macos.py`: a background showing the drag and the one-time quarantine command, plus a "Read me first" file to copy it from — artwork in `dmg_background.py`). That script stages the ZWO SDK with
`scripts/ci/stage_zwo_sdk_macos.py`: the arm64 slice of the committed universal
`libASICamera2.dylib` (ZWO's official build, updated with
`scripts/update_zwo_sdk.py`), with Homebrew's libusb beside it, its libusb
reference rewritten to `@rpath` with an `@loader_path` rpath, and both
re-signed. The committed file is never bundled as-is: its arm64 slice links
libusb at an absolute Homebrew path a user's Mac may not have.
The job is not required by the ruleset and the `.app` is not notarized.
`publish-dev` attaches its `.dmg` to `dev-latest` as
`PFRSentinel-dev-unsigned-macos-arm64.dmg` beside the Windows installer, and
the release notes and Discussions comment link it (`dev_build_notes.py --mac`).
A failed macOS build doesn't block the Windows publish: it goes out without
the `.dmg` and without the link. The rest of macOS packaging is issue #41,
Linux packaging #163. PyInstaller is pinned in `requirements-build.txt`, kept
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
| `test_camera.py` | 14 | `zwo_camera` — SDK, config, debayering |
| `test_camera_hardware.py` | 4 | real camera on whichever OS the SDK loads — detect, open, serial, one RAW8 frame; a library that is found but won't load fails instead of skipping (all `requires_camera`) |
| `test_camera_auto_recovery.py` | 20 | `camera_auto_recovery` off — one plain reconnect, no ladder/USB/SDK reset (`camera_reconnect`), capture loop stops instead of retrying (cold-open quirk still absorbed, handle released on the fatal exit), controller schedules no restart or USB reset; on = unchanged |
| `test_discord.py` | 32 | `discord_alerts` — webhooks, embeds (mocked) |
| `test_image_output.py` | 18 | `processor` — overlays, stretch, output formats |
| `test_output_crop.py` | 59 | `output_crop` — box normalisation/clamping/evenness, centring, fit-to-circle, proportional rescale across frame sizes, PIL crop, metadata round-trip, DEFAULT_CONFIG integration |
| `test_settings.py` | 11 | `config` — JSON save/load, merge, defaults |
| `test_webserver.py` | 13 | `web_output` — HTTP server, ETag, status JSON (`requires_network`) |
| `test_ml_classifiers.py` | 6 | `ml.roof_classifier` / `ml.sky_classifier` + the dev-only `ui/controllers/ml_prediction.py` loader — ONNX load + inference smoke tests (`requires_ml_models`) |
| `test_api_auth.py` | 43 | `api_auth` — bearer compare, Host allow-list, token minting, redaction |
| `test_api_control.py` | 51 | `api_control` — command validation, idempotency, `wait` semantics, OpenAPI catalog |
| `test_web_control.py` | 33 | `web_control` — control routes, auth matrix, no-CORS regression guard |
| `test_capture_command_bridge.py` | 11 | `CaptureCommandBridge` + headless handler — GUI-thread marshalling |
| `test_diagnostics_bundle.py` | 19 | `diagnostics_bundle` — secret/location redaction, log-age filter, ZIP contents + summary, newest calibration buffer dump under `allsky/` (absent one listed as missing); Alpaca: `alpaca_camera.host` and `unique_id` masked by path (a loopback or empty host kept, `webserver_host` stays visible), `alpaca:` profile keys never leak the identity, the written bundle carries the masked config |
| `test_raw_frame_export.py` | 7 | `raw_frame_export` — Bayer FITS round-trip, unprocessed PNG, scalar metadata |
| `test_zwo_camera_capture_now.py` | 2 | `zwo_camera` — one-shot `request_immediate_capture` wake used by the diagnostics export |
| `test_coordinate_hemisphere.py` | 24 | `coordinate_hemisphere` + `config_migrate` — unsigned DMS is read north/east and the typed text kept under `weather.hemisphere_unconfirmed`, clock-based longitude default, applying an answer signs the decimal and clears the mark, the startup prompt saves on Confirm and keeps the question on Ask me later, the diagnostics bundle redacts the typed text |
| `test_hemisphere_dialog.py` | 4 | `HemisphereDialog` — one radio pair per pending field, defaults preselected, typed text shown (offscreen Qt) |
| `test_update_checker.py` | 12 | `update_checker` — prereleases/drafts and the dev asset are never offered; `-dev` ranks below its release |
| `test_dev_build_notes.py` | 15 | `scripts/ci/dev_build_notes.py` — dev tag stays off `v*`, release/discussion text states the risks and carries the change list; the macOS `.dmg` link appears only with `--mac` (a failed Mac build publishes no dead link), and its name can never pass for an installer |
| `test_dev_build_changelog.py` | 7 | `scripts/ci/dev_build_changelog.py` — PRs since the last release via commits, grouping, delta since the previous dev build, API failure never blocks |
| `test_dev_build_discussion.py` | 9 | `scripts/ci/dev_build_discussion.py` — one thread per release cycle, retire/reopen, never edits a post (GITHUB_TOKEN is refused updateDiscussion), never touches people's threads |
| `test_set_dev_version.py` | 11 | `scripts/ci/set_dev_version.py` — dev version base/counter, Windows FileVersion, spec parses the same way |
| `test_timelapse_window.py` | 13 | `timelapse_window` — sun window on the LOCAL calendar date (real astral, injected zones), fixed fallback, per-day cache |
| `test_capture_schedule_window.py` | 39 | `capture_schedule_window` — timelapse-derived capture window ± margin, gate two-day logic, roof/always fallbacks, labels |
| `test_zwo_schedule_gate.py` | 16 | `zwo_camera` — schedule gate vs legacy HH:MM path, fail-open, `scheduled_window_label` |
| `test_api_status_schedule.py` | 11 | `api_status.build_schedule` — status API schedule block for fixed and timelapse sources, with and without a camera |
| `test_schedule_window_source_ui.py` | 8 | `_schedule_window_source` — window-source rows: load, visibility, signals (offscreen Qt); Interval hint says when the schedule sets the rate |
| `test_timelapse_window_forecast.py` | 26 | `timelapse_window_forecast` — open/next window, inclusive edges, twilight depths on the local night, fixed-time fallback notes, clock-change durations, no log spam |
| `test_timelapse_status_card.py` | 5 | `TimelapseStatusCard` — session line, projected window line, open-video button (offscreen Qt) |
| `test_output_crop_card.py` | 25 | `OutputCropCard` / `CropBoxEditor` / `OutputCropController` — drag/resize/spin clamping, config round-trip, thumbnail cap + active gating, Fit-to-sky (offscreen Qt) |
| `test_watch_controller_crop.py` | 7 | `WatchControllerQt` — the output crop on `img.info` reaches the all-sky preview renderer; `extras` forwarded on `image_processed`; the processor's own metadata (cached gate verdict included) is what the renderer gets, so a suppressed frame is never drawn |
| `test_watch_crop_cache.py` | 3 | `_on_watch_image_processed` — watch mode caches the pre-resize clean frame for Calibrate Now, not the cropped output |
| `test_watcher.py` | 2 | `ImageFileHandler` — `process_image` extras reach the callback |
| `test_headless_runner_crop.py` | 3 | `HeadlessRunner._process_and_save` — output crop after resize, before overlays |
| `test_system_tray_qt.py` | 10 | `SystemTrayQt` — native `QSystemTrayIcon` menu state, show/hide, capture gating, `TrayUnavailableError` leaves the window visible (offscreen Qt) |
| `test_autostart_posix.py` | 25 | `autostart_macos` / `autostart_linux` / `launch_command` / POSIX `app_restart` — LaunchAgent plist relaunches on crash but not on a clean quit, written atomically, never calls `launchctl`, foreign/garbage plist reads as off; XDG entry and Exec quoting; dispatch per OS; the real `/bin/sh` waiter relaunches only after the PID exits (skipped on Windows) |
| `test_stage_zwo_sdk_macos.py` | 7 | `scripts/ci/stage_zwo_sdk_macos.py` — finds ZWO's absolute Homebrew libusb reference (and an already-`@rpath` one) in `otool -L` output, never mistakes the library's own id line, reads every `LC_RPATH`, licence and source are in the repo, refuses to run off macOS |
| `test_dmg_background.py` | 8 | `scripts/ci/dmg_background.py` + `build_dmg_macos.py` — the .dmg window's install steps: 1x/2x backgrounds cover the window with or without the title bar, icons and label plates inside it and clear of the command box, the command shown is the wiki's and the read-me's, dmgbuild settings (app, read-me, Applications link, positions, volume icon), refuses to build off macOS |
| `test_reveal_in_file_manager.py` | 15 | `reveal_in_file_manager` — explorer / `open -R` / `xdg-open` argv per platform, missing path and launch failure never raise |
| `test_zwo_sdk_library.py` | 35 | `zwo_sdk_library` — library name per OS, search order (bundled → per-user `sdk/` → system, multiarch first), versioned `.so`/`.dylib`, symlink stubs skipped, foreign-platform path detection, help text |
| `test_zwo_sdk_loading.py` | 9 | `CameraConnection.initialize_sdk` always hands zwoasi an absolute path and falls back from a stale one; the Start Capture index resolver does the same; `Config` rewrites only another platform's SDK path |
| `test_linux_usb_preflight.py` | 15 | `linux_usb_preflight` — `usbfs_memory_mb` and udev-rule warnings, root exempt, once per process, no-op off Linux, shipped `asi.rules` satisfies the check |
| `test_font_loader.py` | 5 | `font_loader` — bundled Space Grotesk resolves off Windows, Arial never tried there, fallback outcome cached |
| `test_ffmpeg_utils.py` | 15 | `ffmpeg_utils` — PATH first, then winget / Homebrew / distro candidates per platform; winget probe never spawns off Windows |
| `test_windows_only_ui.py` | 5 | `FfmpegInstallCard` copyable install command off Windows; `MissingCameraNotice` hides Revive where there is no USB reset API (offscreen Qt) |
| `test_update_dialog_platform.py` | 3 | `UpdateDialog` — download hidden and GitHub made primary off Windows; installer launch is a no-op there (offscreen Qt) |
| `test_special_themes.py` | 17 | `special_themes` — pack patches accent + neutrals and re-derives aliases, switching off leaves nothing behind, status colours untouchable, neutrals no brighter than Sand, every pack glyph exists in the bundled MDI font, font bundled in the spec; nav rail swaps/restores icons, every sprite state paints in both painter sets, `AppearanceCard` chip/swatch signals and chip restyle on load, `AppBar.show_sending` hold cancelled by a newer frame (offscreen Qt) |
| `test_scroll_safe_spinbox.py` | 10 | `ScrollSafeSpinBox` / `ScrollSafeDoubleSpinBox` — wheel ignored unless the box holds click/Tab focus; page-handed focus never arms it, a click on the child line edit does (offscreen Qt) |
| `test_frame_static_score.py` | 18 | `frame_static_score` — pure noise scores 0.25 at any brightness / bit depth, dim scenes stay scenes, gradients and LEDs don't fool it, unmeasurable input is `None`, full-res plane never copied |
| `test_ml_service.py` | 11 | `ml_service` — `median_lum` bit-depth normalisation, per-frame memory ceiling (`requires_ml_models`), static score reported in results, never changes the roof reading, logged on transition only; time context from the shared service with the configured location cached, daytime defaults when it fails |
| `test_status_strip_static.py` | 16 | `StatusStrip` — static frame keeps the roof reading and marks it unreliable, Sky reads "Too much static", clears on the next good frame even when that frame carries no roof or sky verdict (classifier off, prediction failed, no ML results), a no-verdict frame between good ones still keeps the last reading, warning glyph exists; observable-sky gate reasons take the Sky tile ("No stars detected", "Exposure too short", also without ML results), Closed roof and static still win, an observable frame restores the reading (offscreen Qt) |
| `test_status_strip_fit.py` | 25 | `status_strip_fit` + `StatusStrip` on a narrow window — values elide only at a word boundary (a dangling " ·" dropped), word-step widths, nothing changes when everything fits, low-priority tiles fold to their icon in rank order ("Not configured" Roof/Sky/Weather, then Web, Discord, Weather, Seeing) before any value elides, an elide tier takes turns a word at a time and IMAGING elides last; widget: 1500 px unchanged, IMAGING whole or word-elided with the full text in its tooltip at 1100 / 960 / 900 px in the paused and not-configured states, Roof/Sky never fold while they hold a reading, a folded tile's tooltip names its value above its own, widening restores the layout, a longer value refits without a resize, an unshown strip is never trimmed (offscreen Qt) |
| `test_image_processing_panel_stretch.py` | 4 | `ImageProcessingPanel` Auto Stretch rows — Target Median slider reaches the engine floor, Dark Threshold enabled only with Dark Scene Color Fix (offscreen Qt) |
| `test_allsky_label_stability.py` | 52 | `label_stability` + renderer — 15-frame sky-mask vote rides out an exposure change yet still adopts a lasting one, keep fraction (Schmitt trigger: sky at half, kept down to a quarter, a pixel on the line no longer flips each frame, a restart after a stale run has no standing sky), 40 misses keep the vote and mark it stale, a real mask after a stale run replaces it, sparse (3–9 detection) frames only add sky, Moon glare left out of the denominator, vote kept on the reduced grid and matching a recount, sticky top-N with an absent incumbent's slot held for the drawn-label hold (never over budget, reset forgets), slot memory; the equipment map vetoes labels, a `SAME_CAPTURE_KEY` reprocess never advances the vote, direct renders never teach the map; sky mask unchanged when the same sky is exposed less, equipment-edge stars still claim less sky |
| `test_label_persistence.py` | 31 | `label_persistence` + `label_candidates` — issue #144: a label draws after 2 eligible frames (a one-frame newcomer never; the first frame after a reset draws at once), held 10 frames after it stops being eligible and the slot held as long, fade-in and fade-out alphas monotonic, a short drop drawn at full strength, a lasting drop needs the debounce again, a label whose slot went to another crossfades out over the fade frames, a reprocess (`SAME_CAPTURE_KEY`) advances neither the drawn set nor the top-N pick, reset and a gate-withheld frame forget it; the layers draw exactly the persisted UIDs on an obstructed plane, faded by their alpha; ranking and drawing share `LABEL_MIN_ALT_DEG` (an object at 7° no longer takes a slot) and a switched-off layer takes none; end to end, held labels drawn for the hold over a map that turned to equipment, then gone (`slow`) |
| `test_allsky_star_sightings.py` | 23 | `star_sightings` — tolerance from the model residual (floor, cap, frame scale), one sighting never shows a label and two in a row do, a gap breaks the pair, held 4 frames after a steady star and 3 after a brief one, an intermittent star never blinks, decay with no detections, patch passes the label test without writing the caller's plane, a reprocess reads without advancing, the equipment map has the last word, targets follow the layer toggles and skip the Sun and Moon; renderer sights catalogue stars drawn at their pixels and `reset_label_stability` clears them (`slow`) |
| `test_obstruction_map.py` | 27 | `obstruction_map` — converges to a synthetic silhouette within `HEAL_FRAMES` and forgets a moved scope in ~2×, no-detection / unobservable / sparse frames teach nothing negative, Moon disc excluded, reach keeps star gaps unknown, size and crop stamps start a new map and a moved crop reads None, npz round-trip, truncated / garbage / foreign files load as unknown without raising, concurrent saves leave one valid file, an update during a write stays dirty, 10-min save throttle, cached read-only full-resolution mask, update timing logged with a 5× ceiling (`slow`) |
| `test_equipment_map_wiring.py` | 11 | Reset Equipment Map button confirms then emits its action; `AllSkyController` loads the map at startup, resets it (and Reset Calibration clears it too), saves at capture stop and shutdown; `set_model` / `clear_model` / capture start forget label memory (offscreen Qt) |
| `test_observing_window.py` | 57 | `observing_window` — twilight gate, roof gate and its opt-out, roof must read Closed on two consecutive frames once an Open frame has been seen (a cold start on a Closed roof suppresses at once, N/A establishes nothing, a day of twilight frames forgets last night's Open state, reset forgets it too), several callers in one frame count once, a reprocess of the same capture never advances or clears the count, nor does a caller with no roof verdict (Watch mode's overlay render); issue #93 rules in order: static blocks, exposure floor (0.06 s blocks, 10 s passes, absent passes, 0 disables, short frames leave the roof streak alone), Open + 3 no-star frames blocks and 2 do not, ML off blocks too, `stars_visible` vetoes a low count, recovery needs 2 frames at 2× the floor or 2 at the floor with `stars_visible` (never below the floor, never without the verdict), a Closed streak still blocks with plenty of stars, blind callers and reprocesses neither count nor clear, reason cached with the verdict, one INFO line per transition, `_ML_RESULTS` untouched so the ASCOM FSM still certifies SAFE and the meteor roof rule sees Open |
| `test_sky_evidence.py` | 54 | `sky_evidence` — exposure parsing for every format (s / ms / µs / m / bare / numeric; unreadable is None and never blocks), `TelemetryBar` formats through the shared parser, ML signals from `_ML_RESULTS` or the Watch-mode tokens, evidence fields with the sky circle back in frame pixels, every input resampled to one fixed 900 px plane (the same sky at 3552 / 2131 / 1776 px counts within 15 %, a 256 or 750 px frame is interpolated up), the cheap count agrees with `detect_stars` on a star field and stays near 0 on an empty disc, noise-only and lit-ceiling count 0, numpy and PIL count alike, `ignore_rects` take burned-in text out, scan skipped on a short exposure / static frame / daylight, a failed scan leaves None, sky circle cached per frame size and refreshed, per-frame budget (`slow`: p50/p95 over 50 frames at 3552 px logged, only a 5× sanity ceiling asserted — the 30 ms budget is verified from the measured figures, kept in the docstring), no full-resolution plane kept; real reference-rig data when present: 26 night community frames tabulated against NINA's roof state and 115 library frames against `library.db` (closed roof 20–80, open sky 206–445 on the plane; burned-in text does not register; the default floor of 100 separates them), the Sep 17 23:59 closed-roof 13 s frame blanked by the no-stars rule |
| `test_allsky_settings_config.py` | 12 | `AllSkySettingsPanel.get_config` merges over the loaded dict (H11): every `DEFAULT_CONFIG['allsky_overlay']` leaf round-trips, keys without a control survive an edit, unknown keys carried through, old configs get the gate defaults, the two gate spin boxes load and emit, the Label size combo loads and emits its preset and an unknown preset reads as Large, the Show labels behind equipment toggle loads and emits and an old config reads it as off, the Messier and NGC Label text combos load and emit each layer's `label_style` and an old or unknown style reads as the common name, no aliasing of the loaded dict (offscreen Qt) |
| `test_object_label_text.py` | 17 | `object_label_text` — issue #144 Messier / NGC label text: the common name alone (the default for both layers), name and number (the old form), the number alone; no common name falls back to the number in every style, several names show the first, an unknown style reads as the default; `render_messier` draws the chosen style (number narrower than name narrower than both) and a missing style as the common name |
| `test_allsky_point_labels.py` | 9 | planet and Moon labels placed like star labels — every star and planet reserved first and planets named before stars, so a planet's name sits right of it and no star's name covers a planet (the old order pushed it off or lost it), renderer call order; `labels_behind_equipment` — the visibility plane is the whole sky disc when on (never past it), the vote's obstruction holds when off, the vote is still fed |
| `test_allsky_moon_label.py` | 23 | `moon_label` + Moon placement — glare centred on the saturated core, not the prediction (30 px out), radius past the core and inside the halo, same answer at 4× the frame size, no core / a bright star / a core outside the search radius / an off-frame prediction is no glare, capped at a quarter of the sky radius, full-resolution budget; every label slot clears the glare, the Moon's name lands right of it and other labels are kept out, the Moon's name has a dark outline and a planet's does not; a seen Moon needs no visibility test and keeps its top-N slot on an obstructed plane |
| `test_allsky_label_size.py` | 16 | `label_size` — five presets 2 base px apart with Large the unchanged default, floor, unknown preset reads as Large, every layer (`nina_target` included) shifted by the same offset without mutating the caller's dict, `render_allsky_overlay` applies it before any layer reads its `label_size` |
| `test_fits_writer.py` | 21 | `fits_writer` — built-in writer read back by real astropy for uint8/uint16/int16/float32/float64 and channels-first RGB, header cards with comments, 2880-byte blocks, parity with the astropy path, a hollow `astropy.io.fits` (the frozen app) detected by the missing class and the dev-mode raw/luminance writers still producing files |
| `test_allsky_guided_hints.py` | 9 | `guided_hints` — three anchors place the other bright stars within a click-snap, capped to the brightest, mis-identified or rotated anchors withhold the hints with a reason, a frame with almost no detections never accuses the anchors |
| `test_sky_circle_crop.py` | 11 | `sky_circle_crop` + guided path on a wide frame (the 1920x1080 ASI662MC rig): a frame past 1.2:1 crops the circle, square and unknown sizes do not; the true a1 765 is rejected against the r=448 circle read as the horizon and accepted when cropped, the lens-polynomial gate still applies; hints outside the circle kept within the frame; `detect_stars(region_radius=)` finds stars outside the circle and drops one at the frame edge |
| `test_allsky_guided_residuals.py` | 3 | `guided_calibration` — per-anchor residuals on the solved model (renamed / excluded) and on the `CalibrationError` of a failed solve |
| `test_allsky_calibration_attention.py` | 18 | `calibration_attention` + `CalibrationService.attention_changed` — caution only when refinements keep failing AND the saved model can't be confirmed; a healthy model stays silent; cleared by a new model or a reset; an incumbent at chance level in consecutive runs is `misaligned` whatever the failure count or anchor verdict, never for the guided solve itself but yes for a joint fit that inherited its stamp |
| `test_allsky_calibration_fit_merit.py` | 23 | `calibration_fit_merit` + `FisheyeModel.final_tol_px` / `chance_ratio` — the #93 saved model (606 matches, RMS 10.9 px on 16.5 px, 1.04x chance) is not credible and rates Preliminary, the #10 rig's genuine fit stays Excellent, unknown fields never fail a model, guided exempt, save/load round-trip, legacy file loads as unknown, `dataclasses.replace` keeps the fields, the joint fit records the ratio it was gated on; only the user-anchored solve is exempt, a guided-stamped joint fit is judged and capped |
| `test_calibration_service_chance_streak.py` | 22 | `_RefineWorker.incumbent_scored` + `CalibrationService` — score rides its own signal before the result and still arrives when the refinement fails, scored on the run's frames at the final tolerance; at a known site only the dark frames reach the scorer (the reporter's #105 dusk buffer arrives empty and gives no verdict, a dark buffer is scored whole, no site scores every frame); two chance-level runs drop the badge to Preliminary on `badge_quality_changed` (never `quality_upgraded`) with the measurement as the note and raise `misaligned` with anchor health None, status says "matches at chance level", one credible run clears, None is not a strike, the file is never rewritten, stale generation ignored, new / loaded / admitted model resets the streak, verdict reaches `should_replace` (offscreen Qt) |
| `test_refine_seed_resolution.py` | 5 | `_RefineWorker` — the incumbent seeds the joint fit in the buffer's resolution: a raw-resolution guided model (3552 px on the reference rig vs a 2628 px buffer) is scaled with `model_in_frame` (cx/cy/a1/a3/a5, provenance kept) without rewriting the incumbent, a seed already at the buffer's size or without a recorded size passes through, a cold start stays seedless (offscreen Qt) |
| `test_allsky_incumbent_chance.py` | 27 | `incumbent_chance` — the incumbent scored against a synthetic #93 buffer: random projections read ~1x chance at RMS tol/sqrt(2), the true model far above it, one match pass under the 1 s budget, cloudy guard (median detections) returns None, model rescaled into the buffer's resolution, matcher failure is no verdict; two-strike streak, None is neither strike nor clearance, one credible run clears, badge cap, status text; a strike is under `DISCREDIT_RATIO` 1.5 and clearing needs 2x: the #105 reporter's Sep 29 night (1.81-2.33x) is never discredited, a score between the bars breaks a run of strikes but doesn't clear, #93's 1.04x and the reference rig's Sep 28 triangle fit (1.01-1.15x) still are |
| `test_dark_sky_frames.py` | 12 | `dark_sky_frames` — only frames with the sun at or below -15° are scored: the #105 reporter's dusk buffer (gate opened at sun -8°) and the two runs that discredited the model score nothing, a dark buffer passes unchanged, a buffer straddling twilight keeps its dark tail, under 5 dark frames scores nothing, no site / no sun altitude fails open, a missing frame time is never read as "now" |
| `test_calibration_store.py` | 5 | `calibration_store` — a guided solve is kept as `allsky_calibration.guided.json` beside the calibration, an automatic fit never is, the #105 Sep 29 sequence (guided save then two automatic saves) leaves the solve in the copy while the one-step backup has lost it, a failed copy is a warning, diagnostics path matches |
| `test_calibration_history.py` | 16 | `calibration_history` — record / list / load round trip with source, provenance, quality, chance ratio and the replaced model's stamp, newest first, the #105 Sep 29 sequence (guided then two refinements) keeps the guided solve, guided and automatic retention counted apart, files-only pruning (a folder is never removed), unreadable entries skipped, path-like ids refused, a failed write is a warning; `save_with_backup` records each automatic save and not a re-stamp, a restore save backs up but keeps the model's own stamp; the diagnostics bundle lists every entry |
| `test_calibration_history_restore.py` | 12 | `CalibrationHistoryController` + `CalibrationHistoryDialog` — restore puts the guided solve back live (file, backup, guided copy, `restored` entry, `adopt_model` with the status line) and can itself be undone, a missing entry changes nothing; dialog rows newest first with guided starred, Restore selected confirms then asks for that entry, Restore last guided picks the newest guided solve, disabled with none, failure shown and success closes; the Lens Calibration card button; main-window restore end to end; the service names refinement and escape saves (offscreen Qt) |
| `test_guided_calibration_session.py` | 20 | `GuidedCalibrationSession` — passing solve held unsaved, failure rows name the suspect (one, not all, on a wrong-basin failure), stale hint results dropped and superseded or closing suggestion solves cancelled, every request is answered even when the handler raises; `commit_guided_calibration` reports a failed file write, waits for Calibrate Now and keeps a guided copy beside the file (offscreen Qt) |
| `test_guided_calibration_wiring.py` | 2 | `_open_guided_calibration` — the real main-window wiring releases the dialog and both prep frames on cancel and on save (a closure over the dialog leaked them) (offscreen Qt) |
| `test_allsky_guided_dialog.py` | 37 | `GuidedCalibrationDialog` — frame gets the room, stays open through solve / failure with every star kept and the suspect selected, review before save, a refused save can be retried without solving again, suggested names, confirm before discarding, Stretch slider (`DisplayStretchSlider`) hidden without a `DisplayStretch` and otherwise re-renders after the settle timer keeping the view and reporting the strength; Exclude / Include selected keeps the star listed and orange but out of `solve_requested`, `hints_requested` and the Solve count, the flag survives edits and a failed solve, result rows land on their anchors around an exclusion, solver-excluded / renamed rows show their state and residual, Remove works on an excluded row, the excluded star's prediction is drawn and dropping it points at Include (offscreen Qt) |
| `test_allsky_guided_anchors.py` | 10 | `allsky_guided_anchors` — solve tuples skip the user's exclusions in order, result rows matched by name around an exclusion (never shifted), clearing solve marks keeps the exclusion, row text / marker state / colour per verdict (tick, unsnapped, excluded by you, left out, renamed with residual, off image) |
| `test_star_pick_canvas.py` | 14 | `StarPickCanvas` — clicks in original image pixels at any zoom, wheel zoom about the cursor, clamped pan, drag never picks, picking can be switched off, view cached at the display pixel ratio, `replace_image` keeps zoom and pan for a re-stretched frame (offscreen Qt) |
| `test_remember_display_stretch.py` | 4 | `AllSkyController.remember_display_stretch` — saved under `allsky_overlay`, other keys kept, announced through `settings_changed` so the panel's snapshot is refreshed, unchanged value neither saved nor announced, clamped and rounded (offscreen Qt) |
| `test_display_stretch.py` | 7 | `display_stretch` — slider middle is the detector's p1–p99 stretch, log-linear clip fraction to 0.01 % / 10 % at the ends, harder is brighter and saturates more, the linear frame is never touched, a flat frame renders black |
| `test_time_context.py` | 22 | `time_context` — `is_astronomical_night` from sun elevation at absolute instants: flips at astronomical dusk/dawn, never night in the afternoon at a western site (the date-clamp bug), same flag from any host zone, high-latitude summer and polar day never raise, no-location clock fallback, per-day sun-times cache, `ui/controllers` shim re-exports |
| `test_allsky_quality_badge.py` | 18 | `QualityBadge` amber "unconfirmed" / "check alignment" state, panel caution text, controller status line and badge level after a guided save; the tooltip note for a capped chance fit reaches the badge from the controller's `quality_note_changed` before the level; a live verdict (`badge_quality_changed`) shows the capped level, the measurement and the saved rating (offscreen Qt) |
| `test_frame_catalog.py` | 3 | `frame_catalog` — `above_horizon_stars` against the scalar per-star loop it replaced: same star objects, exact alt/az, same order and tuple shape at a mid-latitude, a southern and a polar site |
| `test_allsky_buffer_dump.py` | 33 | `buffer_dump` + `frame_catalog` — dump/load round trip keeps aware datetimes, float detections and the recomputed catalogue list, site rounded to 0.01°, extras on the model survive, image arrays on a frame never reach the file, newest 5 kept (files only), exhaustion dumps once, basin escape dumps in a dev build only, `dump_now` on an empty buffer is None, controller status line and panel button (offscreen Qt); `library_to_buffer.py` on a synthetic two-frame library (closed roof skipped, file-name times without a db, `--ignore-rect`) |
| `test_track_coherence.py` | 10 | `track_coherence` — progression vs scatter of a track regressed on time; Polaris coherent, a jittering light static; the measured separation behind `COHERENCE_MIN_RATIO` at the reporter's plate scale |
| `test_static_lights.py` | 14 | `static_lights` — pier lights found across a night and stripped from both pools, Polaris and a slewing light never listed, chance hits on a dense pool are not lights (presence rule), duck-typed equipment map, `clean_pools` on buffer + ring |
| `test_detection_filters.py` | 9 | `detection_filters` via `detect_stars` — streak and ignore-box rules on drawn frames, the dark-neighbourhood rule (off by default) drops rim glints and keeps a dark sky's stars |
| `test_frame_ring.py` | 10 | `frame_ring` — one frame per 10 min, 36 deep, a day gap or clock step starts a new ring, snapshots share the dicts |
| `test_exposure_midpoint.py` | 9 | `exposure_midpoint` — SDK start + half exposure, FITS `DATE-OBS`, receipt time minus half an exposure, never raises |
| `test_pole_from_rotation.py` | 12 | `pole_from_rotation` — pole within 25 px on both rig presets and the southern mirror with Polaris hidden and a LED by the pole, same answer with three static lights and a slewing light, wrong sky-circle seed recovered, mirror flips `east_left`, `sigma_px` covers the error in ≥ 90 % of 20 seeded runs, random and static-only fields withheld, resource budget (≤ 10 s, ≤ 50 MB on 36 × 200) |
| `test_allsky_pool_hygiene_real.py` | 4 | Reference-rig 2026-09-18 library night (skips without `sample_images/`) — the raw pool reproduces the buffer dump, static strip takes it below the cap, chance expectation falls, the guided seed scores higher against chance |
| `test_orientation_search.py` | 23 | `orientation_search` — roll-vote geometry matches `FisheyeModel`, axis grid and scale scan, the true basin ranks first on all three synthetic presets unseeded and pole-seeded (rotation and Polaris-style poles), mirror hint respected, candidates are distinct basins, an unreachable pole falls back to the unfiltered ranking, centre-offset vote recovers 40–120 px, cold start recovers the reporter preset end to end, resource budget (≤ 30 s unseeded / ≤ 5 s seeded, 5× ceiling), full-resolution cold-start timing logged (60 × 200, seeded and unseeded; `PFR_FULL_ACCEPTANCE=1` only) |
| `test_joint_fit.py` | 11 | `joint_fit` — moved names re-exported from `multi_calibrate`; the measured-pole pseudo-observation is additive (a vanishing term changes nothing), leaves a correct seed alone, holds a 6°-rolled seed within 3σ, a tight sigma pins the basin (the term is live), an off-image pole is charged not crashed; `pole_constraint_for` drops the pole against a guided seed that contradicts it; the seed is never mutated |
| `test_pole_tolerance.py` | 16 | `pole_tolerance` — 3σ + radial allowance, floor near the centre, flat 140 px without a sigma, the reference night's margins, a pole near the centre gated tighter than the flat gate; `PoleConstraint` hemisphere; `validate_pole` uses the calibrated tolerance, a caller's own, and legacy estimates without the field |
| `test_jev_review.py` | 19 | `jev_review` + `jev_label_audit` + `ReviewTab` — Jev block from the systemone answers, stored without touching labels, state / report rows / corroboration read 'True'/'False' strings through `to_bool` (a closed roof is never sent as open), report judges sky only where the human roof is open (same rule as the tab), response cache locked across 8 writers and tolerant of a half-written line; roof / sky conflicts vs the human label (sky only on open-roof frames with a sky label), corroborated only when every other source sides with Jev, corroborated ranks above confident, panel text and banner; Review tab Jev filters, columns and ordering, labeling-tab panel + banner with the form left alone, no per-item column auto-resize (offscreen Qt) |
| `test_allsky_pole_constrained_real.py` | 5 | Reference-rig 2026-09-18 library night (skips without `sample_images/`) — rotation pole found at the guided plate scale and inside the calibrated gate with a ×3 margin, pole-seeded search under budget, pole-seeded cold start lands in the guided basin (plan §0.7), the pole term is neutral on a seed already in the basin |
| `test_resource_monitor.py` | 15 | `resource_monitor` + `performance.get_process_memory` — first sample logs, quiet app only on the heartbeat, a 64 MB private-bytes step or a reason earns a line, CPU% = process time / wall / cores, sustained rise warns once and re-arms after another threshold of growth, a spike or the step into capturing never warns, snapshot is plain JSON, real Win32 / `/proc` counters, `MemoryTrace` hotspots and growth |
| `test_resource_monitor_controller.py` | 12 | `ResourceMonitorController` — every gauge read from a stand-in window (all-sky buffer/ring, meteor stack bytes, queues, overlay cache, reprocess cache, web snapshot), missing or raising subsystems left out, line/warning routed to the logger, sampling failure is DEBUG only, interval and `memory_trace` follow `diagnostics.*`, `memory_trace` refused outside dev builds, started by the window in every build (offscreen Qt) |
| `test_timelapse_always_rollover.py` | 10 | `timelapse_window.observing_night` + writer — Always On rolls over at local noon (the library's night cutoff), never at midnight: a 17:41→07:26 night is one file, 11:59→12:00 starts the next, a 02:00 restart is named for the evening before; window modes keep calendar-date names |
| `test_camera_white_balance.py` | 18 | `camera_white_balance` — manual writes both gains (clamped, auto off) and warns below 10, ASI Auto writes the camera's defaults with auto on and never `ASI_AUTO_MAX_BRIGHTNESS` (falls back to the defaults if auto is refused), gray world neutral; `ZWOCamera.set_white_balance_live` writes under `sdk_lock` and keeps the reconnect values in step, `apply_camera_settings` pushes the profile's R/B, capture start passes `wb_mode` (2026-11-09 green ASI662MC); slider warning (offscreen Qt) |
| `test_timelapse_frame_bytes.py` | 5 | `TimelapseWriter._process_frame` — `Image.tobytes()` matches the old `np.array(convert('RGB'))` bytes for RGB / RGBA / L / I;16 input; an RGB frame is never `convert`ed |
| `test_settings_panel_units.py` | 6 | `SettingsPanel` Weather Units — Imperial / Metric saved from the combo's item data (issue #125), the saved choice survives a restart, a value the combo doesn't offer reads as Metric, the Test button passes the selected units to `WeatherService` (offscreen Qt) |
| `test_weather_service_rebuild.py` | 6 | `_init_weather_service` + `FileWatcher.set_weather_service` + `ImageProcessorWorker` — a rebuilt `WeatherService` reaches a running watcher and its handler (cleared when weather is unconfigured, no-op without a watcher), the worker reads the main window's current service per frame instead of a start-up copy (offscreen Qt) |
| `test_youtube_night_stats.py` | 19 | `youtube_night_stats` — the night a timelapse covers from the library (automatic upload: only frames inside the recording window; Upload latest: the night up to the file's mtime), sensor temperatures in the saved weather units (°C / °F / K, unknown reads as metric), mixed roof / gap / cloud-cover wording, a night with no sky signal leaves those fields empty, a window across local noon takes the night with most frames, empty or missing library and a raising index render every placeholder empty, the publisher hands the uploader the night and survives a failing library provider, the controller reads the index off the main window |
| `test_youtube_templates.py` | 7 | `youtube_config` templates — night placeholders known to `unknown_template_fields` and empty without stats, `{recording}` filled for an automatic upload and empty for Upload latest video (raw `{frame_count}` / `{duration}` kept), default description equal in `config_defaults`, a manual upload posts one line with no night and first line + one blank line + night line with one (`squeeze_blank_lines`) |
| `test_alpaca_safety.py` | 22 | `alpaca_safety` — state reads UNSAFE until fed and again once frames stop, the feed's own FSM needs two confident Open frames (and two Closed to leave), low confidence / no ML result unsafe, nothing fed while off and a fresh UNSAFE start after re-enabling, configured `min_confidence`; Management API lists one SafetyMonitor with a stable UniqueID, IsSafe false until connected and when stale, case-insensitive parameters, ClientTransactionID echoed (garbage reads 0), 400/404 for bad device / value / path, unknown members NotImplemented, DeviceState, interface V3; service starts once, restarts on a port change, stops when disabled, survives a failed bind, binds to loopback unless `bind` says otherwise (CodeQL `py/bind-socket-all-network-interfaces`); real HTTP + discovery reply (`requires_network`) |
| `test_nina_target_store.py` | 12 | `nina_target_store` — injected clock, `set` returns changed (same target again is not a change, a different one is), `current` is None past `max_age_s`, an identical heartbeat extends the life, null clears and counts as a change, snapshot shape and age, `clear` forgets target and time, singleton shared until reset, JSON field names round-trip through `as_dict` / `from_dict`, concurrent set/read smoke never tears |
| `test_api_target.py` | 95 | `api_target` — POST /nina/target validation: RA/Dec edges, rotation normalised, J2000 only (any case), FOV both-or-neither and capped, name control characters stripped and truncated to 64 chars, source defaulted and capped, NaN / Infinity / huge-integer literals and objects are 400 not a crash, non-object / non-UTF-8 / invalid JSON bodies, null target clears, unknown keys ignored, oversize body 413; `build_result` fields match the catalog; OpenAPI and docs HTML carry the route only with `control_path` |
| `test_web_target.py` | 35 | `web_target` — POST /nina/target route on a fake handler: auth matrix (401 missing / wrong / non-Bearer token, 403 Host not allowed, 503 `control_disabled`, no configured token fails closed), Host checked before the token, no CORS header on success or rejection, token never in a response or log, 413 on oversize `Content-Length`, bad body is 400 and leaves the store untouched, `changed` true then false on a repeat (INFO for a change, DEBUG for a heartbeat), null clears, route matching, `web_control.route_post` dispatches the path and still rejects unknown ones, huge integer coordinate is 400 not 500, a refused body (400 / 413) is one WARNING in Sentinel's log without the token |
| `test_allsky_render_target.py` | 73 | `render_target` — gnomonic FOV corners on the tangent plane (diagonal, half width / height, quarter turn swaps axes, angle mod 180, samples per edge); `place_target` puts a zenith target at the centre, scales the field with `a1`, measures the angle east of north, precesses J2000 to the date before alt/az, drops the box when any sample is below the horizon or the field is under `MIN_FOV_PX` (reticle alone), draws nothing below the horizon / off image / layer off; `locate_target` names why nothing was placed and why a reticle stands in for the box; the box alone with nothing inside it (no centre cross) vs ring-and-ticks reticle; the line follows the name's weight with `line_width` as its floor, over a dark outline on both sides; the label is reserved first and clears the box, the reticle and the Moon's glare, later labels stay off both; the visibility plane is ignored (drawn over equipment), a failing placement leaves the other layers alone, a star in the target's slot yields; bad colour (logged once) and garbage sizes fall back, `stale_after_s` clamped 30–3600 default 120, null block reads as defaults, label-size preset moves the label; end-to-end render |
| `test_allsky_target_status_log.py` | 10 | `target_status_log` — one INFO line per change in what the overlay does with the NINA target: drawn with a box, a reticle and why, not drawn and why, hidden once stale (logged once while the age grows), a new target or a return to a state is logged again, `forget` on a clear; the preview path logs a stale target once and a cleared one not at all, the renderer reports the placement |
| `test_allsky_target_preview.py` | 15 | `render_allsky_for_preview` — `_nina_target` injected from the store, none / cleared → None, a stale target dropped after the default 120 s, `stale_after_s` read from the layer config and clamped 30–3600 (unusable value never fatal), the caller's config not written, a null block or a raising store loses the target and not the rest of the overlay |
| `test_allsky_nina_target_card.py` | 26 | `NinaTargetCard` — `load` / `values` round-trip the four controlled keys and leave the rest of the layer alone, a fresh card shows the defaults, `load` never emits, each control emits `changed` exactly once, hide-after clamped to 30–3600 (and refused by the spin box itself), only `#RRGGBB` colours the renderer can read are kept, the default colour is one the picker offers (offscreen Qt) |
| `test_frame_metadata.py` | 9 | `frame_metadata` — golden ZWO frame metadata: exact keys, order and values for RAW8 and RAW16 at a fixed clock, unreadable / negative temperature text, camera-property fallbacks, `wb_config` copied, the builder's key list is the golden one (every backend must produce it) |
| `test_camera_session_contract.py` | 165 | `session` / `driver` contracts — `ZWOCamera` and `AlpacaCamera` carry every `CaptureSession` attribute and method and satisfy the protocol, zwoasi `Camera` covers `ExposureHandle` with the constant values it names, loop-owned attributes left to the loop, `stop_capture` takes the connection's `sdk_lock`, gain clamped to the session limits, factory post-construction settings, `read_capture_params` reads the saved profile, the loop prints a session's own troubleshooting steps (ZWO's unchanged) |
| `test_capabilities.py` | 12 | `capabilities` — frozen dataclass, static set per source (unknown reads as ZWO), ZWO shows the USB controls and Alpaca hides them, ZWO hints are the five the loop always printed, ZWO refined by the open connection (RAW16, bit depth), every field set on both static sets |
| `test_camera_source.py` | 20 | `camera_source` — defaults land on new and old configs, unknown source reads `zwo`, `alpaca_settings` typed and bounded, profile key prefers the UniqueID (`alpaca:<id>`, else `alpaca:<host>:<port>:<n>`), ZWO identity is exactly the old lookup, an Alpaca profile survives `prune_bogus_profiles`, same-named legacy profile seeds it, `capture_source_label` (ZWO text unchanged), `camera_ready`, `persist_alpaca_identity` writes once, moves a host-keyed profile to the UniqueID and never overwrites one |
| `test_camera_controller_teardown.py` | 7 | `CameraControllerQt` — Stop pressed while connecting stops the already-running loop (the old `disconnect()` call raised and orphaned it with the USB handle open), a late stop keeps a wedged session for the next start, start tears a leftover session down before connecting; Stop marks the session dying at once, a quick Start after Stop waits for the old thread and is refused ("still shutting down") while it lives on, a joined teardown lets the session go |
| `test_raw16_revert.py` | 2 | a refused RAW16 change unticks the switch on the camera settings widget (the revert used to raise `AttributeError` on the panel), an accepted one leaves it (offscreen Qt) |
| `test_debayer_mono.py` | 13 | `debayer_raw_image` `'MONO'` (8- and 16-bit, grey to every channel, dst buffers honoured) and `'RGB'` passthrough, the four Bayer patterns and the BGGR default unchanged, `build_frame` / rebuild carry both, raw export: RGB gives a PNG and no Bayer FITS, mono a FITS without `BAYERPAT` |
| `test_alpaca_readout.py` | 31 | `alpaca_readout` — INDIGO 2.0's bridge on the ASI676MC (MaxADU 4096 in every mode; "RAW 8" sent 0-255, "RAW 16" sent MSB-aligned 0-65534, ImageBytes 44 bytes short): bit depth read from mode names, only an 8-bit mode moves to the 16-bit mode of the same kind and size (names without a depth never), capture switches it and Test Connection never writes it, a frame above MaxADU adopts 65535 for the session (a later dark frame scaled the same, an 8-bit claim never widened, beyond 16 bits still clipped), the 8-bit-only warning once across a reconnect; a payload short by exactly its header keeps ImageBytes with the bottom of the last column zeroed and one warning, any other undecodable ImageBytes is fetched again as JSON and the session stays on JSON |
| `test_alpaca_imagebytes.py` | 26 | `alpaca_imagebytes` — 44-byte little-endian header, `[x][y]` element order transposed to (H, W) on a non-square frame, every transmission element type, rank 3 planes last, error payload raises the mapped exception (NotConnected is a disconnect for the loop), malformed payload is a protocol error, JSON fallback equals ImageBytes, case-insensitive JSON keys |
| `test_alpaca_http.py` | 34 | `alpaca_http` — ClientID and increasing ClientTransactionID on every request, GET query vs canonical-case PUT form fields (wrong casing rejected by the strict fake), case-insensitive reply keys, ErrorNumber / HTTP status mapping, 5xx / bad JSON / connection error / timeout are transport errors, every call time-bounded, ImageBytes and JSON downloads agree, JSON fallback, download on its own session in 1 MiB chunks, abort and stall cut it short, wall-clock deadline on a trickle, Management API; IPv6 zone id escaped in the URL, `split_host_port` (garbage refused with a reason; a typed port reaches that port), DNS lookup bounded and cached, `put_control` never waits behind the ordinary session, connect closes a client a skipped disconnect left open |
| `test_alpaca_sensor.py` | 83 | `alpaca_sensor` — physical pattern from the four BayerOffsets and subframe start parity, the pipeline name (physical reversed) debayers the right colours and matches the ZWO ASI676MC "BGGR", agrees with the simulator's mosaic, mono / RGB, CMYG / LRGB read mono and warn once, flips move the pattern (odd dimensions keep that axis), MaxADU 255 is 8-bit clipped and larger is MSB-aligned 16-bit, above 16 bits shifts right, unknown reads 65535, row-major little-endian bytes; MaxADU reported as a power of two is the level count (4096 → 4095), `to_raw_array` agrees with and without in-place and never touches the input, uint16 in place needs no new frame |
| `test_alpaca_discovery.py` | 13 | `alpaca_discovery` — `alpacadiscovery1` to broadcast and loopback on UDP 32227 (fake socket), replies parsed and deduped in order, garbage ignored, socket / send failures never raise, the Camera filter drops PFR's own SafetyMonitor, a dead server among live ones, a renumbered device listed under its new number; each IPv4 interface's directed broadcast (ifaddr), enumeration failure falls back to broadcast + loopback, a camera on a second subnet is found, listing failures at DEBUG with one INFO summary |
| `test_alpaca_sim_device.py` | 15 | `scripts/dev/alpaca_sim` fake camera pinned to the spec — 0x407 on camera members while disconnected, exposure state machine on an injected clock, wrong-moment / bad-value errors, Abort discards and Stop keeps the image, Can* NotImplemented, mono without Bayer offsets and colour as rank 3, gain / offset modes, V4 asynchronous Connect, every fault switch (N NotConnected, dropped connection, idle blip, failed exposure, renumbering, transport), request log; driver-error and InvalidOperation reads, ROI locked while exposing when the spec says so |
| `test_alpaca_device.py` | 20 | `alpaca_device` — V3 `Connected=true` and V4 `Connect` + bounded `Connecting` poll, an already-connected device left alone, release only when configured, static sensor facts, NotImplemented optional members switch capabilities off, gain list mode is an index range, gain clamped and sent on change only, full-frame ROI in canonical casing, facade µs → s and range clamp, exposure status incl. the idle-after-start grace, MSB-aligned row-major data, ROI / dropped frames, temperature in tenths, Abort falls back to Stop |
| `test_alpaca_connection.py` | 14 | `alpaca_connection` — handshake reads static facts and configures, learns the UniqueID and profile key, a renumbered device found by UniqueID, unknown UniqueID fails the gate, a server with no Camera (the SafetyMonitor port) and a missing device number get clear errors, unreachable server / no host fail without raising, `sdk_lock` per instance and never `SDK_LOCK`, disconnect idempotent and leaves the device connected unless `release_on_disconnect`, aborts an exposure in flight, reconnect after the driver dropped the camera, `verify_identity`, never touches config |
| `test_alpaca_camera.py` | 31 | `AlpacaCamera` + `alpaca_capture_frame` — metadata keys and order equal ZWO's, Duration = profile ms / 1000, `max_exposure` (ZWO message) and device-range clamps, red-top / blue-bottom correct under every BayerOffset, software flip keeps colour, mono and RGB sensors, recovery request and Stop abort the exposure, idle blip and three transient poll failures tolerated (a fourth raises), bounded exposure timeout, CameraState Error fails the frame, download diagnostics and JSON fallback, gain sent on change only, capabilities follow the device, a profile Bayer pattern never clobbers the device's, `asi_auto` runs as gray world and manual's software red gain scales red without rewriting the stored config (ZWO's `wb_config` left as given), RAW16 refused and offset live, identity learned but not persisted, Stop returns within 3 s during a long download, params read the shared `zwo_*` mirror keys |
| `test_alpaca_capture_loop.py` | 7 | shared `capture_loop` driving a real `AlpacaCamera` on the fake network (fake clock) — outage → reconnect → resume, a transport outage that outlasts one reconnect still recovers, recovery off gets one reconnect then stops and releases the handle, gated off-peak never sends `Connected=false` by default and does when configured, the window reopens after off-peak, an unreachable camera prints the Alpaca hints |
| `test_alpaca_calibration.py` | 4 | `CameraCalibration` on the Alpaca facade converges to the target on the simulator's exposure × gain scene, µs → s happens only in the facade, `adjust_exposure_auto` writes through it |
| `test_alpaca_probe.py` | 12 | `alpaca_probe` (Test Connection) — summary line, never configures and releases only when asked, labels mono / RGB sensors, says why it failed, never raises, bounded when the server accepts and then stalls; named gains listed for the Gain hint, a session that returns no response is a protocol error not a crash |
| `test_alpaca_sim.py` | 14 | `scripts/dev/alpaca_camera_sim.py` over real sockets on 127.0.0.1 — Bayer orientation end to end, ImageBytes and JSON agree in (H, W), brightness follows exposure × gain, spec errors over HTTP, ASCOM's `alpyca` reads it like our client (JSON path and Management API too; skipped without alpyca), UDP discovery + listing, the CLI starts and serves (`requires_network`); discovery binds where HTTP listens, live download through the guarded session and calibration on a slow readout |
| `test_alpaca_session_live.py` | 5 | `AlpacaCamera` against the real-HTTP simulator on its own thread — live frames carry the full metadata and correct colour, auto-exposure converges on the sky scene, JSON fallback and the probe; issue #177 end to end: a camera that stops finishing exposures is reopened in the driver and captures again without Stop + Start (`requires_network`) |
| `test_alpaca_stalled_exposure.py` | 11 | `alpaca_stall_recovery` + shared loop, issue #177 — a camera that answers but never finishes an exposure (simulator `stall_exposures`) is reopened in the driver exactly once (one WARNING naming the other-client effect) after two stalls and resumes after a 1 s test exposure; the timeout names the CameraState before and after abort, overdue polls at DEBUG, no INFO traceback; pill reads `stalled` after a reconnect that answered; a stall no reopen fixes climbs 1/5, 2/5 … to long-retry with one error alert; a passing probe never clears the stall count (short exposures work, long ones stall); a transport outage never reopens; reopen rate-limited, never after one stall, V4 uses Disconnect + Connect; another client's exposure during the probe is not aborted |
| `test_capture_source_selector.py` | 15 | Capture Mode selector — three segments ↔ (`capture_mode`, `camera_source`), Directory Watch leaves the source alone, load selects the segment from the pair, description per segment, the source gates the camera widgets, capture locks the selector (offscreen Qt); mouse clicks on each segment switch mode, the item's `clicked(bool)` reaches the right segment |
| `test_camera_capability_gate.py` | 13 | `apply_capabilities` — ZWO shows SDK path, picker, RAW16 and hardware WB, Revive notice ZWO only; Alpaca hides them and shows its card, a stored `asi_auto` reads as gray world without writing and switching back restores it, the detected Bayer pattern read-only (mono shows none), unsupported gain / offset disabled / hidden, back to ZWO exactly, a clamped value reloads in full, Alpaca settings save to the Alpaca profile, software WB gains write `white_balance`, stop resets to the static set (offscreen Qt) |
| `test_alpaca_connection_card.py` | 14 | `AlpacaConnectionCard` + settings-widget wiring — ranges and defaults, load never emits, typed address emitted and clears the learned identity without a profile per keystroke, discovered devices listed by label with none picked, picking one fills the address and writes its identity, discovery error / empty result, button progress, test caption (long failure shortened, whole text in the tooltip), capture locks the card, loading never writes `alpaca_camera` (offscreen Qt) |
| `test_alpaca_discovery_controller.py` | 7 | `AlpacaDiscoveryController` — discovery result delivered on the GUI thread, a failure reports its error, a second discovery while one runs is ignored, Test Connection passes the settings and delivers the details, a raising probe or a missing probe module is a failed test, `capabilities_from_probe` (offscreen Qt) |
| `test_alpaca_main_window_wiring.py` | 8 | main-window Alpaca wiring — Start needs a host, not detected cameras (ZWO gate unchanged), startup auto-detect skips the SDK, Connect Camera on an Alpaca rig discovers, Discover results reach the panel, Test without a host never reaches the controller, a passed test and a live session push their capabilities to the panel (offscreen Qt) |
| `test_api_status.py` | 16 | `api_status` — `derive_health` (idle / ok / degraded on a stall / recovering / error, gated window idle not error, watch mode not degraded on age), status view countdown and empty snapshot, capture block matches the field catalog; `camera_source` reported in camera mode and `null` in watch / idle, old feeders without the argument still work, field documented and in the OpenAPI schema |
| `test_alpaca_transport_text.py` | 11 | `alpaca_transport_text` — refused / unknown host / no route / timeout / reset / anything else each read as one short sentence naming the server, never urllib3's retry text or the ClientID query, raw text at DEBUG only, a read timeout names the read wait, the probe carries the sentence, a non-Alpaca web server is named as such (HTTP 404 on /management), Alpaca traffic ignores a system proxy by default |
| `test_camera_controller_alpaca.py` | 17 | `CameraControllerQt` on an Alpaca source — never resolves a ZWO index, learned identity persisted on the main thread, a connect failure reports its reason not a generic line, unrecoverable DLL patterns ignored (ZWO still resets USB), a start that fails before the first frame is retried under auto-recovery and repeated failures climb the restart ladder, a wedged Alpaca thread reschedules then escalates without USB (ZWO still tries USB first), USB reset worker and Revive do nothing, unrecoverable text names no ZWO SDK; a wedged ZWO session is still reset after switching to Alpaca, a skipped USB reset keeps recovery alive, start-failure handling and unrecoverable text follow the backend/session that started |
| `test_camera_settings_alpaca.py` | 7 | `apply_camera_settings` on an Alpaca session — profile found by the `alpaca:<UniqueID>` key and pushed live, the device Bayer pattern survives the profile, a different configured camera is refused, switching source mid-capture pushes nothing, offset skipped when the camera has none, a WB change is stored without a device write, a live manual red gain scales red in the delivered frame |
| `test_headless_alpaca.py` | 11 | `HeadlessRunner` on an Alpaca source — `_init_camera` opens the camera through the session factory and persists its identity, its profile is the active one, a missing host or unreachable server fails cleanly, ZWO still takes the SDK branch, `/status` carries `camera_source`, a dropped server is reopened after a failed frame; recovery off spends one reconnect until a good frame and then pauses, recovery on always reconnects, the open logs one line with no connection duplicates, expected failures keep their traceback at DEBUG |
| `test_alpaca_diagnostics.py` | 7 | `alpaca_diagnostics` + diagnostics summary — session facts after a frame (driver, interface, sensor type, MaxADU, bit depth, patterns, image format, last download size / time / MB/s), facts before connect or a frame never raise, transfer rate, summary names `camera_source` and carries the Alpaca block without host or UniqueID, ZWO summary unchanged apart from the source |
| `test_capture_source_labels.py` | 18 | source-aware wording outside the panels — Discord start mode line ("ZWO Camera Capture" byte for byte, "Alpaca Camera Capture", "Directory Watch", and watch when the key is absent), periodic and startup main-window text, capture snapshot reports the source, watchdog fatal text follows the source, app bar idle pill reads "Alpaca · not connected" until a Test Connection passes or a session has run (still counted after it stops), ZWO keeps "Connected · idle" (offscreen Qt) |
| `test_config_migrate.py` | 28 | `config_migrate` — legacy `zwo_*` keys fold into the active camera profile (or `__unassigned__`), existing values win, idempotent, dead keys stripped, global `zwo_*` kept; coordinate canonicalisation (DMS with hemisphere → signed decimal, unsigned DMS read north/east with a warning, garbage / out of range left alone, notices never carry the coordinate, canonical config not rewritten); an Alpaca config drops the mirrors without growing a profile, an Alpaca config with a ZWO camera still folds into it, a restart round trip makes no stray profile |
| `test_alpaca_download.py` | 18 | `alpaca_download` — a dripping body is cut at the total deadline, abort mid-drip returns within the watch interval, headers that never arrive are broken by the deadline and by abort, the session's abort predicate honoured, error body bounded and printable, a short body is a transport error not a hang, buffer sized from Content-Length, an oversize JSON image refused with a reason naming ImageBytes, ImageBytes error with a bad DataStart reports the device error, only real sessions guarded; the same against real local dripping / silent servers (`requires_network`) |
| `test_alpaca_frame_budget.py` | 6 | `alpaca_frame_budget` + `capture_watchdog` — unmeasured download budget assumes a slow VPN, measured throughput sets it (floor and cap), frame budget = exposure + grace + download, the watchdog waits out an Alpaca frame budget, ZWO's threshold unchanged, a watchdog cancel of a download consumed once |
| `test_alpaca_shared_camera.py` | 32 | `alpaca_shared_camera` + device on a camera shared with NINA — a failed download never aborts the other client's exposure, our own still aborted on Stop (also while its start PUT is blocking), ImageReady ends ours, start refused because another client is exposing skips the frame, abort on the control session, gain / offset / ROI re-asserted before every frame, wrong-shape or wrong-duration image dropped and ROI restored, a driver that always reports another duration stops being checked, Idle-then-ImageReady not lost, optional member driver errors read as absent (required ones still fail connect), temperature during readout, pixels above MaxADU warn once, MaxADU 65536 keeps 16-bit values, warnings survive a reconnect, calibration on a slow readout (abort aborts ours; hook not on ZWO), live settings never wait behind the capture thread, one pixel copy per frame, slow-driver PUT room, ignored ImageBytes warned once |
| `test_capture_loop_wording.py` | 8 | `capture_loop` + `recovery_wording` — ZWO wording is the original text byte for byte and logs the same lines through its callback, an Alpaca outage logs network wording at WARNING without tracebacks (a bug keeps its traceback), `link_state` follows the loop (long retry reads `waiting`, `ok` initially on every session), `FrameSkipped` is not an error and never reconnects |
| `test_alpaca_identity_retention.py` | 11 | `alpaca_identity` — retyping the same port keeps the identity and the tuned profile, a different address clears it and going back restores it, a discovered device remembered at its address, a passed Test Connection learns the identity and moves the address profile, a result for an address edited mid-test not applied, tested ranges come back after a ZWO round trip, exposure hint follows the camera limit, named gains shown by name (list mode only), Alpaca edits write no legacy `zwo_*` mirror, a long Found label never widens the card (offscreen Qt) |
| `test_camera_link_pill.py` | 12 | app bar camera pill — a session's `link_state` shows "Reconnecting to camera…" / "Camera lost · retrying" / "Reconnected · waiting for a frame" / "Camera not finishing exposures" on either backend, frames flowing again show the count, no `link_state` reads as ok, refresh pushes the idle label and the primary action at once (offscreen Qt) |
| `test_app_bar_primary_action.py` | 3 | `AppBar` primary action — a mouse click on the visible Stop emits `stop_clicked`, a click that reaches the hidden Start button while capturing stops instead of re-entering `start_capture`, idle Start / Connect unchanged (offscreen Qt) |
| `test_camera_error_logging.py` | 5 | a capture-loop error reaches the log once (the controller logs it, the window no longer repeats it): ZWO "Camera error: …" at ERROR, an Alpaca loop retrying an outage at WARNING; errors the controller never logged (SDK missing, unrecoverable mode) still logged by the window |
| `test_imaging_conditions_rules.py` | 39 | `imaging_conditions.frame_rules` / `frame_signals` / `settings` — every reason code, sun limit -18 / -12 and unknown sun fail-open, roof confidence and the roofless opt-out, Watch-mode tokens read like `_ML_RESULTS`, cloud cover null with neither baseline nor usable ML class and `ml_class` fallback, ratio → cloud and `stars_low`, cold start never judges a count on its own, `change_pct` vs the last `trend_frames` and a configurable % drop, FWHM `change_pct` vs the night median and `fwhm_rise_pct`, baseline frames only dark / open / Clear / not static, hand-edited config clamped, auto staleness, an old config merges the block's defaults |
| `test_imaging_conditions_feed.py` | 22 | `ImagingConditionsFeed` + `ConditionsStore` on an injected clock — starts not-OK (`warming_up`), 3 bad to pause / 3 good to resume (`recovering`), stale after 3x the frame interval (min 120 s) or the configured limit and starting over on resume, disabled, reprocess not counted, baseline learning per Moon bin with `baseline_status` learning → ready, cloudy / closed / twilight frames never teach it, a new camera / frame size / lens restarts it (a refinement or a first calibration does not), arcsec only with a calibration (scaled from the calibration's frame size, null on a crop or a garbage file), the pipeline hook never raises, Directory Watch `process_image` feeds it, per-frame cost (`slow`: p50/p95 logged, 5 ms ceiling) |
| `test_imaging_conditions_baseline.py` | 23 | `star_baseline` — Moon bins, no star count in the shipped defaults, median once enough samples, bounded per bin, invalid samples refused, throttled atomic saves with no temp left behind, stamp change starts over, reset removes the file (files only), truncated / foreign / non-UTF-8 / oversized files load as learning, a failed write stays dirty, concurrent adds and saves |
| `test_api_conditions.py` | 16 | `GET /conditions` — golden contract key set (original names pinned, additive ones listed) on a cold and a published snapshot, catalog = payload, every reason code documented, auth matrix (401 / 403 / 503 `control_disabled`, no CORS, token never echoed), route matching and `web_output` GET dispatch, OpenAPI + `/docs` only with the control API, `/docs` renders every field (nested ones flattened, each with a type and a description), compact `/status` block |
| `test_imaging_conditions_card.py` | 14 | `ImagingConditionsCard` / `ImagingConditionsController` / IMAGING tile — silent load, values round-trip, garbage loads as defaults, one `changed` per edit, verdict wording, a change that rounds to zero reads ±0% (stars and FWHM), the headline reason is not repeated on the line below ("Also: …", recovering shows the good-frame count), stale reads Paused · No recent frames (Capture stopped with capture off) and dates the measurements, Idle before capture with no frames and Warming up once it runs, the learning line names the Moon bin and is hidden in daylight or without a star index, empty lines hidden, the star-index tooltip on the cloud limit, every row label fits its column, controller saves into the config block (with `sensitivity` and the derived star floor), disabling reaches the store, Reset baseline forgets the counts (offscreen Qt) |
| `test_imaging_conditions_presets.py` | 12 | `presets` + the card's Sensitivity — Balanced is the old defaults written out, `read_settings` never reads the preset name, every preset's star floor sits under its cloud line and the presets order Relaxed → Strict, star floor bounds, a moved number reads Custom; picking a preset writes all six keys with one `changed`, editing shows Custom and editing back restores the preset, the cloud limit carries the star floor, picking Custom opens Advanced and changes nothing, a hand-edited ratio survives (offscreen Qt) |
| `test_conditions_history.py` | 12 | `conditions_history` + `nina_checkin` — events follow `imaging_ok` with durations and a JSON payload, a frames gap while OK is a pause at the stale limit, the ring bounded by count and age, events survive a restart (an open OK closed as `restarted`), old / garbled / oversized / missing files load as nothing, 4 writers + 2 readers, the feed records every judged frame, the store's `events` block (empty while off); client labels, last NINA vs last anyone, the real `/conditions` route records accepted and refused requests without the token |
| `test_conditions_live_view.py` | 12 | Live Monitoring conditions panel + NINA & ASCOM page — verdict sentences (paused with time and cause, recovering with the good-frame count, OK since, idle, checking, stale, off, waiting for dark, not OK yet), no jargon in pause causes, event lines with durations / "so far", learning and measurement lines, NINA check-in wording; chart shades not-OK runs and gaps and paints, the panel fills every part, shown only on Live Monitoring and collapsible, the IMAGING tile alone is a link; page in the nav rail, the tile / Settings link / Open pointers navigate, the moved Roof Safety and Control API cards load and save the same `ml_models` / `output` keys, Image Processing no longer holds them (offscreen Qt) |
