"""Names and text for the rolling dev prerelease that build.yml publishes.

The dev channel is one GitHub prerelease under a fixed tag, replaced on every
publish, plus one Discussions thread per release cycle ("Dev builds: 3.7.7")
that gets a comment per build. The release hosts the binary; the thread hosts
the conversation, because a discussion has no API for attaching files.

Load-bearing names, read by the workflow from ``constants`` rather than
repeated there:

- ``TAG`` must not match ``v*``. That pattern fires tag-pushed.yml and, through
  it, the Claude release-notes draft, which would try to write notes for a dev
  build.
- ``ASSET_NAME`` must not look like a production installer to
  services/update_checker.py, which takes the first asset ending ``.exe`` with
  ``setup`` in its name. The updater polls ``/releases/latest``, which excludes
  prereleases, so this is the second line of defence, not the first. The name
  is fixed so its download URL always serves the newest build.
- ``discussion_title`` is how dev_build_discussion.py finds a cycle's thread
  again. Renaming a thread on GitHub makes the next publish open a new one.

The change lists come from dev_build_changelog.py as Markdown fragments.

Run on the runner's system Python, so stdlib only.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO = "englishfox90/PFRSentinel"

TAG = "dev-latest"
ASSET_NAME = "PFRSentinel-dev-unsigned-installer.exe"
DISCUSSION_CATEGORY = "Dev builds"
DISCUSSION_TITLE_PREFIX = "Dev builds: "

RELEASE_URL = f"https://github.com/{REPO}/releases/tag/{TAG}"
ASSET_URL = f"https://github.com/{REPO}/releases/download/{TAG}/{ASSET_NAME}"
PRODUCTION_URL = f"https://github.com/{REPO}/releases/latest"


def release_line(version: str) -> str:
    """The release a dev version leads up to: ``3.7.7-dev.14`` -> ``3.7.7``."""
    match = re.match(r"^(\d+\.\d+\.\d+)", version)
    if not match:
        raise ValueError(f"unrecognised dev version {version!r}")
    return match.group(1)


def discussion_title(version: str) -> str:
    return f"{DISCUSSION_TITLE_PREFIX}{release_line(version)}"


def release_title(version: str, sha: str) -> str:
    return f"Dev build {version} ({sha[:7]})"


def _source_line(sha: str, ref: str, pr: str | None) -> str:
    commit = f"[`{sha[:7]}`](https://github.com/{REPO}/commit/{sha})"
    origin = f"PR #{pr}" if pr else f"`{ref}`"
    return f"Built from {commit} ({origin})"


def before_you_install() -> str:
    return "\n".join([
        "### Before you install",
        "",
        "- **Windows SmartScreen will warn.** The installer is unsigned. Choose "
        "*More info* → *Run anyway*.",
        "- **Raw capture is on by default.** On a fresh config a dev build writes a "
        "raw FITS file and a calibration JSON for *every frame* into the `raw_debug` "
        "folder. On a rig capturing all night that adds up to real disk. Turn it off "
        "under *Image Processing* → *Developer Mode* → *Enable Dev Mode*. An existing "
        "config keeps whatever it already had.",
        "- **It replaces your installed Sentinel.** Dev and production builds install "
        "to the same place and share `%APPDATA%\\PFRSentinel`. This is not an upgrade "
        "path. The in-app updater never offers dev builds, and offers a release only "
        "once one newer than this build ships, such as the release this build leads "
        "up to. To get back to a supported build sooner, reinstall the "
        f"[latest release]({PRODUCTION_URL}).",
        "- **Not supported.** It exists to test a change before it ships. Please "
        "report what you find, but don't run an observatory you rely on with it.",
    ])


def release_body(version: str, sha: str, ref: str, pr: str | None, run_url: str,
                 discussion_url: str, changelog: str) -> str:
    return "\n".join([
        "> [!WARNING]",
        "> Unsigned, unsupported test build of PFR Sentinel. Not a release.",
        "",
        f"{_source_line(sha, ref, pr)}, version {version}. [Build log]({run_url}).",
        "",
        f"Download: [{ASSET_NAME}]({ASSET_URL}). That link always serves the newest "
        "dev build, and this release is replaced each time one is published.",
        "",
        f"Feedback and questions go in [{discussion_title(version)}]({discussion_url}).",
        "",
        "### What's in this build",
        "",
        changelog.rstrip(),
        "",
        before_you_install(),
        "",
    ])


def discussion_intro(version: str, sha: str, changelog: str) -> str:
    line = release_line(version)
    return "\n".join([
        f"Dev builds leading up to PFR Sentinel {line}: test builds of changes that "
        "have not been released yet. A new build is published every time a change "
        "merges, and announced as a comment below, so subscribe to hear about them.",
        "",
        f"- **Download the newest build:** [{ASSET_NAME}]({ASSET_URL})",
        f"- **Release notes:** [the dev prerelease]({RELEASE_URL})",
        "",
        "**Reply under the comment for the build you tested**, so feedback stays with "
        "the build it is about. Only the newest build can be downloaded; the link "
        f"above always serves it. When {line} is released this thread closes and "
        "dev builds move to a new one.",
        "",
        f"### What's in the newest build ({version}, `{sha[:7]}`)",
        "",
        changelog.rstrip(),
        "",
        before_you_install(),
        "",
    ])


def build_comment(version: str, sha: str, ref: str, pr: str | None, run_url: str,
                  delta: str) -> str:
    return "\n".join([
        f"**New dev build: {version}.** {_source_line(sha, ref, pr)}.",
        "",
        f"[Download]({ASSET_URL}) · [Release notes]({RELEASE_URL}) · [Build log]({run_url})",
        "",
        "**New since the previous dev build**",
        "",
        delta.rstrip(),
        "",
        "Reply to this comment with what you found in this build. The download link "
        "serves the newest build, so after the next one is published it no longer "
        "fetches this one.",
        "",
    ])


def superseded_comment(new_title: str, new_url: str) -> str:
    return (f"Dev builds have moved on to [{new_title}]({new_url}). This thread is "
            "closed as outdated; the builds it covered can no longer be downloaded.\n")


def constants() -> str:
    """KEY=value lines for $GITHUB_ENV."""
    return "\n".join([
        f"DEV_TAG={TAG}",
        f"DEV_ASSET_NAME={ASSET_NAME}",
        f"DEV_DISCUSSION_CATEGORY={DISCUSSION_CATEGORY}",
        "",
    ])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("constants", help="KEY=value lines for $GITHUB_ENV")

    build_args = argparse.ArgumentParser(add_help=False)
    # Stamped by set_dev_version.py on the build job. This job's own checkout
    # still holds the committed version, so it cannot be read from version.py.
    build_args.add_argument("--version", required=True, help="e.g. 3.7.7-dev.14")
    build_args.add_argument("--sha", required=True)
    build_args.add_argument("--ref", required=True)
    build_args.add_argument("--pr", default="", help="PR number; empty when none")
    build_args.add_argument("--run-url", required=True)

    sub.add_parser("title", parents=[build_args], help="Release title")
    release = sub.add_parser("release", parents=[build_args], help="Release body")
    release.add_argument("--discussion-url", required=True)
    release.add_argument("--changelog-file", required=True, type=Path)
    comment = sub.add_parser("comment", parents=[build_args], help="Per-build discussion comment")
    comment.add_argument("--delta-file", required=True, type=Path)

    args = parser.parse_args(argv)
    pr = getattr(args, "pr", "") or None

    if args.command == "constants":
        text = constants()
    elif args.command == "title":
        text = release_title(args.version, args.sha) + "\n"
    elif args.command == "release":
        text = release_body(args.version, args.sha, args.ref, pr, args.run_url,
                            args.discussion_url,
                            args.changelog_file.read_text(encoding="utf-8"))
    else:
        text = build_comment(args.version, args.sha, args.ref, pr, args.run_url,
                             args.delta_file.read_text(encoding="utf-8"))

    sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
