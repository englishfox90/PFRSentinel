"""Locate the ZWO ASI SDK shared library on this OS.

``zwoasi`` is a ctypes wrapper that loads whatever library path it is handed,
so the SDK is portable as long as nobody spells ``ASICamera2.dll`` themselves.
This module is the one place that knows the per-platform filename and where a
copy is likely to be; everything else asks it.

Always hand ``zwoasi.init()`` an absolute path from here. There is no
``os.add_dll_directory()`` equivalent on macOS or Linux, and a bare name only
resolves through the loader's own search path — which never includes the
folder the app runs from.

Kept outside ``services/camera/`` on purpose: ``config_defaults`` needs it at
import time, and importing that package pulls in OpenCV and the capture stack.
"""
from __future__ import annotations

import glob
import os
import sys
import sysconfig
from typing import List, Optional

from .host_platform import IS_MACOS, IS_WINDOWS, zwo_sdk_library_name
from .utils_paths import get_app_data_dir, resource_path

# Folder under the app-data root where a user can drop the library. It survives
# upgrades and needs no admin rights, which matters off Windows where the
# library is not bundled.
USER_SDK_SUBFOLDER = "sdk"

# Every real build is megabytes; anything under this is a link stub.
MIN_LIBRARY_BYTES = 4096

ALL_LIBRARY_NAMES = ("ASICamera2.dll", "libASICamera2.dylib", "libASICamera2.so")

MACOS_LIBRARY_DIRS = (
    "/usr/local/lib",      # ZWO's own instructions, Intel Homebrew
    "/opt/homebrew/lib",   # Apple silicon Homebrew
    "/opt/local/lib",      # MacPorts
)

LINUX_LIBRARY_DIRS = (
    "/usr/local/lib",
    "/usr/lib64",
    "/usr/lib",
)


def library_name() -> str:
    """Filename of the SDK library on this OS."""
    return zwo_sdk_library_name()


def bundled_library_path() -> str:
    """Where the library sits when it ships with the app (may not exist)."""
    return resource_path(library_name())


def user_library_dir() -> str:
    """Per-user folder that is searched for a manually installed library."""
    return os.path.join(get_app_data_dir(), USER_SDK_SUBFOLDER)


def _linux_multiarch_dirs() -> List[str]:
    # Debian/Ubuntu/Raspberry Pi OS put distro libraries — including INDI's
    # libasi package — under /usr/lib/<triplet>, not /usr/lib.
    triplet = sysconfig.get_config_var("MULTIARCH")
    if not triplet:
        return []
    return [os.path.join("/usr/local/lib", triplet), os.path.join("/usr/lib", triplet)]


def _system_library_dirs() -> List[str]:
    if IS_WINDOWS:
        dirs = []
        for var in ("PROGRAMFILES(X86)", "PROGRAMFILES"):
            root = os.getenv(var)
            if root:
                dirs.append(os.path.join(root, "PFRSentinel", "_internal"))
        return dirs
    if IS_MACOS:
        return list(MACOS_LIBRARY_DIRS)
    return _linux_multiarch_dirs() + list(LINUX_LIBRARY_DIRS)


def search_dirs() -> List[str]:
    """Folders searched for the library, most specific first."""
    dirs = [os.path.dirname(bundled_library_path())]
    if getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(sys.executable)
        dirs += [exe_dir, os.path.join(exe_dir, "_internal")]
    dirs.append(user_library_dir())
    dirs += _system_library_dirs()

    unique = []
    for folder in dirs:
        if folder not in unique:
            unique.append(folder)
    return unique


def _is_real_library(path: str) -> bool:
    # ZWO ships the bare name as a symlink to the versioned file. Copied off a
    # git host or out of a zip on Windows it arrives as a ~20-byte text file
    # holding the link target, which exists but cannot be loaded.
    try:
        return os.path.isfile(path) and os.path.getsize(path) >= MIN_LIBRARY_BYTES
    except OSError:
        return False


def _library_in(folder: str) -> Optional[str]:
    exact = os.path.join(folder, library_name())
    if _is_real_library(exact):
        return exact
    if IS_WINDOWS:
        return None
    # ZWO's SDK and distro packages both carry a versioned file
    # (libASICamera2.so.1.37, libASICamera2.dylib.1.37); the bare name may be
    # missing, left to a -dev package, or a broken link.
    for candidate in sorted(glob.glob(exact + ".*"), reverse=True):
        if _is_real_library(candidate):
            return candidate
    return None


def find_library() -> Optional[str]:
    """Absolute path of the first SDK library found, or None."""
    for folder in search_dirs():
        found = _library_in(folder)
        if found:
            return os.path.abspath(found)
    return None


def resolve_library_path(configured: Optional[str] = None) -> Optional[str]:
    """The library to load: the configured path if it exists, else a search.

    A configured path that no longer exists is normal, not an error — a config
    written on Windows and carried to another OS, a reinstall to a different
    folder, or the pre-port default that named the DLL on every platform.
    """
    if configured and os.path.isfile(configured):
        return os.path.abspath(configured)
    return find_library()


def names_another_platforms_library(path: str) -> bool:
    """True when ``path`` is an SDK library path, but for a different OS."""
    # Split on both separators: a Windows path read on Linux has no '/' in it.
    filename = path.replace("\\", "/").rsplit("/", 1)[-1]
    if filename.startswith(library_name()):
        return False
    return any(filename.startswith(other) for other in ALL_LIBRARY_NAMES)


def default_library_path() -> str:
    """Config default: a library that exists if there is one, else the bundled spot."""
    return find_library() or bundled_library_path()


def missing_library_help() -> List[str]:
    """Log lines telling the operator how to get the library onto this machine."""
    name = library_name()
    lines = [f"ERROR: ZWO ASI SDK library ({name}) not found. Searched:"]
    lines += [f"  - {folder}" for folder in search_dirs()]
    if IS_WINDOWS:
        lines.append(f"{name} ships with PFR Sentinel — reinstall, or set SDK Path in the Capture tab.")
    else:
        lines.append(
            f"Download the ASI Camera SDK (Linux & Mac) from the ZWO developer page, "
            f"copy {name} for this machine's CPU into {user_library_dir()}, "
            f"or set SDK Path in the Capture tab."
        )
        if not IS_MACOS:
            lines.append(
                "On Linux also install the udev rule (installer/linux/asi.rules) — "
                "without it the camera only opens as root."
            )
    return lines
