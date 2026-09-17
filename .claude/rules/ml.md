---
globs: "ml/**/*.py"
description: ML module conventions — local ONNX inference only
---

# ML Module Rules

The `ml/` module trains and runs scene classifiers (roof open/closed, sky conditions, stars, moon).

## Inference
- All production inference uses **ONNX models loaded via `onnxruntime`**. No cloud APIs, no PyTorch at runtime.
- Production interface: `ui/controllers/ml_prediction.py`. Don't add new inference entry points.
- Model files live in the repo at `ml/models/` and are resolved **relative to the source tree**, not the app-data dir — `ui/controllers/ml_prediction.py` walks up from `__file__`. `PFRSentinel.spec` bundles the `.onnx` files to the same `ml/models` path.
- The whole inference path is gated on `services.dev_mode_config.is_dev_mode_available()` — it is off in production builds.

## Training
- Training scripts in `ml/` are dev-only — they may import `torch`, `torchvision`, etc. without affecting the shipped app.
- Training data sources: NINA API (roof state), weather API (conditions ground-truth), all-sky camera + manual labels.

## Community data
- Opt-in only. Captures are 256x256 FITS + a calibration JSON sidecar.
- Never upload anything without explicit user consent in config.

## Standalone tools
- `ml/labeling_tool.py` is a standalone dev UI — it has its own size budget and doesn't follow the panel/controller split.
