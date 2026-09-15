---
description: Find Python modules that lack matching pytest coverage
---

Audit which `services/` and `ml/` modules have no corresponding test file.

1. List every `.py` file in `services/` and `ml/` (skip `__init__.py` and obvious scripts).
2. For each, check whether `tests/test_<modulename>.py` exists.
3. Print a markdown table:

   | Module | Has test file? | Notes |
   |--------|---------------|-------|
   | `services/processor.py` | ✓ | `tests/test_image_output.py` |
   | `services/foo.py` | ✗ | no coverage |

4. Then suggest the **top 3 modules to add tests for**, prioritised by:
   - Risk of silent breakage (config, processor, weather, output paths)
   - Recently changed files (check `git log --since="2 weeks ago" --name-only`)
   - Modules with complex branching logic

Note: the test file naming convention isn't strict 1:1 — `tests/test_image_output.py` covers `services/processor.py`. Use judgment, don't just match filenames.

Keep the output to one table + a short prioritised list. No fluff.
