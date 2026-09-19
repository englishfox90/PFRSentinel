"""Facts about the host operating system that shape user-facing wording.

One place for "which OS is this" so panels never spell platform names, file
manager names or library extensions themselves. Behavioural platform switches
(subprocess flags, tray backends, path roots) stay where they are used — this
module is for *presentation*: the label in a switch, a placeholder, a dialog
filter.
"""
from __future__ import annotations

import sys

IS_WINDOWS = sys.platform == "win32"
IS_MACOS = sys.platform == "darwin"
IS_LINUX = not IS_WINDOWS and not IS_MACOS


def platform_label() -> str:
    """Human name of the host OS for UI copy: 'Windows', 'macOS' or 'Linux'."""
    if IS_WINDOWS:
        return "Windows"
    if IS_MACOS:
        return "macOS"
    return "Linux"


def file_manager_name() -> str:
    """What the user calls the thing that shows a folder: Explorer, Finder…"""
    if IS_WINDOWS:
        return "File Explorer"
    if IS_MACOS:
        return "Finder"
    return "file manager"


def zwo_sdk_library_name() -> str:
    """Filename of the ZWO ASI SDK shared library on this OS."""
    if IS_WINDOWS:
        return "ASICamera2.dll"
    if IS_MACOS:
        return "libASICamera2.dylib"
    return "libASICamera2.so"


def zwo_sdk_dialog_filter() -> str:
    """QFileDialog name filter for picking the ZWO SDK library."""
    if IS_WINDOWS:
        return "DLL Files (*.dll)"
    if IS_MACOS:
        return "Shared Libraries (*.dylib)"
    return "Shared Libraries (*.so *.so.*)"
