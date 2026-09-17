"""scripts/ci/dev_build_discussion.py — one dev builds thread per release cycle."""
from functools import lru_cache

import pytest

from tests.test_dev_build_notes import SHA, load_ci_script

CATEGORY_ID = "DIC_dev"


@lru_cache(maxsize=1)
def load_discussion():
    return load_ci_script("dev_build_discussion")


def _thread(number, title, closed=False, author="github-actions"):
    return {"id": f"D_{number}", "number": number, "url": f"https://discussions.invalid/{number}",
            "title": title, "closed": closed, "author": {"login": author}}


class FakeDiscussions:
    """Records mutations; answers queries from a list of threads."""

    def __init__(self, threads, categories=("Announcements", "Dev builds")):
        self.threads = threads
        self.categories = categories
        self.calls = []

    def __call__(self, query, variables):
        if "discussionCategories" in query:
            return {"repository": {"id": "R_1", "discussionCategories": {"nodes": [
                {"id": CATEGORY_ID if name == "Dev builds" else f"DIC_{name}", "name": name}
                for name in self.categories]}}}
        if "discussions(first" in query:
            assert variables["category"] == CATEGORY_ID
            return {"repository": {"discussions": {"nodes": self.threads}}}
        for name in ("createDiscussion", "updateDiscussion", "reopenDiscussion",
                     "closeDiscussion", "addDiscussionComment"):
            if name in query:
                self.calls.append((name, variables))
                if name == "createDiscussion":
                    return {name: {"discussion": _thread(99, variables["title"])}}
                if name == "addDiscussionComment":
                    return {name: {"comment": {"url": "https://discussions.invalid/c"}}}
                return {name: {"discussion": {"id": variables["id"]}}}
        raise AssertionError(f"unexpected query: {query}")

    def names(self):
        return [name for name, _ in self.calls]


def _prepare(fake, version="3.7.7-dev.14"):
    return load_discussion().prepare(version, SHA, "**Changes**\n\n- fix (#35)\n", fake)


def test_first_build_of_a_cycle_opens_its_thread_and_retires_the_old_one():
    fake = FakeDiscussions([_thread(55, "Dev builds: download and feedback")])
    result = _prepare(fake)

    assert result["created"] and result["title"] == "Dev builds: 3.7.7"
    assert fake.names() == ["createDiscussion", "addDiscussionComment", "closeDiscussion"]
    create = fake.calls[0][1]
    assert create["category"] == CATEGORY_ID and "- fix (#35)" in create["body"]
    moved = fake.calls[1][1]
    assert moved["id"] == "D_55" and "[Dev builds: 3.7.7](https://discussions.invalid/99)" in moved["body"]
    assert result["retired"] == ["https://discussions.invalid/55"]


def test_later_builds_rewrite_the_existing_post_without_opening_another():
    fake = FakeDiscussions([_thread(60, "Dev builds: 3.7.7")])
    result = _prepare(fake, "3.7.7-dev.15")

    assert not result["created"] and result["id"] == "D_60"
    assert fake.names() == ["updateDiscussion"]
    assert "3.7.7-dev.15" in fake.calls[0][1]["body"]


def test_a_closed_thread_for_this_cycle_is_reopened():
    fake = FakeDiscussions([_thread(61, "Dev builds: 3.8.0"), _thread(60, "Dev builds: 3.7.7", closed=True)])
    _prepare(fake, "3.7.7-dev.16")
    assert fake.names() == ["updateDiscussion", "reopenDiscussion", "addDiscussionComment", "closeDiscussion"]
    assert fake.calls[1][1]["id"] == "D_60" and fake.calls[3][1]["id"] == "D_61"


def test_threads_people_opened_are_never_touched():
    fake = FakeDiscussions([
        _thread(70, "Dev builds: 3.7.7", author="a-tester"),
        _thread(71, "Dev builds: crash on startup", author="a-tester"),
        _thread(72, "Dev builds: 3.7.6", closed=True),
    ])
    result = _prepare(fake)
    assert result["created"]
    assert fake.names() == ["createDiscussion"]


def test_missing_category_fails_before_any_write(capsys, tmp_path):
    fake = FakeDiscussions([], categories=("Announcements",))
    changelog = tmp_path / "changelog.md"
    changelog.write_text("x", encoding="utf-8")
    code = load_discussion().main(["prepare", "--version", "3.7.7-dev.1", "--sha", SHA,
                                   "--changelog-file", str(changelog)], gql=fake)
    assert code == 1
    assert fake.calls == []
    assert "::error::No 'Dev builds' Discussions category" in capsys.readouterr().out


def test_prepare_cli_writes_step_outputs(tmp_path, monkeypatch):
    output = tmp_path / "github_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    monkeypatch.setenv("GITHUB_REPOSITORY", "englishfox90/PFRSentinel")
    changelog = tmp_path / "changelog.md"
    changelog.write_text("x", encoding="utf-8")
    fake = FakeDiscussions([_thread(60, "Dev builds: 3.7.7")])
    assert load_discussion().main(["prepare", "--version", "3.7.7-dev.2", "--sha", SHA,
                                   "--changelog-file", str(changelog)], gql=fake) == 0
    assert output.read_text(encoding="utf-8") == "id=D_60\nurl=https://discussions.invalid/60\n"


@pytest.mark.parametrize("body", ["true", "42"])
def test_bodies_are_sent_as_strings(body, monkeypatch):
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd

        class Done:
            stdout = '{"data": {}}'
        return Done()

    module = load_discussion()
    monkeypatch.setattr(module.subprocess, "run", fake_run)
    module.gh_graphql("mutation { x }", {"body": body})
    assert captured["cmd"][-2:] == ["-f", f"body={body}"]
