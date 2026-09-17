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
│   ├── system_tray_qt.py       # Qt system tray + notifications
│   ├── components/             # Reusable widgets (header, monitoring panel, status indicator)
│   ├── panels/                 # Pages (layout only) — monitoring, capture, output, overlays, timelapse, logs
│   ├── controllers/            # Business logic — capture, output, overlay, timelapse, ML prediction
│   └── theme/                  # tokens.py, styles.py, accent_themes.py, icons.py
├── services/                   # Core processing modules
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
│   ├── capture_schedule_window.py # Capture schedule that follows the timelapse window ± margin
│   ├── ffmpeg_utils.py         # Shared ffmpeg detection
│   ├── diagnostics_bundle.py   # Support ZIP: logs + redacted config + frames (pure)
│   ├── raw_frame_export.py     # Cached frame → Bayer FITS + unprocessed PNG + JSON
│   ├── allsky/                 # All-sky fisheye calibration + overlay
│   ├── meteor/                 # Meteor detection — see METEOR_DETECTION_PLAN.md
│   ├── library/                # Image library index, sessions, retention
│   └── notifications/          # Notification dispatcher + backends (Discord, Hermes)
├── ml/                         # Scene classifiers (roof, sky conditions, stars, moon) — ONNX inference
├── tests/                      # pytest suite — see "Testing" below
├── docs/                       # Plans, design docs, references
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

The Windows test job is named **`Tests and dependency audit (Windows)`** while
the other two are `Tests (macOS)` / `Tests (Linux)`. That asymmetry is load-bearing:
`main`'s branch protection requires the Windows job by that exact string, and
renaming it leaves the required check stuck on "Expected — waiting for status to
be reported", blocking every merge. **Don't regularise the names without changing
branch protection in the same PR.** macOS and Linux are not required checks, so
they report without gating merges — deliberate while the port in #1 is in flight.
When CI fails on a same-repo PR, `claude-ci-fix.yml` has Claude open a fix PR
against that branch. CodeQL and Dependabot are enabled at the repo level.

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

| Test file | Tests | Covers |
|-----------|-------|--------|
| `test_auto_exposure.py` | 21 | `camera_utils` — brightness, clipping, exposure logic |
| `test_camera.py` | 14 | `zwo_camera` — SDK, config, debayering (3 need `requires_camera`) |
| `test_discord.py` | 32 | `discord_alerts` — webhooks, embeds (mocked) |
| `test_image_output.py` | 18 | `processor` — overlays, stretch, output formats |
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
| `test_timelapse_window.py` | 13 | `timelapse_window` — sun window on the LOCAL calendar date (real astral, injected zones), fixed fallback, per-day cache |
| `test_capture_schedule_window.py` | 39 | `capture_schedule_window` — timelapse-derived capture window ± margin, gate two-day logic, roof/always fallbacks, labels |
| `test_zwo_schedule_gate.py` | 16 | `zwo_camera` — schedule gate vs legacy HH:MM path, fail-open, `scheduled_window_label` |
| `test_api_status_schedule.py` | 11 | `api_status.build_schedule` — status API schedule block for fixed and timelapse sources, with and without a camera |
| `test_schedule_window_source_ui.py` | 5 | `_schedule_window_source` — window-source rows: load, visibility, signals (offscreen Qt) |

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

Developer-facing technical reference (feature design, build/release tooling, vendor SDK) lives in [`docs/dev/`](docs/dev/README.md). End-user content is on the project wiki.

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
