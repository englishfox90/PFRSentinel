"""scripts/ci/dev_build_notes.py — names and text for the rolling dev prerelease."""
import fnmatch
import importlib.util
from functools import lru_cache
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SHA = "f5196b9e96f0e591e800fd52643b150863063237"


@lru_cache(maxsize=1)
def load_notes():
    path = REPO_ROOT / "scripts" / "ci" / "dev_build_notes.py"
    spec = importlib.util.spec_from_file_location("dev_build_notes", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_tag_does_not_fire_the_version_tag_workflows():
    text = (REPO_ROOT / ".github" / "workflows" / "tag-pushed.yml").read_text(encoding="utf-8")
    assert 'tags: ["v*"]' in text
    assert not fnmatch.fnmatchcase(load_notes().TAG, "v*")


def test_discord_workflow_skips_the_dev_tag():
    notes = load_notes()
    text = (REPO_ROOT / ".github" / "workflows" / "github-releases-to-discord.yml").read_text(
        encoding="utf-8")
    assert f"github.event.release.tag_name != '{notes.TAG}'" in text


def test_version_matches_version_py():
    import version
    assert load_notes().read_version() == version.__version__


def test_release_body_states_the_risks_and_the_source():
    notes = load_notes()
    body = notes.release_body("3.7.6", SHA, "main", "48",
                              "https://run.invalid", "https://discussion.invalid")
    assert "SmartScreen" in body and "Run anyway" in body
    assert "calibration JSON" in body and "every frame" in body
    assert "not an upgrade path" in body
    assert "Not supported" in body
    assert SHA[:7] in body and "PR #48" in body
    assert f"[{notes.DISCUSSION_TITLE}](https://discussion.invalid)" in body
    assert f"[{notes.ASSET_NAME}]({notes.ASSET_URL})" in body


def test_ref_stands_in_when_no_pr():
    body = load_notes().build_comment("3.7.6", SHA, "my-branch", None, "https://run.invalid")
    assert "`my-branch`" in body
    assert "PR #" not in body


def test_cli_treats_an_empty_pr_as_none(capsys):
    notes = load_notes()
    assert notes.main(["comment", "--sha", SHA, "--ref", "main", "--pr", "",
                       "--run-url", "https://run.invalid"]) == 0
    assert "PR #" not in capsys.readouterr().out


def test_constants_are_github_env_lines(capsys):
    notes = load_notes()
    assert notes.main(["constants"]) == 0
    lines = capsys.readouterr().out.splitlines()
    keys = dict(line.split("=", 1) for line in lines)
    assert keys["DEV_TAG"] == notes.TAG
    assert keys["DEV_ASSET_NAME"] == notes.ASSET_NAME
    assert keys["DEV_DISCUSSION_CATEGORY"] == notes.DISCUSSION_CATEGORY
