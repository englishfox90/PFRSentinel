"""Start-at-login on macOS (LaunchAgent) and Linux (XDG autostart), plus the
POSIX self-restart waiter. Files go to tmp_path; launchctl is never called."""
import os
import plistlib
import subprocess
import sys
import time
from unittest.mock import patch

import pytest

from services import app_restart, autostart, autostart_linux, autostart_macos
from services import launch_command

APP_EXE = "/Applications/PFR Sentinel.app/Contents/MacOS/PFRSentinel"


@pytest.fixture
def frozen_app():
    with patch.object(sys, "frozen", True, create=True), \
         patch.object(sys, "executable", APP_EXE):
        yield


@pytest.fixture
def agent_file(tmp_path, monkeypatch):
    path = tmp_path / "LaunchAgents" / f"{autostart_macos.LABEL}.plist"
    monkeypatch.setattr(autostart_macos, "plist_path", lambda: str(path))
    return path


@pytest.fixture
def desktop_file(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    return tmp_path / "config" / "autostart" / autostart_linux.FILE_NAME


class TestLaunchArgv:
    def test_frozen_app_is_its_own_executable(self, frozen_app):
        assert launch_command.launch_argv(auto_start=True, start_in_tray=True) == [
            APP_EXE, "--tray", "--auto-start"]

    def test_source_run_is_interpreter_plus_main(self):
        with patch.object(sys, "frozen", False, create=True):
            argv = launch_command.launch_argv()
        assert argv[0] == sys.executable
        assert argv[1].endswith("main.py") and os.path.isabs(argv[1])
        assert len(argv) == 2

    def test_source_run_starts_in_the_project_root(self):
        with patch.object(sys, "frozen", False, create=True):
            cwd = launch_command.launch_working_directory()
        assert os.path.isfile(os.path.join(cwd, "main.py"))


class TestDispatch:
    @pytest.mark.parametrize("platform, backend", [
        ("darwin", autostart_macos),
        ("linux", autostart_linux),
    ])
    def test_posix_platforms_use_their_backend(self, platform, backend):
        with patch.object(autostart, "_IS_WINDOWS", False), \
             patch.object(autostart, "_PLATFORM", platform), \
             patch.object(backend, "enable", return_value=True) as enable, \
             patch.object(backend, "is_enabled", return_value=True), \
             patch.object(backend, "disable", return_value=True):
            assert autostart.enable(auto_start=False, start_in_tray=True) is True
            assert autostart.is_enabled() is True
            assert autostart.disable() is True
        enable.assert_called_once_with(auto_start=False, start_in_tray=True)

    def test_windows_never_reaches_a_posix_backend(self):
        with patch.object(autostart, "_IS_WINDOWS", True), \
             patch.object(autostart, "_PLATFORM", "win32"):
            assert autostart._posix_backend() is None


class TestMacLaunchAgent:
    def test_enable_writes_a_relaunch_on_crash_agent(self, agent_file, frozen_app):
        assert autostart_macos.enable(auto_start=True, start_in_tray=False) is True
        with open(agent_file, "rb") as f:
            agent = plistlib.load(f)
        assert agent["Label"] == autostart_macos.LABEL
        assert agent["ProgramArguments"] == [APP_EXE, "--auto-start"]
        assert agent["RunAtLoad"] is True
        # Crash -> relaunch; a deliberate quit (exit 0) stays quit.
        assert agent["KeepAlive"] == {"SuccessfulExit": False}
        assert agent["ThrottleInterval"] >= 10
        assert agent["LimitLoadToSessionType"] == "Aqua"
        assert "/opt/homebrew/bin" in agent["EnvironmentVariables"]["PATH"]
        assert autostart_macos.is_enabled() is True

    def test_enable_replaces_an_existing_agent(self, agent_file, frozen_app):
        autostart_macos.enable(auto_start=True, start_in_tray=True)
        autostart_macos.enable(auto_start=False, start_in_tray=False)
        with open(agent_file, "rb") as f:
            assert plistlib.load(f)["ProgramArguments"] == [APP_EXE]
        assert [p.name for p in agent_file.parent.iterdir()] == [agent_file.name]

    def test_disable_removes_the_agent(self, agent_file, frozen_app):
        autostart_macos.enable()
        assert autostart_macos.disable() is True
        assert not agent_file.exists()
        assert autostart_macos.is_enabled() is False

    def test_disable_when_absent_is_success(self, agent_file):
        assert autostart_macos.disable() is True

    def test_never_calls_launchctl(self, agent_file, frozen_app):
        with patch("subprocess.run") as run, patch("subprocess.Popen") as popen:
            autostart_macos.enable()
            autostart_macos.disable()
        run.assert_not_called()
        popen.assert_not_called()

    def test_foreign_plist_is_not_ours(self, agent_file):
        agent_file.parent.mkdir(parents=True)
        with open(agent_file, "wb") as f:
            plistlib.dump({"Label": "com.example.other"}, f)
        assert autostart_macos.is_enabled() is False

    def test_garbage_plist_reads_as_disabled(self, agent_file):
        agent_file.parent.mkdir(parents=True)
        agent_file.write_bytes(b"not a plist")
        assert autostart_macos.is_enabled() is False

    def test_unwritable_directory_reports_failure(self, monkeypatch, tmp_path):
        blocker = tmp_path / "file-not-dir"
        blocker.write_text("x")
        monkeypatch.setattr(autostart_macos, "plist_path",
                            lambda: str(blocker / "agent.plist"))
        assert autostart_macos.enable() is False


class TestLinuxDesktopEntry:
    def test_enable_writes_an_autostart_entry(self, desktop_file, frozen_app):
        assert autostart_linux.enable(auto_start=True, start_in_tray=True) is True
        text = desktop_file.read_text(encoding="utf-8")
        assert "[Desktop Entry]" in text
        assert f'Exec="{APP_EXE}" --tray --auto-start' in text
        assert autostart_linux.is_enabled() is True

    def test_disable_removes_it(self, desktop_file, frozen_app):
        autostart_linux.enable()
        assert autostart_linux.disable() is True
        assert not desktop_file.exists()

    def test_someone_elses_entry_is_not_ours(self, desktop_file):
        desktop_file.parent.mkdir(parents=True)
        desktop_file.write_text("[Desktop Entry]\nExec=other\n", encoding="utf-8")
        assert autostart_linux.is_enabled() is False

    @pytest.mark.parametrize("arg, quoted", [
        ("/usr/bin/python3", "/usr/bin/python3"),
        ("/home/me/My Apps/main.py", '"/home/me/My Apps/main.py"'),
        ('/a"b', '"/a\\"b"'),
        ("/cost$1", '"/cost\\$1"'),
        ("/100%", '"/100%%"'),
    ])
    def test_exec_quoting(self, arg, quoted):
        assert autostart_linux.exec_line([arg]) == quoted


class TestPosixRestart:
    def test_waiter_gets_pid_then_the_launch_command(self, frozen_app):
        with patch.object(app_restart.sys, "platform", "darwin"), \
             patch.object(app_restart.subprocess, "Popen") as popen:
            assert app_restart.schedule_restart("test") is True
        argv = popen.call_args.args[0]
        assert argv[:2] == ["/bin/sh", "-c"]
        assert argv[4] == str(os.getpid())
        assert argv[5:] == [APP_EXE]
        assert popen.call_args.kwargs["start_new_session"] is True

    def test_spawn_failure_returns_false(self, frozen_app):
        with patch.object(app_restart.sys, "platform", "linux"), \
             patch.object(app_restart.subprocess, "Popen", side_effect=OSError("no sh")):
            assert app_restart.schedule_restart("test") is False

    @pytest.mark.skipif(sys.platform == "win32", reason="runs the real /bin/sh waiter")
    def test_real_waiter_relaunches_only_after_the_pid_exits(self, tmp_path):
        marker = tmp_path / "relaunched"
        target = subprocess.Popen(["sleep", "1.5"])
        waiter = subprocess.Popen(["/bin/sh", "-c", app_restart._POSIX_WAITER, "w",
                                   str(target.pid), "touch", str(marker)])
        time.sleep(1.0)
        assert not marker.exists()
        target.wait()
        assert waiter.wait(timeout=15) == 0
        assert marker.exists()
