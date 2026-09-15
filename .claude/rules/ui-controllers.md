---
globs: "ui/controllers/**/*.py"
description: Controllers own business logic and thread coordination
---

# UI Controllers — Business Logic Lives Here

Controllers in `ui/controllers/` own all processing, I/O, threading, and state for the panels they back.

## Responsibilities
- Process events from panels (button clicks, config changes)
- Run heavy work in background threads
- Call into `services/` for camera, file, network, and processing operations
- Emit Qt signals when state changes or work completes

## Threading rules
- **Never touch a Qt widget from a worker thread.** Always go through a signal/slot or `QMetaObject.invokeMethod()`.
- If a panel needs to know about progress, emit a signal — don't reach into the panel.
- Background threads should be daemonized so they don't block app shutdown.
- Use `app_logger` for status — never `print()` from a thread.

## Naming
- One controller per panel where possible: `ui/controllers/<feature>_controller.py` ↔ `ui/panels/<feature>_panel.py`.
- Controllers may share lower-level workers (e.g. `ImageProcessorWorker` consumed by both watch and camera controllers).
