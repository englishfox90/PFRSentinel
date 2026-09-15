---
globs: "**/*.py"
description: General Python rules for PFR Sentinel — applies to every .py file
---

# Python — General Rules

- **Logging**: use `from services.logger import app_logger` for all logging. Never `print()` from any module — `print()` from worker threads can deadlock the Qt event loop.
- **Imports**:
  - Inside a package: relative — `from .module import X`
  - From outside the package: absolute — `from services.module import X`
- **Config access**: always go through `services.config`. Use `config.set()` / `config.save()`. Never read or write `config.json` directly.
- **Config keys are nested** — `output_config.get('webserver_enabled')`, NOT `config.get('web_enabled')`.
- **Config paths**: always resolve via `app_config.get_config_dir()`. Never hardcode `%APPDATA%` paths.
- **File size**: target ≤600 lines, hard cap 750. The `check_file_size.py` hook blocks Edit/Write that would exceed the cap. Frozen exceptions are listed in that hook — they may not grow further.
- **No new comments** that just describe what the code does. Save comments for non-obvious *why*: a constraint, a workaround, an invariant a reader would otherwise miss.

## Module design (SRP)

**Before adding code to an existing file**, ask: does this extend what's already here, or is it a distinct responsibility? If it's a new algorithm, a new I/O concern, or a new data transformation, give it its own file — don't grow the nearest one.

**Signals that code deserves its own module:**
- It has a distinct domain name (e.g. image stretching, token substitution, compass geometry)
- It is independently testable without the rest of the file
- Adding it pushes the file past ~400 lines or introduces new top-level imports

**Name files after what they do**, not who calls them: `image_stretch.py` is right, `processor_utils.py` is wrong. A good module name is self-explanatory without reading the file.

**Re-export for backwards compatibility**: when extracting code from an existing module that already has callers, re-export the moved symbol from the original location so nothing breaks:
```python
from .new_module import MyClass  # re-exported for existing callers
```

The file-size cap is a *lagging* indicator — don't wait for the hook to fire. If a group of functions has a distinct purpose, extract it proactively.

## Analytics (PostHog)
- Use the `capture_event()` and `capture_error()` helpers — they handle `distinct_id` lookup and swallow PostHog errors so analytics never breaks the app.
- Event names: `snake_case`, **past tense** (`image_processed`, `camera_disconnected`). Not `processImage` or `process_image`.
- `distinct_id` is a UUID stored in `config.json` as `posthog_distinct_id` — don't generate a new one per event.
- Honour `analytics_enabled: false` — when set, helpers must be a no-op. Check this before calling out.
