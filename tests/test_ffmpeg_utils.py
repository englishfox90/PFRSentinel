"""Tests for services/ffmpeg_utils.py — cross-platform ffmpeg discovery (issue #37)."""
import glob
import os

import pytest

from services import ffmpeg_utils


def _no_path_ffmpeg(monkeypatch):
    monkeypatch.setattr(ffmpeg_utils.shutil, 'which', lambda name: None)


def _set_platform(monkeypatch, *, windows=False, macos=False, linux=False):
    monkeypatch.setattr(ffmpeg_utils, 'IS_WINDOWS', windows)
    monkeypatch.setattr(ffmpeg_utils, 'IS_MACOS', macos)
    monkeypatch.setattr(ffmpeg_utils, 'IS_LINUX', linux)


class TestGetFfmpegPathPathHit:
    def test_returns_shutil_which_result_when_found(self, monkeypatch):
        monkeypatch.setattr(ffmpeg_utils.shutil, 'which', lambda name: '/usr/bin/ffmpeg')
        assert ffmpeg_utils.get_ffmpeg_path() == '/usr/bin/ffmpeg'


class TestGetFfmpegPathWindows:
    def test_finds_winget_candidate(self, monkeypatch):
        _no_path_ffmpeg(monkeypatch)
        _set_platform(monkeypatch, windows=True)
        found = os.path.join('C:\\fake', 'Gyan.FFmpeg_1', 'bin', 'ffmpeg.exe')
        monkeypatch.setattr(glob, 'glob', lambda *a, **k: [found])
        monkeypatch.setattr(ffmpeg_utils.os.path, 'isfile', lambda p: p == found)
        assert ffmpeg_utils.get_ffmpeg_path() == found

    def test_falls_back_to_bare_ffmpeg_when_no_winget_install(self, monkeypatch):
        _no_path_ffmpeg(monkeypatch)
        _set_platform(monkeypatch, windows=True)
        monkeypatch.setattr(glob, 'glob', lambda *a, **k: [])
        assert ffmpeg_utils.get_ffmpeg_path() == 'ffmpeg'


class TestGetFfmpegPathMacos:
    def test_finds_first_existing_candidate(self, monkeypatch):
        _no_path_ffmpeg(monkeypatch)
        _set_platform(monkeypatch, macos=True)
        target = ffmpeg_utils.MACOS_FFMPEG_CANDIDATES[1]
        monkeypatch.setattr(ffmpeg_utils.os.path, 'isfile', lambda p: p == target)
        assert ffmpeg_utils.get_ffmpeg_path() == target

    def test_falls_back_to_bare_ffmpeg_when_none_exist(self, monkeypatch):
        _no_path_ffmpeg(monkeypatch)
        _set_platform(monkeypatch, macos=True)
        monkeypatch.setattr(ffmpeg_utils.os.path, 'isfile', lambda p: False)
        assert ffmpeg_utils.get_ffmpeg_path() == 'ffmpeg'


class TestGetFfmpegPathLinux:
    def test_finds_first_existing_candidate(self, monkeypatch):
        _no_path_ffmpeg(monkeypatch)
        _set_platform(monkeypatch, linux=True)
        target = ffmpeg_utils.LINUX_FFMPEG_CANDIDATES[2]
        monkeypatch.setattr(ffmpeg_utils.os.path, 'isfile', lambda p: p == target)
        assert ffmpeg_utils.get_ffmpeg_path() == target

    def test_falls_back_to_bare_ffmpeg_when_none_exist(self, monkeypatch):
        _no_path_ffmpeg(monkeypatch)
        _set_platform(monkeypatch, linux=True)
        monkeypatch.setattr(ffmpeg_utils.os.path, 'isfile', lambda p: False)
        assert ffmpeg_utils.get_ffmpeg_path() == 'ffmpeg'


class TestIsWingetAvailable:
    def test_false_off_windows_without_spawning_a_process(self, monkeypatch):
        _set_platform(monkeypatch, macos=True)

        def _boom(*a, **k):
            raise AssertionError("subprocess.run must not be called off Windows")

        monkeypatch.setattr(ffmpeg_utils.subprocess, 'run', _boom)
        assert ffmpeg_utils.is_winget_available() is False

    def test_true_on_windows_when_winget_exits_zero(self, monkeypatch):
        _set_platform(monkeypatch, windows=True)

        class _Result:
            returncode = 0

        monkeypatch.setattr(ffmpeg_utils.subprocess, 'run', lambda *a, **k: _Result())
        assert ffmpeg_utils.is_winget_available() is True


class TestFfmpegInstallCommand:
    def test_windows(self, monkeypatch):
        _set_platform(monkeypatch, windows=True)
        assert ffmpeg_utils.ffmpeg_install_command() is None

    def test_macos(self, monkeypatch):
        _set_platform(monkeypatch, macos=True)
        assert ffmpeg_utils.ffmpeg_install_command() == 'brew install ffmpeg'

    def test_linux(self, monkeypatch):
        _set_platform(monkeypatch, linux=True)
        assert ffmpeg_utils.ffmpeg_install_command() == 'sudo apt install ffmpeg'


class TestFfmpegInstallHint:
    def test_macos_mentions_homebrew(self, monkeypatch):
        _set_platform(monkeypatch, macos=True)
        assert 'Homebrew' in ffmpeg_utils.ffmpeg_install_hint()

    def test_linux_mentions_package_manager(self, monkeypatch):
        _set_platform(monkeypatch, linux=True)
        assert 'package manager' in ffmpeg_utils.ffmpeg_install_hint()

    def test_windows_without_winget_mentions_winget(self, monkeypatch):
        _set_platform(monkeypatch, windows=True)
        assert 'winget' in ffmpeg_utils.ffmpeg_install_hint()


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
