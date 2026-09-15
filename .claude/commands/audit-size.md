---
description: Audit Python file sizes against the project caps and exception list
---

Run a file-size audit for the PFR Sentinel codebase.

1. Run this command to count lines per Python file under `services/`, `ui/`, and `ml/`:

   ```bash
   wc -l services/*.py ui/**/*.py ml/*.py 2>/dev/null | sort -rn | head -30
   ```

2. Cross-reference against `.claude/hooks/check_file_size.py`:
   - **Hard cap**: 750 lines for any file not in `EXCEPTIONS`
   - **Warn cap**: 600 lines (warning, not block)
   - **Frozen exceptions**: each has its own ceiling in the hook

3. Print a markdown table with three sections:
   - **Over hard cap (750)** — files not in the exception list that exceed 750. These should not exist; if any are listed, name them as urgent.
   - **Over their frozen exception ceiling** — files in `EXCEPTIONS` that exceed their custom cap. Same urgency as above.
   - **Approaching cap (600–750)** — files heading toward trouble. Recommend a split.

4. Flag any new file approaching 600 lines as a candidate for proactive splitting before it crosses the hard cap.

Keep the output terse — one table, one short recommendation paragraph. No celebration, no narration.
