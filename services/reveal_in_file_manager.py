"""Reveal or open a path in the OS file manager.

One implementation instead of six, because the "show this in Explorer/Finder"
launch differs by platform (Windows, macOS, Linux) and every ad hoc copy of it
tended to only get tested on Windows.
"""
from __future__ import annotations

import os
import subprocess
import sys

from services.logger import app_logger


def _popen(args) -> bool:
    try:
        kwargs = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
        if sys.platform != "win32":
            kwargs["start_new_session"] = True
        subprocess.Popen(args, **kwargs)
        return True
    except Exception as e:
        app_logger.debug(f"reveal_in_file_manager: failed to launch {args}: {e}")
        return False


def _startfile(path) -> bool:
    try:
        os.startfile(path)
        return True
    except Exception as e:
        app_logger.debug(f"reveal_in_file_manager: os.startfile failed for {path}: {e}")
        return False


def reveal_path(path, select: bool = True) -> bool:
    """Show `path` in the OS file manager, selecting it when it is a file."""
    if not path or not os.path.exists(path):
        app_logger.debug(f"reveal_in_file_manager: path does not exist: {path}")
        return False

    # QFileDialog hands back forward slashes on Windows, which `explorer
    # /select,` silently misreads as a different folder.
    path = os.path.normpath(path)
    is_file = os.path.isfile(path)
    target_dir = os.path.dirname(path) if is_file else path

    if is_file and select:
        if sys.platform == "win32":
            return _popen(["explorer", f"/select,{path}"])
        if sys.platform == "darwin":
            return _popen(["open", "-R", path])
        # No portable "select" verb on Linux — open the containing folder.
        return _popen(["xdg-open", target_dir])

    if sys.platform == "win32":
        return _startfile(target_dir)
    if sys.platform == "darwin":
        return _popen(["open", target_dir])
    return _popen(["xdg-open", target_dir])


def open_path(path) -> bool:
    """Open `path` with its default application."""
    if not path or not os.path.exists(path):
        app_logger.debug(f"reveal_in_file_manager: path does not exist: {path}")
        return False

    path = os.path.normpath(path)
    if sys.platform == "win32":
        return _startfile(path)
    if sys.platform == "darwin":
        return _popen(["open", path])
    return _popen(["xdg-open", path])
