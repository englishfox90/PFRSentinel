"""
Tests for services.utils_paths — the cross-platform app-data root.

get_app_data_dir has two branches: the Windows LOCALAPPDATA environment
variable, and platformdirs for everything else. Patching sys.platform is
enough to select the first. It is not enough for the second — platformdirs
binds its implementation class at import time from sys.platform — so those
tests also swap services.utils_paths.user_data_dir for a shim that drives the
real platformdirs class for the platform under test, keeping the assertions
against genuine path-building rather than a hand-rolled fake. Windows' own
known-folder resolver is unavailable off Windows, so the Windows fixture
stands in for it with platformdirs' WIN_PD_OVERRIDE_LOCAL_APPDATA hook.
Every platform fixture pins sys.platform, so the suite behaves the same on
the Windows CI runner as it does here.
"""
import os

import pytest
from platformdirs import macos as pd_macos
from platformdirs import unix as pd_unix
from platformdirs import windows as pd_windows

from services import app_config, utils_paths
from services.app_config import APP_DATA_FOLDER


def _platform_shim(dirs_class):
    """Build a user_data_dir replacement backed by a specific platformdirs class."""
    def _user_data_dir(appname=None, appauthor=None, **kwargs):
        return dirs_class(appname=appname, appauthor=appauthor, **kwargs).user_data_dir
    return _user_data_dir


@pytest.fixture
def windows_dirs(monkeypatch, tmp_path):
    """Drive utils_paths through platformdirs' Windows class, rooted in tmp_path.

    LOCALAPPDATA is cleared so the env-var branch is out of the way and the
    platformdirs fallback is what runs; WIN_PD_OVERRIDE_LOCAL_APPDATA is
    platformdirs' own hook for standing in for the real known folder.
    """
    local_app_data = tmp_path / "AppData" / "Local"
    local_app_data.mkdir(parents=True)
    monkeypatch.setattr(utils_paths.sys, "platform", "win32")
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.setenv("WIN_PD_OVERRIDE_LOCAL_APPDATA", str(local_app_data))
    monkeypatch.setattr(utils_paths, "user_data_dir", _platform_shim(pd_windows.Windows))
    return local_app_data


@pytest.fixture
def macos_dirs(monkeypatch, tmp_path):
    """Drive utils_paths through platformdirs' macOS class, rooted in tmp_path."""
    monkeypatch.setattr(utils_paths.sys, "platform", "darwin")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.setattr(utils_paths, "user_data_dir", _platform_shim(pd_macos.MacOS))
    return tmp_path


@pytest.fixture
def linux_dirs(monkeypatch, tmp_path):
    """Drive utils_paths through platformdirs' Unix class, rooted in tmp_path."""
    monkeypatch.setattr(utils_paths.sys, "platform", "linux")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.setattr(utils_paths, "user_data_dir", _platform_shim(pd_unix.Unix))
    return tmp_path


def _segments(path):
    return [part for part in path.replace("\\", "/").split("/") if part]


def test_windows_without_localappdata_falls_back_to_platformdirs(windows_dirs):
    app_dir = utils_paths.get_app_data_dir()

    assert app_dir.replace("\\", "/") == str(windows_dirs / APP_DATA_FOLDER).replace("\\", "/")
    assert os.path.isdir(app_dir)


def test_windows_app_data_dir_has_no_doubled_app_folder(windows_dirs):
    """Regression guard: a missing appauthor=False yields
    %LOCALAPPDATA%\\PFRSentinel\\PFRSentinel and orphans every existing config."""
    app_dir = utils_paths.get_app_data_dir()

    segments = _segments(app_dir)
    assert segments[-1] == APP_DATA_FOLDER
    assert segments.count(APP_DATA_FOLDER) == 1
    assert f"{APP_DATA_FOLDER}/{APP_DATA_FOLDER}" not in app_dir.replace("\\", "/")


def test_platformdirs_doubles_app_folder_without_appauthor_false(windows_dirs):
    """The behaviour the appauthor=False argument exists to suppress."""
    doubled = pd_windows.Windows(appname=APP_DATA_FOLDER).user_data_dir

    assert _segments(doubled).count(APP_DATA_FOLDER) == 2


