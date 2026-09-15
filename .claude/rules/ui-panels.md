---
globs: "ui/panels/**/*.py"
description: Panels are UI layout only — business logic belongs in controllers
---

# UI Panels — Layout Only

Panels in `ui/panels/` define widget layout, signal/slot wiring, and display formatting. They do not own business logic.

## Allowed
- Qt widgets (PySide6, qfluentwidgets)
- Layout code, signal/slot wiring
- Display formatting (e.g. timestamp → string, byte count → `"1.2 MB"`)
- `numpy` for **display scaling only** (e.g. converting raw camera frames for QImage)

## Forbidden
- `requests`, `urllib`, `httpx` — network calls go in services
- `threading`, `asyncio`, `ThreadPoolExecutor` — controllers own background work
- `subprocess` — wrap in a service
- `cv2.cvtColor` / `cv2.imread` / `cv2.imwrite` — image processing belongs in `services/processor.py`
- Direct file I/O for data (`open()` for reading FITS/JSON/config) — use a service or controller
- Direct websocket/MQTT/serial calls

## When you need to add logic
Find or create a controller in `ui/controllers/`. Have the panel emit a signal; the controller does the work; the controller emits a result signal back; the panel updates widgets in response.

The `check_panel_purity.py` hook warns (does not block) when forbidden patterns appear.
