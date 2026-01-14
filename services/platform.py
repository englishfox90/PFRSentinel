"""
Cross-Platform Utilities for PFRSentinel

Provides OS detection and platform-specific path resolution for:
- Windows
- macOS (Darwin)  
- Linux

This module centralizes all platform-specific logic to enable
cross-platform support while maintaining a clean codebase.
"""
import os
import sys
import platform as py_platform
from pathlib import Path
from typing import Optional, Tuple
from enum import Enum


class Platform(Enum):
    """Supported operating systems"""
    WINDOWS = "windows"
    MACOS = "macos"
    LINUX = "linux"
    UNKNOWN = "unknown"


# Detect current platform at module load
_system = py_platform.system().lower()
if _system == "windows":
    CURRENT_PLATFORM = Platform.WINDOWS
elif _system == "darwin":
    CURRENT_PLATFORM = Platform.MACOS
elif _system == "linux":
    CURRENT_PLATFORM = Platform.LINUX
else:
    CURRENT_PLATFORM = Platform.UNKNOWN


def is_windows() -> bool:
    """Check if running on Windows"""
    return CURRENT_PLATFORM == Platform.WINDOWS


def is_macos() -> bool:
    """Check if running on macOS"""
    return CURRENT_PLATFORM == Platform.MACOS


def is_linux() -> bool:
    """Check if running on Linux"""
    return CURRENT_PLATFORM == Platform.LINUX


def get_platform_name() -> str:
    """Get human-readable platform name"""
    names = {
        Platform.WINDOWS: "Windows",
        Platform.MACOS: "macOS",
        Platform.LINUX: "Linux",
        Platform.UNKNOWN: "Unknown",
    }
    return names.get(CURRENT_PLATFORM, "Unknown")


# =============================================================================
# User Data Directories
# =============================================================================

def get_user_data_dir(app_name: str) -> Path:
    """
    Get the appropriate user data directory for the current platform.
    
    Windows: %LOCALAPPDATA%/app_name
    macOS: ~/Library/Application Support/app_name
    Linux: ~/.local/share/app_name (XDG standard)
    
    Args:
        app_name: Application name (used as folder name)
        
    Returns:
        Path to user data directory (created if doesn't exist)
    """
    if is_windows():
        base = os.environ.get('LOCALAPPDATA')
        if not base:
            # Fallback to APPDATA if LOCALAPPDATA not available
            base = os.environ.get('APPDATA', str(Path.home()))
        data_dir = Path(base) / app_name
    elif is_macos():
        data_dir = Path.home() / "Library" / "Application Support" / app_name
    else:  # Linux and others
        # Follow XDG Base Directory Specification
        xdg_data = os.environ.get('XDG_DATA_HOME')
        if xdg_data:
            data_dir = Path(xdg_data) / app_name
        else:
            data_dir = Path.home() / ".local" / "share" / app_name
    
    # Create directory if it doesn't exist
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


def get_user_config_dir(app_name: str) -> Path:
    """
    Get the appropriate user config directory for the current platform.
    
    Windows: %LOCALAPPDATA%/app_name (same as data)
    macOS: ~/Library/Application Support/app_name (same as data)
    Linux: ~/.config/app_name (XDG standard)
    
    Args:
        app_name: Application name (used as folder name)
        
    Returns:
        Path to user config directory (created if doesn't exist)
    """
    if is_windows() or is_macos():
        # Windows and macOS use the same location for config and data
        return get_user_data_dir(app_name)
    else:  # Linux and others
        # Follow XDG Base Directory Specification
        xdg_config = os.environ.get('XDG_CONFIG_HOME')
        if xdg_config:
            config_dir = Path(xdg_config) / app_name
        else:
            config_dir = Path.home() / ".config" / app_name
        
        config_dir.mkdir(parents=True, exist_ok=True)
        return config_dir


def get_user_log_dir(app_name: str) -> Path:
    """
    Get the appropriate user log directory for the current platform.
    
    Windows: %APPDATA%/app_name/logs
    macOS: ~/Library/Logs/app_name
    Linux: ~/.local/share/app_name/logs (or $XDG_DATA_HOME/app_name/logs)
    
    Args:
        app_name: Application name (used as folder name)
        
    Returns:
        Path to user log directory (created if doesn't exist)
    """
    if is_windows():
        appdata = os.environ.get('APPDATA')
        if appdata:
            log_dir = Path(appdata) / app_name / 'logs'
        else:
            log_dir = get_user_data_dir(app_name) / 'logs'
    elif is_macos():
        log_dir = Path.home() / "Library" / "Logs" / app_name
    else:  # Linux and others
        log_dir = get_user_data_dir(app_name) / 'logs'
    
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir


