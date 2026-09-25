---
globs: "ml/**/*.py"
description: ML module conventions — local ONNX inference only
---

# ML Module Rules

The `ml/` module trains and runs scene classifiers (roof open/closed, sky conditions, stars, moon).

## Inference
- All production inference uses **ONNX models loaded via `onnxruntime`**. No cloud APIs, no PyTorch at runtime.
- Production interface: `services/ml_service.py` — `MLService.analyze_image()` behind the `get_ml_service()` singleton. It runs on every frame whenever `ml_models.enabled` is on, in every build. Add new inference there, not as a new entry point.
- Model files live in the repo at `ml/models/` and are resolved **relative to the source tree**, not the app-data dir — `services/ml_service.py` resolves them from its own `__file__`. `PFRSentinel.spec` bundles both `.onnx` files to the same `ml/models` path and lists `ml.roof_classifier` / `ml.sky_classifier` in `hiddenimports`, so the shipped app carries them.
- `ui/controllers/ml_prediction.py` is **not** the production path. It is a second, dev-only loader used by `ui/controllers/dev_mode_utils.py` to put roof/sky predictions into the per-frame calibration JSON export. Don't route new features through it.
- `services.dev_mode_config.is_dev_mode_available()` gates only that dev-only loader, `dev_mode_utils.py`, and the Dev Mode card in `ui/panels/image_processing.py`. It does not gate `services/ml_service.py` — per-frame ML runs in production builds.

## Training
- Training scripts in `ml/` are dev-only — they may import `torch`, `torchvision`, etc. without affecting the shipped app.
- Training data sources: NINA API (roof state), weather API (conditions ground-truth), all-sky camera + manual labels.

## Community data
- Opt-in only. Captures are 256x256 FITS + a calibration JSON sidecar.
- Never upload anything without explicit user consent in config.

## Standalone tools
- `ml/labeling_tool.py` is a standalone dev UI — it has its own size budget and doesn't follow the panel/controller split.
