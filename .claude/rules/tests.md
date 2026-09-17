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
- `@pytest.mark.requires_windows` — Windows-only behaviour (NTFS junctions, drive
  roots, Win32 APIs). `tests/conftest.py` skips these off Windows automatically,
  so use the marker rather than an inline `skipif(sys.platform != "win32")` — the
  marker is selectable (`-m requires_windows`) and keeps one pytest command line
  working on all three CI runners.

Default CI run: `pytest -m "not requires_camera and not requires_network"`.

`tests/conftest.py` also sets `QT_QPA_PLATFORM=offscreen` (via `setdefault`, so a
real display still wins locally). Don't re-set it per file.

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
