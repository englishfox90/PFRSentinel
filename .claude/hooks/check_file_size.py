#!/usr/bin/env python
"""Per-file size caps — PreToolUse guard, plus a PostToolUse safety net.

PreToolUse (Edit|Write): blocks Write when `content` alone exceeds the ceiling.
For Edit, reads the current file and simulates the edit to predict post-edit line count.
Exits 2 on violation (blocks the tool call); exits 0 otherwise.

PostToolUse (Bash): a Bash write carries no `file_path` to check ahead of time,
so the whole tree is audited afterwards instead. Warning only — see audit_tree.
"""
import contextlib
import io
import json
import os
import sys

# Caps and exceptions live in a tracked module shared with CI, so a hook run and
# a CI run enforce exactly the same policy.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _REPO_ROOT)
from scripts.ci import check_file_sizes  # noqa: E402
from scripts.ci.size_policy import EXCEPTIONS, HARD_CAP, WARN_CAP, count_lines  # noqa: E402


def predict_edit_lines(path: str, old_string: str, new_string: str, replace_all: bool) -> int:
    try:
        with open(path, encoding="utf-8") as f:
            current = f.read()
    except (OSError, UnicodeDecodeError):
        return -1
    if replace_all:
        updated = current.replace(old_string, new_string)
    else:
        # Edit tool replaces only the first occurrence.
        updated = current.replace(old_string, new_string, 1)
    return count_lines(updated)


def audit_tree() -> int:
    """Fallback when a tool writes files without naming one (Bash: heredoc, sed -i).

    Nothing can be predicted before the fact, so re-run the shared CI audit over
    the tree after the write. Always exits 0: the write has already landed, so
    there is nothing left to block, and failing an arbitrary Bash call would do
    far more damage than the breach being reported.
    """
    out, err = io.StringIO(), io.StringIO()
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = check_file_sizes.main()
    except Exception:
        return 0
    if rc == 0:
        return 0
    # Only the over-ceiling lines. The audit also reports every file merely
    # approaching the cap; that is a standing backlog, not news about this write.
    print(
        "WARN: a source file now exceeds its size ceiling. The Edit/Write size "
        "hook cannot see writes made through Bash, so this is a post-hoc audit:",
        file=sys.stderr,
    )
    for line in out.getvalue().splitlines():
        if line.startswith("FAIL"):
            print(f"  {line}", file=sys.stderr)
    print(
        "Split by responsibility before adding more; do not raise the cap. "
        "See .claude/rules/python-general.md.",
        file=sys.stderr,
    )
    return 0


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except json.JSONDecodeError:
        return 0

    tool_name = data.get("tool_name", "")
    tool_input = data.get("tool_input", {}) or {}
    path = tool_input.get("file_path", "")
    if not path:
        return audit_tree()
    # The NINA plugin (nina-plugin/) is C#/XAML. It was landing outside every
    # guardrail in this repo — SentinelPoller.cs passed 600 lines with nothing
    # to say so. Same caps, same reasoning: a file that large stops being
    # reviewable regardless of language.
    if not path.endswith((".py", ".cs", ".xaml")):
        return 0

    rel = os.path.relpath(path).replace("\\", "/")

    if tool_name == "Write":
        lines = count_lines(tool_input.get("content", ""))
    elif tool_name == "Edit":
        lines = predict_edit_lines(
            path,
            tool_input.get("old_string", ""),
            tool_input.get("new_string", ""),
            bool(tool_input.get("replace_all", False)),
        )
        if lines < 0:
            return 0  # file unreadable, don't block
    else:
        return 0

    ceiling = EXCEPTIONS.get(rel, HARD_CAP)
    if lines > ceiling:
        print(
            f"BLOCK: {rel} would be {lines} lines, exceeds ceiling {ceiling}. "
            f"Split by responsibility before adding more. "
            f"See .claude/rules/python-general.md.",
            file=sys.stderr,
        )
        return 2
    if rel not in EXCEPTIONS and lines > WARN_CAP:
        print(
            f"WARN: {rel} is {lines} lines (target ≤{WARN_CAP}, cap {HARD_CAP}). "
            f"Consider splitting soon.",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