def get_user_cache_dir(app_name: str) -> Path:
    """
    Get the appropriate user cache directory for the current platform.
    
    Windows: %LOCALAPPDATA%/app_name/cache
    macOS: ~/Library/Caches/app_name
    Linux: ~/.cache/app_name (XDG standard)
    
    Args:
        app_name: Application name (used as folder name)
        
    Returns:
        Path to user cache directory (created if doesn't exist)
    """
    if is_windows():
        cache_dir = get_user_data_dir(app_name) / 'cache'
    elif is_macos():
        cache_dir = Path.home() / "Library" / "Caches" / app_name
    else:  # Linux and others
        xdg_cache = os.environ.get('XDG_CACHE_HOME')
        if xdg_cache:
            cache_dir = Path(xdg_cache) / app_name
        else:
            cache_dir = Path.home() / ".cache" / app_name
    
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


# =============================================================================
# ZWO ASI SDK Library Paths
# =============================================================================

def get_zwo_sdk_library_name() -> str:
    """
    Get the ZWO ASI SDK library name for the current platform.
    
    Windows: ASICamera2.dll
    macOS: libASICamera2.dylib
    Linux: libASICamera2.so
    
    Returns:
        Library filename for current platform
    """
    if is_windows():
        return "ASICamera2.dll"
    elif is_macos():
        return "libASICamera2.dylib"
    else:  # Linux
        return "libASICamera2.so"


def get_zwo_sdk_search_paths() -> list:
    """
    Get list of paths to search for ZWO ASI SDK library.
    
    Returns:
        List of paths to check for the SDK library
    """
    sdk_name = get_zwo_sdk_library_name()
    paths = []
    
    # Current directory / bundled with app
    paths.append(sdk_name)
    
    if is_windows():
        # Windows-specific locations
        program_files = os.environ.get('PROGRAMFILES', r'C:\Program Files')
        program_files_x86 = os.environ.get('PROGRAMFILES(X86)', r'C:\Program Files (x86)')
        
        paths.extend([
            os.path.join(program_files, 'PFRSentinel', '_internal', sdk_name),
            os.path.join(program_files_x86, 'PFRSentinel', '_internal', sdk_name),
            os.path.join(program_files, 'ZWO ASI', 'ASICamera2.dll'),
            os.path.join(program_files_x86, 'ZWO ASI', 'ASICamera2.dll'),
        ])
    elif is_macos():
        # macOS-specific locations
        paths.extend([
            f'/usr/local/lib/{sdk_name}',
            f'/opt/homebrew/lib/{sdk_name}',  # Apple Silicon Homebrew
            f'/Library/Frameworks/libASICamera2.framework/Versions/Current/{sdk_name}',
            os.path.expanduser(f'~/Library/Frameworks/{sdk_name}'),
            # Common astronomy software locations
            '/Applications/ASI Studio.app/Contents/Frameworks/' + sdk_name,
        ])
    else:  # Linux
        paths.extend([
            f'/usr/lib/{sdk_name}',
            f'/usr/local/lib/{sdk_name}',
            f'/usr/lib/x86_64-linux-gnu/{sdk_name}',
            f'/usr/lib/aarch64-linux-gnu/{sdk_name}',  # ARM64
            os.path.expanduser(f'~/.local/lib/{sdk_name}'),
            # Common astronomy software locations
            '/opt/zwo/' + sdk_name,
        ])
    
    return paths


def find_zwo_sdk() -> Optional[str]:
    """
    Search for ZWO ASI SDK library on the system.
    
    Returns:
        Path to SDK library if found, None otherwise
    """
    for path in get_zwo_sdk_search_paths():
        if os.path.isfile(path):
            return path
    return None


# =============================================================================
# ffmpeg Detection
# =============================================================================

def get_ffmpeg_executable() -> str:
    """
    Get the ffmpeg executable name for the current platform.
    
    Windows: ffmpeg.exe
    macOS/Linux: ffmpeg
    
    Returns:
        ffmpeg executable name
    """
    if is_windows():
        return "ffmpeg.exe"
    return "ffmpeg"


def find_ffmpeg() -> Optional[str]:
    """
    Search for ffmpeg executable on the system.
    
    Returns:
        Path to ffmpeg if found, None otherwise
    """
    import shutil
    
    # Check if ffmpeg is in PATH
    ffmpeg = shutil.which('ffmpeg')
    if ffmpeg:
        return ffmpeg
    
    # Platform-specific additional locations
    if is_windows():
        extra_paths = [
            r'C:\ffmpeg\bin\ffmpeg.exe',
            r'C:\Program Files\ffmpeg\bin\ffmpeg.exe',
            r'C:\Program Files (x86)\ffmpeg\bin\ffmpeg.exe',
        ]
    elif is_macos():
        extra_paths = [
            '/usr/local/bin/ffmpeg',
            '/opt/homebrew/bin/ffmpeg',  # Apple Silicon Homebrew
        ]
    else:  # Linux
        extra_paths = [
            '/usr/bin/ffmpeg',
            '/usr/local/bin/ffmpeg',
        ]
    
    for path in extra_paths:
        if os.path.isfile(path):
            return path
    
    return None


