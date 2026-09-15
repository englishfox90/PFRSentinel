---
globs: "tests/**/*.py"
description: Pytest conventions for PFR Sentinel
---

# Tests — Pytest Conventions

## Naming
- `tests/test_<module>.py` mirrors `<module>.py` under test.
- Test functions: `test_<behaviour>` — describe behaviour, not implementation.

## Markers
Defined in `pytest.ini`:
- `@pytest.mark.requires_camera` — physical ZWO ASI camera attached
- `@pytest.mark.requires_network` — internet access (weather API, web server bind)
- `@pytest.mark.requires_ml_models` — ONNX models present in models dir

Default CI run: `pytest -m "not requires_camera and not requires_network"`.

## What to mock
- All network: `requests`, Discord webhooks, weather API, web server clients
- ZWO SDK calls — use `unittest.mock` or fixtures from `tests/conftest.py`
- Filesystem operations should use the `tmp_path` fixture, not project dirs

## What NOT to mock
- The `config` module — use a temp config file via `tmp_path` and pass paths in.
- The processor — exercise it with real PIL images (small ones).

## Fixtures
- Reusable fixtures live in `tests/conftest.py`. Module-local fixtures stay in their test file.
- Fixture name should describe what it provides, not how it works (`sample_fits_image`, not `make_image_with_mocked_header`).
