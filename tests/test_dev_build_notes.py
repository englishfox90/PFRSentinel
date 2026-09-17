"""scripts/ci/dev_build_notes.py — names and text for the rolling dev prerelease."""
import fnmatch
import importlib.util
import sys
from functools import lru_cache
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
CI_SCRIPTS = REPO_ROOT / "scripts" / "ci"
SHA = "f5196b9e96f0e591e800fd52643b150863063237"
CHANGELOG = "**Changes**\n\n- fix(allsky): keep overlay labels stable (#35)\n"


def load_ci_script(name):
    """Import a scripts/ci module; they import their siblings by bare name."""
    if str(CI_SCRIPTS) not in sys.path:
        sys.path.insert(0, str(CI_SCRIPTS))
    spec = importlib.util.spec_from_file_location(name, CI_SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@lru_cache(maxsize=1)
def load_notes():
    return load_ci_script("dev_build_notes")


def test_tag_does_not_fire_the_version_tag_workflows():
    text = (REPO_ROOT / ".github" / "workflows" / "tag-pushed.yml").read_text(encoding="utf-8")
    assert 'tags: ["v*"]' in text
    assert not fnmatch.fnmatchcase(load_notes().TAG, "v*")


def test_discord_workflow_skips_the_dev_tag():
    notes = load_notes()
    text = (REPO_ROOT / ".github" / "workflows" / "github-releases-to-discord.yml").read_text(
        encoding="utf-8")
    assert f"github.event.release.tag_name != '{notes.TAG}'" in text


def test_thread_title_follows_the_release_line():
    notes = load_notes()
    assert notes.discussion_title("3.7.7-dev.14") == "Dev builds: 3.7.7"
    assert notes.discussion_title("3.8.0-dev.0") == "Dev builds: 3.8.0"
    with pytest.raises(ValueError):
        notes.discussion_title("dev")


def test_release_body_states_the_risks_the_source_and_the_changes():
    notes = load_notes()
    body = notes.release_body("3.7.7-dev.14", SHA, "main", "48", "https://run.invalid",
                              "https://discussion.invalid", CHANGELOG)
    assert "SmartScreen" in body and "Run anyway" in body
    assert "calibration JSON" in body and "every frame" in body
    assert "not an upgrade path" in body
    assert "Not supported" in body
    assert SHA[:7] in body and "PR #48" in body
    assert "version 3.7.7-dev.14" in body
    assert "[Dev builds: 3.7.7](https://discussion.invalid)" in body
    assert f"[{notes.ASSET_NAME}]({notes.ASSET_URL})" in body
    assert "### What's in this build\n\n**Changes**" in body


def test_intro_carries_the_newest_changelog_and_asks_for_replies_per_build():
    intro = load_notes().discussion_intro("3.7.7-dev.14", SHA, CHANGELOG)
    assert "leading up to PFR Sentinel 3.7.7" in intro
    assert f"newest build (3.7.7-dev.14, `{SHA[:7]}`)" in intro
    assert "keep overlay labels stable (#35)" in intro
    assert "Reply under the comment for the build you tested" in intro
    assert "SmartScreen" in intro


def test_ref_stands_in_when_no_pr():
    body = load_notes().build_comment("3.7.7-dev.14", SHA, "my-branch", None,
                                      "https://run.invalid", CHANGELOG)
    assert "`my-branch`" in body
    assert "PR #" not in body


def test_comment_cli_reads_the_delta_and_treats_an_empty_pr_as_none(tmp_path, capsys):
    delta = tmp_path / "delta.md"
    delta.write_text(CHANGELOG, encoding="utf-8")
    assert load_notes().main(["comment", "--version", "3.7.7-dev.14", "--sha", SHA,
                              "--ref", "main", "--pr", "", "--run-url", "https://run.invalid",
                              "--delta-file", str(delta)]) == 0
    out = capsys.readouterr().out
    assert "PR #" not in out
    assert "New dev build: 3.7.7-dev.14" in out
    assert "**New since the previous dev build**\n\n**Changes**" in out


def test_title_carries_the_stamped_version(capsys):
    notes = load_notes()
    assert notes.main(["title", "--version", "3.7.7-dev.14", "--sha", SHA, "--ref", "main",
                       "--run-url", "https://run.invalid"]) == 0
    assert capsys.readouterr().out.strip() == f"Dev build 3.7.7-dev.14 ({SHA[:7]})"


def test_constants_are_github_env_lines(capsys):
    notes = load_notes()
    assert notes.main(["constants"]) == 0
    lines = capsys.readouterr().out.splitlines()
    keys = dict(line.split("=", 1) for line in lines)
    assert keys["DEV_TAG"] == notes.TAG
    assert keys["DEV_ASSET_NAME"] == notes.ASSET_NAME
    assert keys["DEV_DISCUSSION_CATEGORY"] == notes.DISCUSSION_CATEGORY
