---
globs: "services/**/*.py"
description: Core services — threading, config, logging conventions
---

# Services — Core Conventions

## Logging
- Always use `from services.logger import app_logger`. Never `print()`.
- Logs land in `%APPDATA%\PFRSentinel\logs` via thread-safe queue.

## Config
- Read/write through `services.config`. Never touch `config.json` directly.
- Resolve paths via `app_config.get_config_dir()` — no hardcoded `%APPDATA%`.
- Config keys are nested: `output_config.get('webserver_enabled')`.

## Threading
- Background work runs in threads. Surface results to the GUI via Qt signals.
- Never call `QWidget.*` methods from a worker thread.
- Use thread-safe queues (`queue.Queue`) for cross-thread data flow when signals don't fit.

## Disk safety
- `cleanup.py` may delete **files only** — gate every delete with `os.path.isfile()`. Never delete folders.
- For temp work, use `tempfile.mkdtemp` / `tempfile.NamedTemporaryFile`, not project-relative paths.

## External APIs
- **Weather**: only call OpenWeatherMap through `services/weather.py`. It has a 10-minute cache. Direct calls cause API spam and rate-limiting.
- **Discord**: only post via `services/discord_alerts.py`.

## Processing entry points
- `services/processor.py` `add_overlays()` is dual-input — accepts a file path (str) OR a PIL Image. Don't add a second entry point; extend this one.

## Image processing pipeline order
- The order is fixed and matters: **resize first** (if `resize_percent < 100`), **then overlays**. Overlay rendering is expensive — running it before resize wastes CPU on pixels that get thrown away.
- Resize uses `LANCZOS` to preserve edge sharpness. Don't change to a faster filter "for performance" — the visible quality loss isn't worth it.
- Output dispatch is centralized in `_push_to_output_servers()`. Add new outputs there, not by sprinkling calls through the processor.

## Output filenames
- Filename pattern tokens: `{filename}`, `{session}`, `{timestamp}` (YYYYMMDD_HHMMSS).
- The filename field **excludes the file extension** — extension is set separately via the Format dropdown. Adding `.jpg` to the pattern produces `name.jpg.jpg`.

## All-sky calibration
- Calibration JSON lives at `%LOCALAPPDATA%\PFRSentinel\allsky_calibration.json` — user-generated, never bundled.
- Required fields when writing: `rms_residual`, `n_matches`, `calibrated_at`. Diagnostics depend on these — don't omit.
- Calibration is per-installation (depends on physical lens orientation). Never copy calibration across machines.
