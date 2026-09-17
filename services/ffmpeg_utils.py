"""
Shared ffmpeg and winget/package-manager availability checks.
Used by the timelapse feature.
"""
import glob
import os
import shutil
import subprocess
import sys

from services.host_platform import IS_WINDOWS, IS_MACOS, IS_LINUX

# Hide console windows for subprocess calls on Windows
_POPEN_KWARGS = {'creationflags': subprocess.CREATE_NO_WINDOW} if sys.platform == 'win32' else {}

# macOS: GUI apps launched from Finder/Dock get a minimal PATH that omits
# Homebrew, so probe the common install locations directly.
MACOS_FFMPEG_CANDIDATES = (
    '/opt/homebrew/bin/ffmpeg',  # Apple silicon Homebrew
    '/usr/local/bin/ffmpeg',     # Intel Homebrew
    '/opt/local/bin/ffmpeg',     # MacPorts
)

LINUX_FFMPEG_CANDIDATES = (
    '/usr/bin/ffmpeg',
    '/usr/local/bin/ffmpeg',
    '/snap/bin/ffmpeg',
    os.path.expanduser('~/.local/bin/ffmpeg'),
)


def get_ffmpeg_path() -> str:
    """
    Return the full path to the ffmpeg executable.

    Search order:
    1. System/user PATH (shutil.which)
    2. Platform-specific candidate locations:
       - Windows: winget packages folder — Gyan.FFmpeg installs as a zip
         extract to %LOCALAPPDATA%\\Microsoft\\WinGet\\Packages\\ and may
         not add itself to PATH on all winget versions.
       - macOS: common Homebrew / MacPorts install paths.
       - Linux: common distro package manager / snap / user install paths.

    Falls back to the bare string 'ffmpeg' so callers can still attempt
    to run it and get a natural FileNotFoundError if truly absent.
    """
    # 1. PATH check
    path = shutil.which('ffmpeg')
    if path:
        return path

    # 2. Platform-specific candidate locations
    if IS_WINDOWS:
        winget_base = os.path.join(
            os.getenv('LOCALAPPDATA', ''),
            'Microsoft', 'WinGet', 'Packages'
        )
        for candidate in glob.glob(
            os.path.join(winget_base, 'Gyan.FFmpeg*', '**', 'ffmpeg.exe'),
            recursive=True,
        ):
            if os.path.isfile(candidate):
                return candidate
    elif IS_MACOS:
        for candidate in MACOS_FFMPEG_CANDIDATES:
            if os.path.isfile(candidate):
                return candidate
    else:
        for candidate in LINUX_FFMPEG_CANDIDATES:
            if os.path.isfile(candidate):
                return candidate

    return 'ffmpeg'


def is_ffmpeg_available() -> bool:
    """Check if ffmpeg is installed and runnable (PATH or a known install location)."""
    path = get_ffmpeg_path()
    try:
        result = subprocess.run([path, '-version'], capture_output=True, timeout=5, **_POPEN_KWARGS)
        return result.returncode == 0
    except Exception:
        return False


def is_winget_available() -> bool:
    """Check if winget (Windows Package Manager) is available. Windows only."""
    if not IS_WINDOWS:
        return False
    try:
        result = subprocess.run(['winget', '--version'], capture_output=True, timeout=5, **_POPEN_KWARGS)
        return result.returncode == 0
    except Exception:
        return False


def ffmpeg_install_command() -> str | None:
    """Shell command a user could run to install ffmpeg, or None on Windows.

    Windows is served by the winget button; when winget itself is missing there
    is no one-line install to offer, so the card shows only the manual hint.
    """
    if IS_WINDOWS:
        return None
    if IS_MACOS:
        return 'brew install ffmpeg'
    return 'sudo apt install ffmpeg'


def ffmpeg_install_hint() -> str:
    """One-sentence, platform-appropriate instruction shown above the install command.

    On Windows this is only meaningful when winget is unavailable — the
    winget-available path uses the primary "Install via winget" button instead.
    """
    if IS_MACOS:
        return "Install it with Homebrew, then restart PFR Sentinel:"
    if IS_LINUX:
        return "Install it from your distribution's package manager, then restart PFR Sentinel:"
    return (
        "winget (Windows Package Manager) is not available on this system. "
        "Install ffmpeg manually and add it to PATH."
    )
