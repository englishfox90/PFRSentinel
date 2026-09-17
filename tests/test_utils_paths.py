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
patches platformdirs' get_win_folder to point at tmp_path.

EVERY test here must pin sys.platform, and every test that reaches the
platformdirs branch must also clear LOCALAPPDATA and shim user_data_dir.
A test that does neither silently changes meaning between this Linux
container and the Windows CI runner: the runner takes the env-var branch,
never calls the shim, and resolves the developer's real
%LOCALAPPDATA%\PFRSentinel — creating files there and failing tmp_path
assertions. That is a CI blocker, not a flake. The fixtures below do this;
a test that does not use one has to do it itself.
"""
import errno
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
    platformdirs fallback is what runs. get_win_folder stands in for the real
    known-folder lookup, which is unavailable off Windows; patching the
    resolver rather than using platformdirs' WIN_PD_OVERRIDE_* hook keeps this
    working on every platformdirs release the app supports, not only 4.8+.
    """
    local_app_data = tmp_path / "AppData" / "Local"
    local_app_data.mkdir(parents=True)
    monkeypatch.setattr(utils_paths.sys, "platform", "win32")
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.setattr(pd_windows, "get_win_folder", lambda csidl_name: str(local_app_data))
    monkeypatch.setattr(utils_paths, "user_data_dir", _platform_shim(pd_windows.Windows))
    return local_app_data


def _set_home(monkeypatch, tmp_path):
    """Point both POSIX and Windows home-lookup at tmp_path.

    platformdirs' macOS/Unix classes resolve via os.path.expanduser("~"),
    which is ntpath.expanduser on a Windows runner regardless of the
    sys.platform monkeypatch (os.path binds to the real OS at interpreter
    start). ntpath.expanduser reads USERPROFILE and never looks at HOME, so
    setting HOME alone leaves it resolving the runner's real home directory.
    posixpath.expanduser never looks at USERPROFILE, so setting both is safe
    on every runner.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))


@pytest.fixture
def macos_dirs(monkeypatch, tmp_path):
    """Drive utils_paths through platformdirs' macOS class, rooted in tmp_path."""
    monkeypatch.setattr(utils_paths.sys, "platform", "darwin")
    _set_home(monkeypatch, tmp_path)
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.setattr(utils_paths, "user_data_dir", _platform_shim(pd_macos.MacOS))
    return tmp_path


@pytest.fixture
def linux_dirs(monkeypatch, tmp_path):
    """Drive utils_paths through platformdirs' Unix class, rooted in tmp_path."""
    monkeypatch.setattr(utils_paths.sys, "platform", "linux")
    _set_home(monkeypatch, tmp_path)
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

    expected = macos_dirs / "Library" / "Application Support" / APP_DATA_FOLDER
    assert app_dir.replace("\\", "/") == str(expected).replace("\\", "/")
    assert "should_be_ignored" not in app_dir


def test_localappdata_does_not_leak_into_linux_path(linux_dirs, monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "should_be_ignored"))

    app_dir = utils_paths.get_app_data_dir()

    expected = linux_dirs / ".local" / "share" / APP_DATA_FOLDER
    assert app_dir.replace("\\", "/") == str(expected).replace("\\", "/")
    assert "should_be_ignored" not in app_dir


def test_macos_app_data_dir_is_application_support(macos_dirs):
    app_dir = utils_paths.get_app_data_dir()

    expected = macos_dirs / "Library" / "Application Support" / APP_DATA_FOLDER
    assert os.path.isabs(app_dir)
    assert app_dir.replace("\\", "/") == str(expected).replace("\\", "/")
    assert os.path.isdir(app_dir)


def test_linux_app_data_dir_is_local_share(linux_dirs):
    app_dir = utils_paths.get_app_data_dir()

    expected = linux_dirs / ".local" / "share" / APP_DATA_FOLDER
    assert os.path.isabs(app_dir)
    assert app_dir.replace("\\", "/") == str(expected).replace("\\", "/")
    assert os.path.isdir(app_dir)


