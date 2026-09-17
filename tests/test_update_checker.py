"""Production installs must never be offered a dev build.

The dev channel is a rolling GitHub prerelease (build.yml publish-dev). The
updater polls /releases/latest, which GitHub defines as excluding prereleases,
so these cover the two guards behind that: the updater's own prerelease check,
and the dev asset's name not matching the installer rule.
"""
from unittest.mock import MagicMock, patch

import pytest

from services import update_checker
from services.update_checker import UpdateChecker, compare_versions
from tests.test_dev_build_notes import load_notes


@pytest.fixture
def checker(tmp_path):
    instance = UpdateChecker()
    instance._cache_path = tmp_path / "update_check_cache.json"
    return instance


def _release(**overrides):
    data = {
        "tag_name": "v99.0.0",
        "name": "v99.0.0",
        "body": "notes",
        "prerelease": False,
        "draft": False,
        "published_at": "2026-09-16T00:00:00Z",
        "html_url": "https://example.invalid/release",
        "assets": [{"name": "PFRSentinel-99.0.0-setup.exe", "size": 1024,
                    "browser_download_url": "https://example.invalid/setup.exe"}],
    }
    data.update(overrides)
    return data


def _check(checker, release):
    response = MagicMock()
    response.json.return_value = release
    with patch.object(update_checker.requests, "get", return_value=response):
        return checker.check_for_update(force=True)


def test_newer_release_is_offered(checker):
    info = _check(checker, _release())
    assert info is not None
    assert info.download_url == "https://example.invalid/setup.exe"


@pytest.mark.parametrize("flag", ["prerelease", "draft"])
def test_prerelease_or_draft_is_never_offered(checker, flag):
    assert _check(checker, _release(**{flag: True})) is None


def test_dev_installer_asset_is_not_picked_as_the_installer(checker):
    notes = load_notes()
    asset = {"name": notes.ASSET_NAME, "size": 1024,
             "browser_download_url": notes.ASSET_URL}
    info = _check(checker, _release(assets=[asset]))
    assert info is not None
    assert info.download_url == ""


@pytest.mark.parametrize("current, latest, expected", [
    ("3.7.6", "3.7.7", -1),
    ("3.7.7", "v3.7.7", 0),
    ("3.7.7-dev.14", "3.7.7", -1),
    ("3.7.7-dev.14", "3.7.6", 1),
    ("3.7.7-dev.2", "3.7.7-dev.14", -1),
    ("3.2.5-beta", "3.2.5", -1),
    ("3.2.10", "3.2.9", 1),
])
def test_compare_versions_ranks_suffixed_builds_below_their_release(current, latest, expected):
    assert compare_versions(current, latest) == expected


def test_dev_build_is_offered_the_release_it_leads_up_to(checker, monkeypatch):
    monkeypatch.setattr(update_checker, "__version__", "99.0.0-dev.14")
    info = _check(checker, _release())
    assert info is not None
    assert info.latest_version == "99.0.0"
