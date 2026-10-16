"""Relaunch the PFR Sentinel process — last-resort recovery from a ZWO SDK
wedge that nothing in-process can clear.

When the SDK DLL corrupts (concurrent access, or a USB stack hang) the capture
thread blocks forever inside an uninterruptible C call, and on a rig that isn't
running as Administrator the USB device reset is unavailable too. A fresh
process loads a clean copy of ASICamera2.dll, so for a 24/7 unattended rig a
process restart is the only reliable cure.

The single-instance guard (services/single_instance.py) holds a QLocalServer
lock, so the replacement must not start until THIS process has exited and
released it. We launch a small detached waiter (PowerShell on Windows, sh on
macOS and Linux) that blocks on our PID, settles briefly, then starts a new
instance — and then the caller quits the app cleanly.
"""
import os
import subprocess
import sys

from services.launch_command import launch_argv
from services.logger import app_logger

# Give up waiting after this long and relaunch anyway; the single-instance
# lock then arbitrates if the old process somehow never exited.
_WAIT_CAP_S = 180

# $1 is our PID; the rest of argv is the command to exec. Passing the command
# as arguments, not text, means no path needs quoting.
_POSIX_WAITER = (
    'pid="$1"; shift; i=0; '
    'while kill -0 "$pid" 2>/dev/null && [ "$i" -lt ' + str(_WAIT_CAP_S * 2) + ' ]; '
    'do sleep 0.5; i=$((i+1)); done; '
    'sleep 2; exec "$@"'
)

# Win32 process-creation flags so the waiter outlives us and shows no console.
_DETACHED_PROCESS = 0x00000008
_CREATE_NEW_PROCESS_GROUP = 0x00000200
_CREATE_NO_WINDOW = 0x08000000


def schedule_restart(reason: str = "") -> bool:
    """Spawn a detached waiter that relaunches the app after this process exits.

    Returns True if the waiter was launched (the caller should then quit the
    app cleanly), False if a restart could not be scheduled (the caller should
    fall back to an alert-and-wait).
    """
    if sys.platform != 'win32':
        return _schedule_restart_posix(reason)
    try:
        pid = os.getpid()
        if getattr(sys, 'frozen', False):
            # PyInstaller build: sys.executable IS the app exe.
            target = f'"{sys.executable}"'
        else:
            # Dev run: re-exec the same interpreter + entry script.
            target = f'"{sys.executable}" "{os.path.abspath(sys.argv[0])}"'

        # Wait for THIS pid to exit (releasing the single-instance lock and the
        # USB handle), settle, then relaunch. Start-Process detaches the new app
        # from the waiter so the waiter can exit immediately afterward.
        #
        # POLL until the PID is actually gone rather than Wait-Process with a
        # fixed timeout: a fixed timeout could relaunch while we still hold the
        # single-instance lock, and the second instance would then exit on the
        # lock and leave the rig dead. Best-effort relaunch after a generous cap
        # if the process somehow never exits (single-instance arbitrates then).
        ps_command = (
            "$ErrorActionPreference='SilentlyContinue';"
            "$deadline=(Get-Date).AddSeconds(" + str(_WAIT_CAP_S) + ");"
            "while ((Get-Process -Id " + str(pid) +
            " -ErrorAction SilentlyContinue) -and ((Get-Date) -lt $deadline)) "
            "{ Start-Sleep -Milliseconds 500 };"
            "Start-Sleep -Seconds 2;"
            "Start-Process " + target
        )
        subprocess.Popen(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_command],
            creationflags=(
                _DETACHED_PROCESS | _CREATE_NEW_PROCESS_GROUP | _CREATE_NO_WINDOW
            ),
            close_fds=True,
        )
        app_logger.warning(
            f"Application restart scheduled (waiter watching pid {pid}) — {reason}"
        )
        return True
    except Exception as e:
        app_logger.error(f"Failed to schedule application restart: {e}")
        return False


def _schedule_restart_posix(reason: str) -> bool:
    try:
        pid = os.getpid()
        subprocess.Popen(
            ["/bin/sh", "-c", _POSIX_WAITER, "pfrsentinel-restart", str(pid), *launch_argv()],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            # Its own session, so quitting the app (or its terminal) doesn't
            # take the waiter with it.
            start_new_session=True,
            close_fds=True,
        )
        app_logger.warning(
            f"Application restart scheduled (waiter watching pid {pid}) — {reason}"
        )
        return True
    except Exception as e:
        app_logger.error(f"Failed to schedule application restart: {e}")
        return False
