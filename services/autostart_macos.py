"""Start at login on macOS: a per-user launchd LaunchAgent.

The agent lives in ``~/Library/LaunchAgents`` and needs no admin rights.
Beyond starting the app at login it gives the 24/7 rig something Windows'
scheduled task doesn't: ``KeepAlive`` with ``SuccessfulExit = false`` makes
launchd relaunch Sentinel after a crash, while a deliberate quit (exit 0) is
left alone.

``enable`` and ``disable`` only write and delete the plist. They deliberately
don't call ``launchctl``: loading the agent now would start a second copy
alongside this one, and unloading a running agent kills its process, which
is this app when it was started at login. launchd reads the file at the next
login, so a change takes effect then.
"""
from __future__ import annotations

import os
import plistlib
import tempfile

from services.launch_command import launch_argv, launch_working_directory
from services.logger import app_logger

LABEL = "com.pfrsentinel.app"

# launchd starts agents with PATH=/usr/bin:/bin:/usr/sbin:/sbin, which misses
# Homebrew (ffmpeg, libusb tools) on both Apple-silicon and Intel Macs.
_AGENT_PATH = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

# Seconds launchd waits between crash relaunches, so a crash at startup
# can't spin the CPU.
THROTTLE_INTERVAL_S = 30


def plist_path() -> str:
    return os.path.join(os.path.expanduser("~"), "Library", "LaunchAgents", f"{LABEL}.plist")


def build_agent(auto_start: bool, start_in_tray: bool) -> dict:
    return {
        "Label": LABEL,
        "ProgramArguments": launch_argv(auto_start=auto_start, start_in_tray=start_in_tray),
        "WorkingDirectory": launch_working_directory(),
        "EnvironmentVariables": {"PATH": _AGENT_PATH},
        "RunAtLoad": True,
        "KeepAlive": {"SuccessfulExit": False},
        "ThrottleInterval": THROTTLE_INTERVAL_S,
        "ProcessType": "Interactive",
        # A GUI app: only in a logged-in desktop session, never at a
        # background (SSH / pre-login) one.
        "LimitLoadToSessionType": "Aqua",
    }


def is_enabled() -> bool:
    try:
        with open(plist_path(), "rb") as f:
            return plistlib.load(f).get("Label") == LABEL
    except FileNotFoundError:
        return False
    except Exception as e:
        app_logger.warning(f"autostart: unreadable LaunchAgent {plist_path()}: {e}")
        return False


def enable(auto_start: bool = True, start_in_tray: bool = True) -> bool:
    path = plist_path()
    agent = build_agent(auto_start, start_in_tray)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".plist.tmp")
        try:
            with os.fdopen(fd, "wb") as f:
                plistlib.dump(agent, f)
            os.replace(tmp, path)
        except BaseException:
            if os.path.isfile(tmp):
                os.remove(tmp)
            raise
    except Exception as e:
        app_logger.error(f"autostart: could not write LaunchAgent {path}: {e}")
        return False
    app_logger.info(
        f"autostart: LaunchAgent written ({' '.join(agent['ProgramArguments'])}); "
        "takes effect at next login")
    return True


def disable() -> bool:
    path = plist_path()
    try:
        if os.path.isfile(path):
            os.remove(path)
            app_logger.info(f"autostart: LaunchAgent removed ({path})")
    except Exception as e:
        app_logger.error(f"autostart: could not remove LaunchAgent {path}: {e}")
    return not os.path.isfile(path)
