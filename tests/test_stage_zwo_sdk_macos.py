"""scripts/ci/stage_zwo_sdk_macos.py — the pinned ZWO download is verified by
its git blob hash before anything is built from it. No network: urlopen is
replaced, and the macOS-only patch steps aren't run here."""
import io
import os
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "scripts", "ci"))
import stage_zwo_sdk_macos as stage  # noqa: E402


def test_blob_hash_matches_git():
    # `git hash-object` of an empty file and of "hello\n".
    assert stage.git_blob_sha1(b"") == "e69de29bb2d1d6434b8b29ae775ad8c2e48c5391"
    assert stage.git_blob_sha1(b"hello\n") == "ce013625030ba8dba906f756967f9e9ca394464a"


def _serve(data):
    return patch.object(stage.urllib.request, "urlopen", return_value=io.BytesIO(data))


def test_fetch_returns_bytes_that_match_the_pin():
    with _serve(b"hello\n"):
        assert stage.fetch_verified("https://x", "ce013625030ba8dba906f756967f9e9ca394464a") == b"hello\n"


def test_fetch_refuses_anything_else():
    with _serve(b"tampered\n"), pytest.raises(SystemExit, match="blob hash"):
        stage.fetch_verified("https://x", "ce013625030ba8dba906f756967f9e9ca394464a")


def test_sources_are_pinned_to_one_commit():
    for url, blob in stage.SDK_SOURCES.values():
        assert stage.INDI_COMMIT in url
        assert len(blob) == 40
    assert "mac_arm64" in stage.SDK_SOURCES[stage.CAMERA_LIB][0]


def test_refuses_to_run_off_macos(monkeypatch):
    monkeypatch.setattr(stage.sys, "platform", "win32")
    monkeypatch.setattr(stage.sys, "argv", ["stage_zwo_sdk_macos.py"])
    assert stage.main() == 1