def test_windows_localappdata_env_var_wins_so_upgrades_keep_their_data_root(
    monkeypatch, tmp_path
):
    """A rig that relocated AppData by setting %LOCALAPPDATA% must still find its
    config, camera profiles and all-sky calibration after the platformdirs move —
    SHGetKnownFolderPath ignores that variable and would start from an empty root."""
    relocated = tmp_path / "D_drive" / "AppData" / "Local"
    relocated.mkdir(parents=True)
    monkeypatch.setattr(utils_paths.sys, "platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(relocated))
    monkeypatch.setattr(utils_paths, "user_data_dir",
                        lambda *args, **kwargs: str(tmp_path / "known_folder" / APP_DATA_FOLDER))

    app_dir = utils_paths.get_app_data_dir()

    assert app_dir == os.path.join(str(relocated), APP_DATA_FOLDER)
    assert not (tmp_path / "known_folder").exists()


def test_localappdata_does_not_leak_into_macos_path(macos_dirs, monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "should_be_ignored"))

    app_dir = utils_paths.get_app_data_dir()

    assert app_dir == str(macos_dirs / "Library" / "Application Support" / APP_DATA_FOLDER)
    assert "should_be_ignored" not in app_dir


def test_localappdata_does_not_leak_into_linux_path(linux_dirs, monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "should_be_ignored"))

    app_dir = utils_paths.get_app_data_dir()

    assert app_dir == str(linux_dirs / ".local" / "share" / APP_DATA_FOLDER)
    assert "should_be_ignored" not in app_dir


def test_macos_app_data_dir_is_application_support(macos_dirs):
    app_dir = utils_paths.get_app_data_dir()

    assert os.path.isabs(app_dir)
    assert app_dir == str(macos_dirs / "Library" / "Application Support" / APP_DATA_FOLDER)
    assert os.path.isdir(app_dir)


def test_linux_app_data_dir_is_local_share(linux_dirs):
    app_dir = utils_paths.get_app_data_dir()

    assert os.path.isabs(app_dir)
    assert app_dir == str(linux_dirs / ".local" / "share" / APP_DATA_FOLDER)
    assert os.path.isdir(app_dir)


def test_app_data_dir_is_absolute_with_no_localappdata(monkeypatch, tmp_path):
    """The old implementation joined an empty LOCALAPPDATA and produced a
    relative 'PFRSentinel' under whatever the working directory happened to be."""
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)

    app_dir = utils_paths.get_app_data_dir()

    assert os.path.isabs(app_dir)
    assert not (tmp_path / APP_DATA_FOLDER).exists()


def test_app_config_app_data_dir_is_absolute_with_no_localappdata(monkeypatch, tmp_path):
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)

    app_dir = app_config.get_app_data_dir()

    assert os.path.isabs(app_dir)
    assert not (tmp_path / APP_DATA_FOLDER).exists()


def test_app_config_delegates_to_utils_paths(linux_dirs):
    assert app_config.get_app_data_dir() == utils_paths.get_app_data_dir()


def test_calibration_paths_live_in_app_data_dir(linux_dirs):
    app_dir = utils_paths.get_app_data_dir()

    assert app_config.get_calibration_path() == os.path.join(app_dir, 'allsky_calibration.json')
    assert app_config.get_calibration_backup_path() == os.path.join(
        app_dir, 'allsky_calibration.previous.json'
    )


def test_log_dir_is_lowercase_logs(linux_dirs):
    log_dir = utils_paths.get_log_dir()

    assert os.path.basename(log_dir) == 'logs'
    assert log_dir == os.path.join(utils_paths.get_app_data_dir(), 'logs')
    assert os.path.isdir(log_dir)


def test_ml_contribution_dir_under_app_data_dir(linux_dirs):
    ml_dir = utils_paths.get_ml_contribution_dir()

    assert ml_dir == os.path.join(utils_paths.get_app_data_dir(), 'ml_contribution')
    assert os.path.isdir(ml_dir)


def test_app_data_dir_falls_back_to_temp_when_home_is_read_only(monkeypatch, tmp_path):
    """Sandboxed and read-only-home environments must not break module import."""
    monkeypatch.setattr(utils_paths, "user_data_dir",
                        lambda *args, **kwargs: str(tmp_path / "denied" / APP_DATA_FOLDER))

    real_makedirs = os.makedirs

    def _deny_under_denied(path, *args, **kwargs):
        if "denied" in str(path):
            raise PermissionError(path)
        return real_makedirs(path, *args, **kwargs)

    monkeypatch.setattr(utils_paths.os, "makedirs", _deny_under_denied)
    monkeypatch.setattr(utils_paths.tempfile, "gettempdir", lambda: str(tmp_path / "tmp"))

    app_dir = utils_paths.get_app_data_dir()

    assert app_dir == os.path.join(str(tmp_path / "tmp"), APP_DATA_FOLDER)
    assert os.path.isdir(app_dir)


def test_resource_path_and_exe_dir_return_absolute_strings():
    resource = utils_paths.resource_path('assets')
    exe_dir = utils_paths.get_exe_dir()

    assert isinstance(resource, str) and os.path.isabs(resource)
    assert isinstance(exe_dir, str) and os.path.isabs(exe_dir)
