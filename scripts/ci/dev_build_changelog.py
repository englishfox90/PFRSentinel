"""What went into a dev build: the pull requests merged since the last release.

Writes two Markdown fragments for build.yml's publish-dev job:

- ``--full``: every PR merged into main between the newest ``vX.Y.Z`` tag and
  the build commit. Goes in the release notes and the pinned discussion post,
  both of which are rewritten on each publish.
- ``--delta``: only the PRs since the previous dev build, for the per-build
  discussion comment, so subscribers see what is new rather than the whole
  list again.

PRs are found through the commits in the range, not by merge date: a PR merged
after the tag into a branch that never reached main must not appear, and a
release tag cut from a branch does not line up with main's merge timeline.
Commits pushed straight to main with no PR are left out.

Grouping is by the conventional-commit prefix most titles here carry. CI,
test and GitHub Actions bumps are real work but not something a tester can
exercise, so they fold into a collapsed section. Python dependency bumps stay
visible: an opencv-python major once changed a return shape and crashed
meteor detection on a rig.

A failure to reach the API must not block a publish, so it writes a
placeholder fragment and exits 0 with a workflow warning.

Stdlib and the ``gh`` CLI only: it runs on the publish job's system Python.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from set_dev_version import newest_release_tag  # noqa: E402

REPO = "englishfox90/PFRSentinel"
DEFAULT_BRANCH = "main"
TOOLING_TYPES = {"ci", "test", "tests", "docs", "chore", "build", "style"}
UNAVAILABLE = "_The change list could not be collected for this build; see the build log._\n"

_PREFIX = re.compile(r"^(?P<type>[a-z]+)(?:\((?P<scope>[^)]*)\))?!?:\s*(?P<rest>.+)$")
# Older titles without a conventional prefix, e.g. "CI pipeline: tests, lint ...".
_LOOSE_PREFIX = re.compile(r"^(?P<type>[A-Za-z]+)\b[^:]{0,30}:")

# (key, heading, collapsed)
GROUPS = (
    ("changes", "Changes", False),
    ("dependencies", "Dependencies", False),
    ("tooling", "CI, tests and tooling", True),
)

Api = Callable[[str, str], list[str]]


def gh_api(path: str, jq: str) -> list[str]:
    """Non-empty output lines of ``gh api --paginate PATH --jq JQ``."""
    out = subprocess.run(["gh", "api", "--paginate", path, "--jq", jq],
                         check=True, capture_output=True, text=True).stdout
    return [line for line in out.splitlines() if line.strip()]


def classify(pr: dict) -> str:
    labels = set(pr.get("labels", []))
    match = _PREFIX.match(pr["title"])
    kind = match.group("type") if match else ""
    scope = match.group("scope") if match else ""
    if "github_actions" in labels:
        return "tooling"
    if "dependencies" in labels or (kind == "build" and scope == "deps"):
        return "dependencies"
    if kind in TOOLING_TYPES:
        return "tooling"
    loose = _LOOSE_PREFIX.match(pr["title"])
    if not match and loose and loose.group("type").lower() in TOOLING_TYPES:
        return "tooling"
    return "changes"


def _display_title(pr: dict) -> str:
    title = pr["title"].strip()
    if classify(pr) == "dependencies":
        match = _PREFIX.match(title)
        if match:
            rest = match.group("rest")
            title = rest[:1].upper() + rest[1:]
    return title


def render(prs: list[dict]) -> str:
    """Grouped bullets, newest first. Empty string when there are no PRs."""
    if not prs:
        return ""
    ordered = sorted(prs, key=lambda pr: pr["merged_at"], reverse=True)
    sections = []
    for key, heading, collapsed in GROUPS:
        items = [f"- {_display_title(pr)} (#{pr['number']})"
                 for pr in ordered if classify(pr) == key]
        if not items:
            continue
        bullets = "\n".join(items)
        if collapsed:
            sections.append(f"<details><summary>{heading} ({len(items)})</summary>\n\n"
                            f"{bullets}\n\n</details>")
        else:
            sections.append(f"**{heading}**\n\n{bullets}")
    return "\n\n".join(sections) + "\n"


def resolve_commit(ref: str, api: Api) -> str | None:
    try:
        lines = api(f"repos/{REPO}/commits/{ref}", ".sha")
    except subprocess.CalledProcessError:
        return None
    return lines[0] if lines else None


def merged_prs(base: str, head: str, api: Api) -> list[dict]:
    """PRs merged into the default branch whose commits are in base...head."""
    shas = api(f"repos/{REPO}/compare/{base}...{head}?per_page=100", ".commits[].sha")
    jq = (f'.[] | select(.merged_at != null and .base.ref == "{DEFAULT_BRANCH}") '
          '| {number, title, merged_at, labels: [.labels[].name]}')
    found: dict[int, dict] = {}
    for sha in shas:
        for line in api(f"repos/{REPO}/commits/{sha}/pulls", jq):
            pr = json.loads(line)
            found[pr["number"]] = pr
    return list(found.values())


def build_fragments(head: str, previous_dev_ref: str | None, api: Api) -> tuple[str, str, str]:
    """(release tag, full fragment, delta fragment)."""
    tag = newest_release_tag(api(f"repos/{REPO}/tags?per_page=100", ".[].name"))
    if tag is None:
        raise RuntimeError("no vX.Y.Z release tag found")

    full_prs = merged_prs(tag, head, api)
    full = render(full_prs) or "_No pull requests merged since the last release._\n"

    previous = resolve_commit(previous_dev_ref, api) if previous_dev_ref else None
    if previous is None:
        delta_prs = full_prs
    elif previous == head:
        delta_prs = []
    else:
        delta_prs = merged_prs(previous, head, api)
    delta = render(delta_prs) or "_No new pull requests since the previous dev build._\n"
    return tag, full, delta


def main(argv: list[str] | None = None, api: Api = gh_api) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--head", required=True, help="Build commit SHA")
    parser.add_argument("--previous-dev", default="",
                        help="Ref of the dev build being replaced; empty on the first publish")
    parser.add_argument("--full", required=True, type=Path)
    parser.add_argument("--delta", required=True, type=Path)
    args = parser.parse_args(argv)

    try:
        tag, full, delta = build_fragments(args.head, args.previous_dev or None, api)
        full = f"Pull requests merged since {tag}, newest first.\n\n{full}"
    except (subprocess.CalledProcessError, RuntimeError, json.JSONDecodeError, KeyError) as exc:
        print(f"::warning::Could not collect the dev build change list: {exc}")
        full = delta = UNAVAILABLE

    args.full.write_text(full, encoding="utf-8")
    args.delta.write_text(delta, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
