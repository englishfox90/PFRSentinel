"""
Application Configuration - Central place for app identity
Change these values when renaming the application

Cross-platform support:
- Windows: ASICamera2.dll
- macOS: libASICamera2.dylib
- Linux: libASICamera2.so
"""
import sys

# Application Identity
APP_NAME = "PFRSentinel"  # Internal name (no spaces, used for paths)
APP_DISPLAY_NAME = "PFR Sentinel"  # Display name (shown to users)
APP_SUBTITLE = "Live Camera Monitoring & Overlay System for Observatories"
APP_DESCRIPTION = "Astrophotography image overlay and monitoring application"
APP_AUTHOR = "Paul Fox-Reeks"
APP_URL = "https://github.com/englishfox90/PFRSentinel"

# Directory names (used for AppData/Application Support paths)
APP_DATA_FOLDER = "PFRSentinel"

# File names
MAIN_CONFIG_FILE = "config.json"
LOG_FILE = "sentinel.log"

# Default paths
DEFAULT_OUTPUT_SUBFOLDER = "Images"

# SDK/Driver info - platform-specific library names
ZWO_SDK_WINDOWS = "ASICamera2.dll"
ZWO_SDK_MACOS = "libASICamera2.dylib"
ZWO_SDK_LINUX = "libASICamera2.so"

# Legacy alias for backward compatibility
ZWO_SDK_DLL = ZWO_SDK_WINDOWS

# Build identifiers - New GUID for renamed app
INNO_SETUP_APP_ID = "{{7F8E9A0B-1C2D-3E4F-5A6B-7C8D9E0F1A2B}"


def get_zwo_sdk_name() -> str:
    """Get the ZWO ASI SDK library name for the current platform.
    
    Returns:
        Library filename: ASICamera2.dll (Windows), 
                         libASICamera2.dylib (macOS), 
                         libASICamera2.so (Linux)
    """
    if sys.platform == 'win32':
        return ZWO_SDK_WINDOWS
    elif sys.platform == 'darwin':
        return ZWO_SDK_MACOS
    else:
        return ZWO_SDK_LINUX


def get_window_title(version: str = None) -> str:
    """Get formatted window title with optional version"""
    if version:
        return f"{APP_DISPLAY_NAME} v{version}"
    return APP_DISPLAY_NAME


def get_user_agent() -> str:
    """Get user agent string for HTTP requests"""
    from version import __version__
    return f"{APP_NAME}/{__version__}"
