---
description: Run before committing — size audit + fast test suite + diff stats
---

Run the standard pre-commit checks for PFR Sentinel and report results.

1. **File size audit** — run `/audit-size` (or its checks inline). Stop and report if anything is over the hard cap or its frozen ceiling.

2. **Fast test suite** — run:
   ```bash
   ./venv/Scripts/python.exe -m pytest -q -m "not requires_camera and not requires_network and not requires_ml_models"
   ```
   Report pass/fail count. If anything fails, show the failing test name and a one-line reason.

3. **Diff overview**:
   ```bash
   git diff --stat
   git status -s
   ```
   List the modified files and a short categorisation: which are core code, which are tests, which are config/docs.

4. **Final verdict**:
   - ✓ READY — all checks pass
   - ✗ BLOCKED — list each blocker on its own line, no narrative

Don't run the slow tests (camera, network, ML models). Don't push or commit anything — this is a check, not an action.
