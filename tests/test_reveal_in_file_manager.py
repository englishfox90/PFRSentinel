"""Tests for services/reveal_in_file_manager.py — one launch path per platform."""
import os
import subprocess

import pytest

from services import reveal_in_file_manager as rifm


class _PopenRecorder:
    """Stand-in for subprocess.Popen that records the call instead of launching."""

    def __init__(self, raise_on_call=False):
        self.calls = []
        self._raise_on_call = raise_on_call

    def __call__(self, args, **kwargs):
        self.calls.append((args, kwargs))
        if self._raise_on_call:
            raise OSError("boom")
        return object()


@pytest.fixture
def sample_file(tmp_path):
    f = tmp_path / "note.txt"
    f.write_text("hi")
    return f


class TestRevealPathFileSelectWindows:
    def test_uses_explorer_select(self, monkeypatch, sample_file):
        monkeypatch.setattr(rifm.sys, "platform", "win32")
        recorder = _PopenRecorder()
        monkeypatch.setattr(subprocess, "Popen", recorder)

        assert rifm.reveal_path(str(sample_file), select=True) is True

        args, kwargs = recorder.calls[0]
        assert args == ["explorer", f"/select,{sample_file}"]
        assert "start_new_session" not in kwargs
        assert kwargs["stdout"] == subprocess.DEVNULL
        assert kwargs["stderr"] == subprocess.DEVNULL


class TestRevealPathFileSelectMacOS:
    def test_uses_open_dash_r(self, monkeypatch, sample_file):
        monkeypatch.setattr(rifm.sys, "platform", "darwin")
        recorder = _PopenRecorder()
        monkeypatch.setattr(subprocess, "Popen", recorder)

        assert rifm.reveal_path(str(sample_file), select=True) is True

        args, kwargs = recorder.calls[0]
        assert args == ["open", "-R", str(sample_file)]
        assert kwargs["start_new_session"] is True


class TestRevealPathFileSelectLinux:
    def test_opens_containing_directory(self, monkeypatch, sample_file):
        monkeypatch.setattr(rifm.sys, "platform", "linux")
        recorder = _PopenRecorder()
        monkeypatch.setattr(subprocess, "Popen", recorder)

        assert rifm.reveal_path(str(sample_file), select=True) is True

        args, kwargs = recorder.calls[0]
        assert args == ["xdg-open", str(sample_file.parent)]
        assert kwargs["start_new_session"] is True


class TestRevealPathDirectory:
    def test_windows_uses_startfile(self, monkeypatch, tmp_path):
        monkeypatch.setattr(rifm.sys, "platform", "win32")
        calls = []
        monkeypatch.setattr(os, "startfile", lambda p: calls.append(p), raising=False)

        assert rifm.reveal_path(str(tmp_path)) is True
        assert calls == [str(tmp_path)]

    def test_macos_uses_open(self, monkeypatch, tmp_path):
        monkeypatch.setattr(rifm.sys, "platform", "darwin")
        recorder = _PopenRecorder()
        monkeypatch.setattr(subprocess, "Popen", recorder)

        assert rifm.reveal_path(str(tmp_path)) is True
        args, kwargs = recorder.calls[0]
        assert args == ["open", str(tmp_path)]

    def test_linux_uses_xdg_open(self, monkeypatch, tmp_path):
        monkeypatch.setattr(rifm.sys, "platform", "linux")
        recorder = _PopenRecorder()
        monkeypatch.setattr(subprocess, "Popen", recorder)

        assert rifm.reveal_path(str(tmp_path)) is True
        args, kwargs = recorder.calls[0]
        assert args == ["xdg-open", str(tmp_path)]


class TestRevealPathFileSelectFalse:
    def test_opens_parent_directory_on_windows(self, monkeypatch, sample_file):
        monkeypatch.setattr(rifm.sys, "platform", "win32")
        calls = []
        monkeypatch.setattr(os, "startfile", lambda p: calls.append(p), raising=False)

        assert rifm.reveal_path(str(sample_file), select=False) is True
        assert calls == [str(sample_file.parent)]

    def test_opens_parent_directory_on_linux(self, monkeypatch, sample_file):
        monkeypatch.setattr(rifm.sys, "platform", "linux")
        recorder = _PopenRecorder()
        monkeypatch.setattr(subprocess, "Popen", recorder)

        assert rifm.reveal_path(str(sample_file), select=False) is True
        args, kwargs = recorder.calls[0]
        assert args == ["xdg-open", str(sample_file.parent)]


class TestOpenPath:
    def test_windows_uses_startfile(self, monkeypatch, sample_file):
        monkeypatch.setattr(rifm.sys, "platform", "win32")
        calls = []
        monkeypatch.setattr(os, "startfile", lambda p: calls.append(p), raising=False)

        assert rifm.open_path(str(sample_file)) is True
        assert calls == [str(sample_file)]

    def test_macos_uses_open(self, monkeypatch, sample_file):
        monkeypatch.setattr(rifm.sys, "platform", "darwin")
        recorder = _PopenRecorder()
        monkeypatch.setattr(subprocess, "Popen", recorder)

        assert rifm.open_path(str(sample_file)) is True
        args, kwargs = recorder.calls[0]
        assert args == ["open", str(sample_file)]

    def test_linux_uses_xdg_open(self, monkeypatch, sample_file):
        monkeypatch.setattr(rifm.sys, "platform", "linux")
        recorder = _PopenRecorder()
        monkeypatch.setattr(subprocess, "Popen", recorder)

        assert rifm.open_path(str(sample_file)) is True
        args, kwargs = recorder.calls[0]
        assert args == ["xdg-open", str(sample_file)]


class TestFailureModes:
    def test_missing_path_returns_false_without_launching(self, monkeypatch, tmp_path):
        monkeypatch.setattr(rifm.sys, "platform", "linux")
        recorder = _PopenRecorder()
        monkeypatch.setattr(subprocess, "Popen", recorder)
        missing = tmp_path / "does-not-exist.txt"

        assert rifm.reveal_path(str(missing)) is False
        assert rifm.open_path(str(missing)) is False
        assert recorder.calls == []

    def test_empty_path_returns_false(self):
        assert rifm.reveal_path("") is False
        assert rifm.open_path("") is False

    def test_popen_failure_returns_false_not_raises(self, monkeypatch, tmp_path):
        monkeypatch.setattr(rifm.sys, "platform", "linux")
        recorder = _PopenRecorder(raise_on_call=True)
        monkeypatch.setattr(subprocess, "Popen", recorder)

        assert rifm.reveal_path(str(tmp_path)) is False

    def test_startfile_failure_returns_false_not_raises(self, monkeypatch, sample_file):
        monkeypatch.setattr(rifm.sys, "platform", "win32")

        def _raise(p):
            raise OSError("no association")

        monkeypatch.setattr(os, "startfile", _raise, raising=False)

        assert rifm.open_path(str(sample_file)) is False
