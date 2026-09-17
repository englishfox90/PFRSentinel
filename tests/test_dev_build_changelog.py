"""scripts/ci/dev_build_changelog.py — PRs merged since the last release."""
import json
import subprocess
from functools import lru_cache

from tests.test_dev_build_notes import load_ci_script

HEAD = "h" * 40


@lru_cache(maxsize=1)
def load_changelog():
    return load_ci_script("dev_build_changelog")


def _pr(number, title, merged_at="2026-09-10T00:00:00Z", labels=()):
    return {"number": number, "title": title, "merged_at": merged_at, "labels": list(labels)}


class FakeGitHub:
    """Answers the gh api paths the script uses, from a commit -> PRs table."""

    def __init__(self, tags, ranges, pulls, refs=None):
        self.tags, self.ranges, self.pulls, self.refs = tags, ranges, pulls, refs or {}

    def __call__(self, path, jq):
        repo = "repos/englishfox90/PFRSentinel/"
        assert path.startswith(repo)
        path = path[len(repo):]
        if path.startswith("tags"):
            return list(self.tags)
        if path.startswith("compare/"):
            return list(self.ranges[path.split("?")[0][len("compare/"):]])
        if path.endswith("/pulls"):
            self.pulls_jq = jq
            return [json.dumps(pr) for pr in self.pulls.get(path.split("/")[1], [])]
        ref = path[len("commits/"):]
        if ref not in self.refs:
            raise subprocess.CalledProcessError(1, "gh")
        return [self.refs[ref]]


def test_classify_groups_by_prefix_and_label():
    classify = load_changelog().classify
    assert classify(_pr(1, "fix(allsky): keep labels stable")) == "changes"
    assert classify(_pr(2, "Timelapse: fix sun windows off the prime meridian")) == "changes"
    assert classify(_pr(3, "build(deps): Update opencv-python requirement")) == "dependencies"
    assert classify(_pr(4, "build(deps): Bump actions/checkout", labels=["dependencies",
                                                                        "github_actions"])) == "tooling"
    assert classify(_pr(5, "ci: run tests on three OSes")) == "tooling"
    assert classify(_pr(6, "test(nina-ui): dispose detached panels")) == "tooling"
    assert classify(_pr(7, "CI pipeline: tests, lint, size audit")) == "tooling"
    assert classify(_pr(8, "perf: halve peak memory")) == "changes"


def test_render_orders_newest_first_and_folds_tooling():
    text = load_changelog().render([
        _pr(30, "Timelapse: fix sun windows", "2026-09-16T04:00:00Z"),
        _pr(35, "fix(allsky): keep labels stable", "2026-09-16T18:00:00Z"),
        _pr(24, "build(deps): update opencv-python to >=5.0", "2026-09-15T05:00:00Z"),
        _pr(51, "ci: publish dev builds", "2026-09-17T03:00:00Z"),
    ])
    assert text.index("(#35)") < text.index("(#30)")
    assert "**Dependencies**\n\n- Update opencv-python to >=5.0 (#24)" in text
    assert "<details><summary>CI, tests and tooling (1)</summary>\n\n- ci: publish dev builds (#51)" in text
    assert load_changelog().render([]) == ""


def test_prs_are_found_through_commits_deduplicated_and_limited_to_main():
    main_pr = _pr(44, "feat(paths): resolve app data cross-platform")
    github = FakeGitHub(
        tags=["v3.7.6", "v3.7.0", "3.6.6", "dev-latest"],
        ranges={f"v3.7.6...{HEAD}": ["c1", "c2", "c3"]},
        # c1 and c2 are two commits of one merge-commit PR; c3 was pushed straight to main.
        pulls={"c1": [main_pr], "c2": [main_pr]},
    )
    prs = load_changelog().merged_prs("v3.7.6", HEAD, github)
    assert [pr["number"] for pr in prs] == [44]
    # The jq filter is what drops unmerged PRs, and PRs merged into other
    # branches (a CI auto-fix PR merged into a feature branch, say).
    assert 'select(.merged_at != null and .base.ref == "main")' in github.pulls_jq


def test_delta_is_measured_from_the_previous_dev_build():
    github = FakeGitHub(
        tags=["v3.7.6"],
        ranges={f"v3.7.6...{HEAD}": ["a", "b"], f"prev...{HEAD}": ["b"]},
        pulls={"a": [_pr(48, "feat(dev-mode): raw capture on")],
               "b": [_pr(35, "fix(allsky): keep labels stable")]},
        refs={"dev-latest": "prev"},
    )
    tag, full, delta = load_changelog().build_fragments(HEAD, "dev-latest", github)
    assert tag == "v3.7.6"
    assert "(#48)" in full and "(#35)" in full
    assert "(#35)" in delta and "(#48)" not in delta


def test_first_publish_uses_the_full_list_and_a_rebuild_has_no_delta():
    github = FakeGitHub(tags=["v3.7.6"], ranges={f"v3.7.6...{HEAD}": ["a"]},
                        pulls={"a": [_pr(48, "feat(dev-mode): raw capture on")]},
                        refs={})
    _, full, delta = load_changelog().build_fragments(HEAD, "dev-latest", github)
    assert delta == full

    github.refs = {"dev-latest": HEAD}
    _, _, delta = load_changelog().build_fragments(HEAD, "dev-latest", github)
    assert "No new pull requests" in delta


def test_an_api_failure_writes_a_placeholder_instead_of_blocking(tmp_path, capsys):
    def broken(path, jq):
        raise subprocess.CalledProcessError(1, "gh")

    full, delta = tmp_path / "full.md", tmp_path / "delta.md"
    assert load_changelog().main(["--head", HEAD, "--full", str(full), "--delta", str(delta)],
                                 api=broken) == 0
    assert "could not be collected" in full.read_text(encoding="utf-8")
    assert delta.read_text(encoding="utf-8") == full.read_text(encoding="utf-8")
    assert "::warning::" in capsys.readouterr().out
