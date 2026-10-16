"""The argv that starts this copy of PFR Sentinel, frozen or from source.

Used wherever the app has to start itself later: the macOS LaunchAgent, the
Linux autostart entry and the self-restart waiter. A frozen build is its own
executable (inside a ``.app`` that is ``Contents/MacOS/PFRSentinel``); a source
run is the current interpreter plus ``main.py``.
"""
from __future__ import annotations

import os
import sys
from typing import List

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def launch_argv(auto_start: bool = False, start_in_tray: bool = False) -> List[str]:
    """Absolute argv for a new instance, with the startup flags requested."""
    if is_frozen():
        argv = [sys.executable]
    else:
        argv = [sys.executable, os.path.join(_PROJECT_ROOT, "main.py")]
    if start_in_tray:
        argv.append("--tray")
    if auto_start:
        argv.append("--auto-start")
    return argv


def launch_working_directory() -> str:
    """Directory a launcher should start the app in.

    A source run resolves bundled resources from the project root; a frozen
    build doesn't care, so it starts in the user's home like any login item.
    """
    if is_frozen():
        return os.path.expanduser("~")
    return _PROJECT_ROOT
