"""Start at login on Linux: an XDG autostart ``.desktop`` entry.

Sentinel is a GUI app, so it belongs to the desktop session rather than a
systemd user unit, which has no display to draw on. Every mainstream desktop
(GNOME, KDE, XFCE, LXDE on a Pi) honours ``~/.config/autostart``. There is no
crash relaunch here; that gap is noted in #40.
"""
from __future__ import annotations

import os
import tempfile
from typing import Iterable

from services.launch_command import launch_argv, launch_working_directory
from services.logger import app_logger

FILE_NAME = "pfrsentinel.desktop"
_MARKER = "X-PFRSentinel-Autostart=true"


def desktop_path() -> str:
    config_home = os.environ.get("XDG_CONFIG_HOME") or os.path.join(
        os.path.expanduser("~"), ".config")
    return os.path.join(config_home, "autostart", FILE_NAME)


def _quote_exec_arg(arg: str) -> str:
    """Quote one argument per the Desktop Entry spec's Exec rules."""
    reserved = set(" \t\n\"'\\><~|&;$*?#()`=%")
    if arg and not any(c in reserved for c in arg):
        return arg
    escaped = "".join("\\" + c if c in '"`$\\' else c for c in arg)
    # A literal % must be doubled even inside quotes (field codes).
    return '"' + escaped.replace("%", "%%") + '"'


def exec_line(argv: Iterable[str]) -> str:
    return " ".join(_quote_exec_arg(a) for a in argv)


def build_entry(auto_start: bool, start_in_tray: bool) -> str:
    argv = launch_argv(auto_start=auto_start, start_in_tray=start_in_tray)
    return "\n".join([
        "[Desktop Entry]",
        "Type=Application",
        "Name=PFR Sentinel",
        "Comment=Observatory camera monitoring",
        f"Exec={exec_line(argv)}",
        f"Path={launch_working_directory()}",
        "Terminal=false",
        "X-GNOME-Autostart-enabled=true",
        _MARKER,
        "",
    ])


def is_enabled() -> bool:
    try:
        with open(desktop_path(), encoding="utf-8") as f:
            return _MARKER in f.read()
    except FileNotFoundError:
        return False
    except Exception as e:
        app_logger.warning(f"autostart: unreadable entry {desktop_path()}: {e}")
        return False


def enable(auto_start: bool = True, start_in_tray: bool = True) -> bool:
    path = desktop_path()
    entry = build_entry(auto_start, start_in_tray)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".desktop.tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(entry)
            os.replace(tmp, path)
        except BaseException:
            if os.path.isfile(tmp):
                os.remove(tmp)
            raise
    except Exception as e:
        app_logger.error(f"autostart: could not write {path}: {e}")
        return False
    app_logger.info(f"autostart: desktop entry written ({path})")
    return True


def disable() -> bool:
    path = desktop_path()
    try:
        if os.path.isfile(path):
            os.remove(path)
            app_logger.info(f"autostart: desktop entry removed ({path})")
    except Exception as e:
        app_logger.error(f"autostart: could not remove {path}: {e}")
    return not os.path.isfile(path)
