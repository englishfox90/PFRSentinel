"""Names and text for the rolling dev prerelease that build.yml publishes.

The dev channel is one GitHub prerelease under a fixed tag, replaced on every
publish, plus one Discussions thread that gets a comment per build. The release
hosts the binary; the thread hosts the conversation, because a discussion has
no API for attaching files.

Three names here are load-bearing, and the workflow reads them from
``constants`` rather than repeating them:

- ``TAG`` must not match ``v*``. That pattern fires tag-pushed.yml and, through
  it, the Claude release-notes draft, which would try to write notes for a dev
  build.
- ``ASSET_NAME`` must not look like a production installer to
  services/update_checker.py, which takes the first asset ending ``.exe`` with
  ``setup`` in its name. The updater polls ``/releases/latest``, which excludes
  prereleases, so this is the second line of defence, not the first. The name
  is fixed so its download URL always serves the newest build.
- ``DISCUSSION_TITLE`` is how the workflow finds the thread again. Renaming the
  thread on GitHub makes the next publish open a new one.

Run on the runner's system Python, so stdlib only.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
REPO = "englishfox90/PFRSentinel"

TAG = "dev-latest"
ASSET_NAME = "PFRSentinel-dev-unsigned-installer.exe"
DISCUSSION_CATEGORY = "Dev builds"
DISCUSSION_TITLE = "Dev builds: download and feedback"

RELEASE_URL = f"https://github.com/{REPO}/releases/tag/{TAG}"
ASSET_URL = f"https://github.com/{REPO}/releases/download/{TAG}/{ASSET_NAME}"
PRODUCTION_URL = f"https://github.com/{REPO}/releases/latest"


def read_version() -> str:
    source = (REPO_ROOT / "version.py").read_text(encoding="utf-8")
    match = re.search(r"""^__version__\s*=\s*["']([^"']+)["']""", source, re.MULTILINE)
    if not match:
        raise SystemExit(f"FAIL: no __version__ assignment in {REPO_ROOT / 'version.py'}")
    return match.group(1)


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
        "path: the in-app updater compares version numbers only, so it will not offer "
        "a release with the same number as this build. To get back to a supported "
        f"build, reinstall from the [latest release]({PRODUCTION_URL}).",
        "- **Not supported.** It exists to test a change before it ships. Please "
        "report what you find, but don't run an observatory you rely on with it.",
    ])


def release_body(version: str, sha: str, ref: str, pr: str | None,
                 run_url: str, discussion_url: str) -> str:
    return "\n".join([
        "> [!WARNING]",
        "> Unsigned, unsupported test build of PFR Sentinel. Not a release.",
        "",
        f"{_source_line(sha, ref, pr)}, version {version}. [Build log]({run_url}).",
        "",
        f"Download: [{ASSET_NAME}]({ASSET_URL}). That link always serves the newest "
        "dev build, and this release is replaced each time one is published.",
        "",
        f"Feedback and questions go in [{DISCUSSION_TITLE}]({discussion_url}).",
        "",
        before_you_install(),
        "",
    ])


def discussion_intro() -> str:
    return "\n".join([
        "This thread is the home for PFR Sentinel dev builds: test builds of changes "
        "that have not been released yet.",
        "",
        f"- **Download the newest build:** [{ASSET_NAME}]({ASSET_URL})",
        f"- **What it was built from:** [the dev prerelease]({RELEASE_URL})",
        "",
        "Each new build is announced as a comment below, so subscribe to the thread "
        "to hear about them. Only the newest build is kept; the download link above "
        "always serves it. Reply here with what you tried and what happened.",
        "",
        before_you_install(),
        "",
    ])


def build_comment(version: str, sha: str, ref: str, pr: str | None, run_url: str) -> str:
    return "\n".join([
        f"**New dev build: {version}.** {_source_line(sha, ref, pr)}.",
        "",
        f"[Download]({ASSET_URL}) · [Release notes]({RELEASE_URL}) · [Build log]({run_url})",
        "",
        "The download link serves the newest build, so after the next one is "
        "published it no longer fetches this one.",
        "",
    ])


def constants() -> str:
    """KEY=value lines for $GITHUB_ENV."""
    return "\n".join([
        f"DEV_TAG={TAG}",
        f"DEV_ASSET_NAME={ASSET_NAME}",
        f"DEV_DISCUSSION_CATEGORY={DISCUSSION_CATEGORY}",
        f"DEV_DISCUSSION_TITLE={DISCUSSION_TITLE}",
        f"DEV_VERSION={read_version()}",
        "",
    ])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("constants", help="KEY=value lines for $GITHUB_ENV")
    sub.add_parser("intro", help="Body of a newly opened discussion")

    build_args = argparse.ArgumentParser(add_help=False)
    build_args.add_argument("--sha", required=True)
    build_args.add_argument("--ref", required=True)
    build_args.add_argument("--pr", default="", help="PR number; empty when none")
    build_args.add_argument("--run-url", required=True)

    sub.add_parser("title", parents=[build_args], help="Release title")
    release = sub.add_parser("release", parents=[build_args], help="Release body")
    release.add_argument("--discussion-url", required=True)
    sub.add_parser("comment", parents=[build_args], help="Per-build discussion comment")

    args = parser.parse_args(argv)
    pr = getattr(args, "pr", "") or None

    if args.command == "constants":
        text = constants()
    elif args.command == "intro":
        text = discussion_intro()
    elif args.command == "title":
        text = release_title(read_version(), args.sha) + "\n"
    elif args.command == "release":
        text = release_body(read_version(), args.sha, args.ref, pr,
                            args.run_url, args.discussion_url)
    else:
        text = build_comment(read_version(), args.sha, args.ref, pr, args.run_url)

    sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
