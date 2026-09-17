"""Stamp a dev version such as ``3.7.7-dev.14`` into version.py before freezing.

A dev build used to carry the committed version, the same number as the
production release. A tester's bug report, diagnostics bundle and analytics
events were then indistinguishable from a production user's.

- ``3.7.7`` is the patch after the newest ``vX.Y.Z`` tag, or the committed
  version when it has already been bumped past that release.
- ``14`` counts commits reachable from HEAD but not from that tag, so it rises
  along main and is the same whenever one commit is rebuilt.

The suffix follows the semver rule that ``3.7.7-dev.14`` sorts *before*
``3.7.7``, which is how services/update_checker.py compares them: a tester on
a dev build is offered the release it leads up to.

Windows version resources hold four integers, so PFRSentinel.spec puts the
counter in the fourth slot (``3.7.7.14``); a production build is ``3.7.7.0``.

Needs every tag and full history (``fetch-depth: 0``). With a shallow clone it
fails loudly rather than guessing, because a wrong base version would ship.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
VERSION_FILE = REPO_ROOT / "version.py"

# Windows VS_FIXEDFILEINFO fields are 16-bit.
MAX_COUNTER = 65535

_RELEASE_TAG = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")
_PLAIN_VERSION = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
_ASSIGNMENT = re.compile(r"""^__version__\s*=\s*["']([^"']+)["'][^\n]*$""", re.MULTILINE)


def newest_release_tag(tags: list[str]) -> str | None:
    releases = [t for t in tags if _RELEASE_TAG.match(t)]
    if not releases:
        return None
    return max(releases, key=lambda t: tuple(int(p) for p in _RELEASE_TAG.match(t).groups()))


def dev_version(committed: str, release_tag: str, commits_since: int) -> str:
    match = _PLAIN_VERSION.match(committed)
    if not match:
        raise ValueError(f"committed version {committed!r} is not X.Y.Z; "
                         f"was version.py already stamped?")
    base = tuple(int(p) for p in match.groups())
    released = tuple(int(p) for p in _RELEASE_TAG.match(release_tag).groups())
    if base <= released:
        base = (released[0], released[1], released[2] + 1)
    if not 0 <= commits_since <= MAX_COUNTER:
        raise ValueError(f"commit counter {commits_since} does not fit a Windows "
                         f"version field (0..{MAX_COUNTER})")
    return f"{base[0]}.{base[1]}.{base[2]}-dev.{commits_since}"


def file_version(version: str) -> str:
    """The four-integer form PFRSentinel.spec writes into the exe."""
    match = re.match(r"^(\d+)\.(\d+)\.(\d+)(?:-dev\.(\d+))?$", version)
    if not match:
        raise ValueError(f"unrecognised version {version!r}")
    return ".".join(g or "0" for g in match.groups())


def stamp(path: Path, version: str) -> str:
    """Rewrite the assignment in place; returns the version it replaced."""
    source = path.read_text(encoding="utf-8")
    found = _ASSIGNMENT.findall(source)
    if len(found) != 1:
        raise ValueError(f"expected one __version__ assignment in {path}, found {len(found)}")
    path.write_text(_ASSIGNMENT.sub(f'__version__ = "{version}"', source, count=1),
                    encoding="utf-8")
    if _ASSIGNMENT.findall(path.read_text(encoding="utf-8")) != [version]:
        raise ValueError(f"wrote {version} to {path} but it did not read back")
    return found[0]


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=REPO_ROOT, check=True,
                          capture_output=True, text=True).stdout.strip()


def main() -> int:
    try:
        tag = newest_release_tag(_git("tag", "--list", "v*").splitlines())
        if tag is None:
            print("FAIL: no vX.Y.Z tags in this clone. The checkout needs "
                  "fetch-depth: 0 so tags and history are present.", file=sys.stderr)
            return 1
        if _git("rev-parse", "--is-shallow-repository") == "true":
            print("FAIL: shallow clone, so the commit count would be wrong. "
                  "Set fetch-depth: 0 on the checkout.", file=sys.stderr)
            return 1
        commits = int(_git("rev-list", "--count", f"{tag}..HEAD"))
        committed = _ASSIGNMENT.findall(VERSION_FILE.read_text(encoding="utf-8"))[0]
        version = dev_version(committed, tag, commits)
        stamp(VERSION_FILE, version)
    except (ValueError, IndexError, subprocess.CalledProcessError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1

    print(f"Dev version: {version} (committed {committed}, {commits} commits since {tag})")
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as fh:
            fh.write(f"version={version}\nfile_version={file_version(version)}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
