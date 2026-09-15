"""CI guard for the per-file size caps.

The local PreToolUse hook only sees edits made through Claude Code. This
audits every tracked source file so hand edits and merges are held to the
same ceilings. Both read the policy from size_policy.py.
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
from scripts.ci.size_policy import EXCEPTIONS, HARD_CAP, WARN_CAP, count_lines  # noqa: E402

# Legacy Tkinter GUI is frozen and never edited; the labeling tool has its own
# budget per .claude/rules/ml.md.
EXCLUDED_PREFIXES = ("archive/",)
EXCLUDED_FILES = {"ml/labeling_tool.py"}
SOURCE_SUFFIXES = (".py", ".cs", ".xaml")


def _tracked_files():
    out = subprocess.run(
        ["git", "ls-files", "-z"], cwd=ROOT, check=True, capture_output=True
    ).stdout
    for raw in out.split(b"\0"):
        path = raw.decode("utf-8", errors="replace")
        if not path.endswith(SOURCE_SUFFIXES):
            continue
        if path.startswith(EXCLUDED_PREFIXES) or path in EXCLUDED_FILES:
            continue
        yield path


def main() -> int:
    over, warn = [], []
    for rel in _tracked_files():
        try:
            with open(os.path.join(ROOT, rel), encoding="utf-8", errors="replace") as f:
                lines = count_lines(f.read())
        except OSError:
            continue
        ceiling = EXCEPTIONS.get(rel, HARD_CAP)
        if lines > ceiling:
            over.append((lines, ceiling, rel))
        elif rel not in EXCEPTIONS and lines > WARN_CAP:
            warn.append((lines, rel))

    for lines, rel in sorted(warn, reverse=True):
        print(f"warn  {lines:5d} > {WARN_CAP}  {rel}")
    for lines, ceiling, rel in sorted(over, reverse=True):
        print(f"FAIL  {lines:5d} > {ceiling}  {rel}")
    if over:
        print(
            f"\n{len(over)} file(s) exceed their size ceiling. Split by responsibility "
            "(see .claude/rules/python-general.md); do not raise the cap.",
            file=sys.stderr,
        )
        return 1
    print(f"ok: no tracked source file exceeds its ceiling ({len(warn)} approaching)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
