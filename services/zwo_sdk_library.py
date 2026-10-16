"""Where the ZWO ASI SDK shared library lives on this OS.

ZWO ships one library per platform — ``ASICamera2.dll`` (Windows),
``libASICamera2.dylib`` (macOS, universal arm64/x86_64) and
``libASICamera2.so`` (Linux) — and ``zwoasi`` loads whichever path it is
handed. This module is the one place that knows the name and where to look,
so callers never spell a platform filename themselves.

A config carried over from another machine is the case that matters: a rig
moved from Windows to a Mac arrives with ``zwo_sdk_path`` pointing at
``C:\\Program Files (x86)\\PFRSentinel\\_internal\\ASICamera2.dll``, a path
that cannot exist there. ``repair_configured_sdk_path`` swaps it for the
library this machine actually has.
"""
from __future__ import annotations

import os
import platform
import sys
from ctypes.util import find_library
from typing import Iterable, List, Optional

from .host_platform import IS_MACOS, IS_WINDOWS, zwo_sdk_library_name
from .utils_paths import resource_path

_SDK_STEM = "ASICamera2"


def _project_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _linux_multiarch_dirs() -> List[str]:
    machine = platform.machine().lower()
    triplet = {
        "x86_64": "x86_64-linux-gnu",
        "amd64": "x86_64-linux-gnu",
        "aarch64": "aarch64-linux-gnu",
        "arm64": "aarch64-linux-gnu",
    }.get(machine)
    return [f"/usr/lib/{triplet}"] if triplet else []


def _system_dirs() -> List[str]:
    if IS_WINDOWS:
        dirs = []
        for var in ("PROGRAMFILES(X86)", "PROGRAMFILES"):
            base = os.environ.get(var)
            if base:
                dirs.append(os.path.join(base, "PFRSentinel", "_internal"))
        return dirs
    if IS_MACOS:
        # ZWO's own installer and a manual copy both land in /usr/local/lib;
        # /opt/homebrew/lib is the Apple-silicon Homebrew prefix.
        return ["/usr/local/lib", "/opt/homebrew/lib"]
    return ["/usr/local/lib", *_linux_multiarch_dirs(), "/usr/lib"]


def candidate_sdk_paths() -> List[str]:
    """Absolute paths to try, most specific first. Existence is not checked."""
    name = zwo_sdk_library_name()
    dirs: List[str] = [os.path.dirname(resource_path(name)), _project_root()]
    if getattr(sys, "frozen", False):
        dirs.append(os.path.dirname(sys.executable))
    dirs.extend(_system_dirs())

    seen = set()
    paths = []
    for d in dirs:
        path = os.path.normpath(os.path.join(d, name))
        if path not in seen:
            seen.add(path)
            paths.append(path)
    return paths


def is_native_sdk_path(path: str) -> bool:
    """True when ``path`` names a library this OS could load (by extension)."""
    lower = (path or "").lower()
    if IS_WINDOWS:
        return lower.endswith(".dll")
    if IS_MACOS:
        return lower.endswith(".dylib")
    return lower.endswith(".so") or ".so." in os.path.basename(lower)


def _first_existing(paths: Iterable[str]) -> Optional[str]:
    for path in paths:
        if os.path.isfile(path):
            return path
    return None


def find_sdk_library() -> Optional[str]:
    """The SDK library on this machine, or None when it isn't installed.

    Falls back to the loader's own search (``ctypes.util.find_library``) last,
    which covers ldconfig on Linux and DYLD_* paths on macOS.
    """
    found = _first_existing(candidate_sdk_paths())
    if found:
        return found
    try:
        return find_library(_SDK_STEM)
    except Exception:
        return None


def resolve_sdk_path(configured: Optional[str]) -> Optional[str]:
    """The library to load: the configured one if usable here, else a search."""
    if configured and is_native_sdk_path(configured) and os.path.isfile(configured):
        return configured
    return find_sdk_library()


def repair_configured_sdk_path(data: dict) -> Optional[str]:
    """Point ``zwo_sdk_path`` at a library that exists here, if it doesn't.

    Mutates ``data`` and returns a log line describing the change, or None
    when nothing changed. A configured path that exists is never touched,
    and a missing one is kept when no replacement is found, so the Capture
    tab still shows the user what they had.
    """
    current = data.get("zwo_sdk_path") or ""
    if current and is_native_sdk_path(current) and os.path.isfile(current):
        return None
    found = find_sdk_library()
    if not found or not os.path.isfile(found) or found == current:
        return None
    data["zwo_sdk_path"] = found
    if current:
        return f"ZWO SDK path '{current}' is not usable on this machine; using {found}"
    return f"ZWO SDK path was empty; using {found}"