def test_windows_empty_localappdata_falls_through_to_platformdirs(
    windows_dirs, monkeypatch, tmp_path
):
    """An empty LOCALAPPDATA must not be joined. Testing truthiness rather than
    `is not None` is what stops os.path.join('', 'PFRSentinel') returning a
    relative path that lands wherever the process happens to be running."""
    monkeypatch.setenv("LOCALAPPDATA", "")
    monkeypatch.chdir(tmp_path)

    app_dir = utils_paths.get_app_data_dir()

    assert os.path.isabs(app_dir)
    assert app_dir.replace("\\", "/") == str(windows_dirs / APP_DATA_FOLDER).replace("\\", "/")
    assert not (tmp_path / APP_DATA_FOLDER).exists()


def test_app_config_does_not_create_a_relative_data_dir_in_the_working_directory(
    linux_dirs, monkeypatch, tmp_path
):
    """app_config used to join os.getenv('LOCALAPPDATA', '') with no platform
    branch, so off Windows it returned a bare 'PFRSentinel' and os.makedirs
    created it under the process working directory."""
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)

    app_dir = app_config.get_app_data_dir()

    expected = linux_dirs / ".local" / "share" / APP_DATA_FOLDER
    assert os.path.isabs(app_dir)
    assert app_dir.replace("\\", "/") == str(expected).replace("\\", "/")
    assert not (cwd / APP_DATA_FOLDER).exists()


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


def _deny_makedirs_under(monkeypatch, tmp_path, error):
    """Make os.makedirs raise for the 'denied' root, and redirect the tempdir."""
    real_makedirs = os.makedirs

    def _makedirs(path, *args, **kwargs):
        if "denied" in str(path):
            raise error
        return real_makedirs(path, *args, **kwargs)

    monkeypatch.setattr(utils_paths.os, "makedirs", _makedirs)
    monkeypatch.setattr(utils_paths.tempfile, "gettempdir", lambda: str(tmp_path / "tmp"))


@pytest.mark.parametrize("platform_name", ["win32", "darwin", "linux"])
def test_app_data_dir_falls_back_to_temp_when_root_is_unwritable(
    monkeypatch, tmp_path, platform_name
):
    """Sandboxed and read-only-home environments must not break module import."""
    monkeypatch.setattr(utils_paths.sys, "platform", platform_name)
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.setattr(utils_paths, "user_data_dir",
                        lambda *args, **kwargs: str(tmp_path / "denied" / APP_DATA_FOLDER))
    _deny_makedirs_under(monkeypatch, tmp_path, PermissionError("denied"))

    app_dir = utils_paths.get_app_data_dir()

    assert app_dir == os.path.join(str(tmp_path / "tmp"), APP_DATA_FOLDER)
    assert os.path.isdir(app_dir)


def test_app_data_dir_falls_back_to_temp_on_read_only_filesystem(monkeypatch, tmp_path):
    """EROFS and ENOSPC are not PermissionError. This function runs at import
    time via config_defaults, so any unwritable root has to degrade, not raise.
    Exercised through the Windows env-var branch, which shares the same guard."""
    denied = tmp_path / "denied" / "Local"
    monkeypatch.setattr(utils_paths.sys, "platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(denied))
    _deny_makedirs_under(monkeypatch, tmp_path, OSError(errno.EROFS, "Read-only file system"))

    app_dir = utils_paths.get_app_data_dir()

    assert app_dir == os.path.join(str(tmp_path / "tmp"), APP_DATA_FOLDER)
    assert os.path.isdir(app_dir)


def test_resource_path_and_exe_dir_return_absolute_strings():
    resource = utils_paths.resource_path('assets')
    exe_dir = utils_paths.get_exe_dir()

    assert isinstance(resource, str) and os.path.isabs(resource)
    assert isinstance(exe_dir, str) and os.path.isabs(exe_dir)
