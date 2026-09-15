---
description: Split an oversized Python file, update the size-hook exception list, and verify nothing breaks
argument-hint: <path/to/file.py>
---

Refactor `$ARGUMENTS` to bring it under the project's file size cap (target ≤600 lines, hard cap 750).

## Approach (follow this order)

1. **Read first** — in parallel, read the target file AND `.claude/hooks/check_file_size.py`.
   - Note the current line count.
   - Check whether the file appears in `EXCEPTIONS` in the hook, and if so its custom ceiling.
   - If the file is NOT in `EXCEPTIONS`, step 6 is a no-op — skip it.

2. **Find the seam** — identify the largest self-contained section that can become its own class
   or module. Prefer extracting a `QWidget` subclass if it's a UI panel, or a helper class if
   it's a service. Grep for the original class name across `ui/` and `services/` to enumerate
   callers — every public method they depend on must remain accessible unchanged on the
   original class after the split.

3. **Plan the split** — estimate post-split line counts for BOTH files before writing any code.
   Both must land under 750; aim for under 600. If one file would still exceed 750, find a
   second seam.

4. **Write the extracted file first**, then rewrite the original to delegate to it. Do not
   change any public API that callers depend on — proxy/delegate instead.

5. **Preserve all behaviour exactly.** Common traps:
   - Inline imports (e.g. `from services.x import y` inside a method) should be moved to
     the top of whichever file now owns that code.
   - If this is a Qt panel/controller:
     - Signal connections must fire in the same order.
     - Widget `setValue()` calls must happen BEFORE `signal.connect()` to avoid premature handler fires.
     - `_loading_config` / guard flags must cover the same code paths.
     - `show()`/`hide()` / `setVisible()` on conditional sections must still run even when config guards block saving.
     - If there are 5+ identical `if self._loading_config or not hasattr(self, ...)` guards, consider extracting a `_can_save` property.

6. **Update `.claude/hooks/check_file_size.py`** — if the file was in `EXCEPTIONS` and is now
   under the 750 hard cap, remove its entry. If it was not in `EXCEPTIONS`, skip.

7. **Run `/simplify`** on the changed files before running tests.

8. **Run the fast test suite**:
   ```bash
   ./venv/Scripts/python.exe -m pytest -m "not requires_camera and not requires_network and not requires_ml_models" -q
   ```
   - Any failures that existed before your changes are acceptable — confirm they are pre-existing
     by checking whether the failing test touches any file you modified.
   - All other tests must pass.

9. **Invoke the `pfr-reviewer` subagent** on the changed files for a domain-aware review of the split.

## Rules to follow

- Read `.claude/rules/python-general.md` and the rule file relevant to the target file's
  directory (`ui-panels.md`, `ui-controllers.md`, `services.md`, `services-camera.md`,
  `allsky.md`, `ml.md`) before making changes.
- No new comments that just describe what the code does.
- No docstrings on private methods.
- Prefer `setVisible(bool)` over separate `show()`/`hide()` calls where it simplifies code.
- Do not change any behaviour, add features, or clean up unrelated code during the split.
