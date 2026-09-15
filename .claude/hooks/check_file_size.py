#!/usr/bin/env python
"""PreToolUse hook — enforce per-file size caps.

Blocks Write when `content` alone exceeds the ceiling.
For Edit, reads the current file and simulates the edit to predict post-edit line count.
Exits 2 on violation (blocks the tool call); exits 0 otherwise.
"""
import json
import os
import sys

# Caps and exceptions live in a tracked module so CI enforces the same policy
# (.claude/ is gitignored, so this hook itself never reaches the runner).
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _REPO_ROOT)
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


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except json.JSONDecodeError:
        return 0

    tool_name = data.get("tool_name", "")
    tool_input = data.get("tool_input", {}) or {}
    path = tool_input.get("file_path", "")
    # The NINA plugin (nina-plugin/) is C#/XAML. It was landing outside every
    # guardrail in this repo — SentinelPoller.cs passed 600 lines with nothing
    # to say so. Same caps, same reasoning: a file that large stops being
    # reviewable regardless of language.
    if not path or not path.endswith((".py", ".cs", ".xaml")):
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