# =============================================================================
# System Commands
# =============================================================================

def open_file_manager(path: str) -> bool:
    """
    Open the system file manager at the specified path.
    
    Args:
        path: Directory path to open
        
    Returns:
        True if successful, False otherwise
    """
    import subprocess
    
    try:
        if is_windows():
            subprocess.run(['explorer', path], check=False)
        elif is_macos():
            subprocess.run(['open', path], check=False)
        else:  # Linux
            subprocess.run(['xdg-open', path], check=False)
        return True
    except Exception:
        return False


def open_url(url: str) -> bool:
    """
    Open a URL in the default browser.
    
    Args:
        url: URL to open
        
    Returns:
        True if successful, False otherwise
    """
    import webbrowser
    try:
        webbrowser.open(url)
        return True
    except Exception:
        return False


# =============================================================================
# DLL/Library Version Detection
# =============================================================================

def get_library_version(library_path: str) -> Optional[str]:
    """
    Get version information from a native library.
    
    Windows: Uses Windows API to read DLL version info
    macOS/Linux: Attempts to read version from library (limited support)
    
    Args:
        library_path: Path to the library file
        
    Returns:
        Version string if available, None otherwise
    """
    if not os.path.exists(library_path):
        return None
    
    if is_windows():
        return _get_windows_dll_version(library_path)
    else:
        # macOS and Linux don't have standardized version embedding
        # Return None or attempt otool/readelf if needed
        return None


def _get_windows_dll_version(dll_path: str) -> Optional[str]:
    """Get version info from a Windows DLL using Win32 API"""
    try:
        import ctypes
        from ctypes import wintypes
        
        version_dll = ctypes.windll.version
        size = version_dll.GetFileVersionInfoSizeW(dll_path, None)
        
        if not size:
            return None
        
        data = ctypes.create_string_buffer(size)
        if not version_dll.GetFileVersionInfoW(dll_path, 0, size, data):
            return None
        
        # Get the fixed file info
        p_buffer = ctypes.c_void_p()
        length = wintypes.UINT()
        if not version_dll.VerQueryValueW(data, "\\\\", ctypes.byref(p_buffer), ctypes.byref(length)):
            return None
        
        # VS_FIXEDFILEINFO structure
        class VS_FIXEDFILEINFO(ctypes.Structure):
            _fields_ = [
                ("dwSignature", wintypes.DWORD),
                ("dwStrucVersion", wintypes.DWORD),
                ("dwFileVersionMS", wintypes.DWORD),
                ("dwFileVersionLS", wintypes.DWORD),
                ("dwProductVersionMS", wintypes.DWORD),
                ("dwProductVersionLS", wintypes.DWORD),
                ("dwFileFlagsMask", wintypes.DWORD),
                ("dwFileFlags", wintypes.DWORD),
                ("dwFileOS", wintypes.DWORD),
                ("dwFileType", wintypes.DWORD),
                ("dwFileSubtype", wintypes.DWORD),
                ("dwFileDateMS", wintypes.DWORD),
                ("dwFileDateLS", wintypes.DWORD),
            ]
        
        info = ctypes.cast(p_buffer, ctypes.POINTER(VS_FIXEDFILEINFO)).contents
        major = (info.dwFileVersionMS >> 16) & 0xFFFF
        minor = info.dwFileVersionMS & 0xFFFF
        build = (info.dwFileVersionLS >> 16) & 0xFFFF
        revision = info.dwFileVersionLS & 0xFFFF
        return f"{major}.{minor}.{build}.{revision}"
    
    except Exception:
        return None


# =============================================================================
# Platform Info for UI/Logging
# =============================================================================

def get_platform_info() -> dict:
    """
    Get comprehensive platform information for logging/diagnostics.
    
    Returns:
        Dictionary with platform details
    """
    import platform as py_platform
    
    return {
        'platform': get_platform_name(),
        'platform_enum': CURRENT_PLATFORM.value,
        'system': py_platform.system(),
        'release': py_platform.release(),
        'version': py_platform.version(),
        'machine': py_platform.machine(),
        'processor': py_platform.processor(),
        'python_version': py_platform.python_version(),
        'is_frozen': getattr(sys, 'frozen', False),
    }
