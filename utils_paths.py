"""
Path utilities for PyInstaller resource handling
Resolves paths correctly whether running from source or as bundled EXE

Cross-platform support:
- Windows: %LOCALAPPDATA%/PFRSentinel
- macOS: ~/Library/Application Support/PFRSentinel
- Linux: ~/.local/share/PFRSentinel (XDG standard)
"""
import os
import sys

# Import app configuration for centralized naming
try:
    from app_config import APP_DATA_FOLDER
except ImportError:
    APP_DATA_FOLDER = "PFRSentinel"  # Fallback

# Import cross-platform utilities
try:
    from services.platform import (
        get_user_data_dir, 
        get_user_log_dir,
        is_windows,
        is_macos,
    )
    _HAS_PLATFORM_MODULE = True
except ImportError:
    _HAS_PLATFORM_MODULE = False


def resource_path(relative_path):
    """
    Get absolute path to resource, works for dev and for PyInstaller
    
    Args:
        relative_path: Path relative to application root
        
    Returns:
        Absolute path to resource
    """
    try:
        # PyInstaller creates a temp folder and stores path in _MEIPASS
        base_path = sys._MEIPASS
    except AttributeError:
        # Running from source - use script directory
        base_path = os.path.dirname(os.path.abspath(__file__))
    
    return os.path.join(base_path, relative_path)


def get_app_data_dir():
    """
    Get application data directory (for logs, user config, etc.)
    
    Cross-platform support:
        Windows: %LOCALAPPDATA%/PFRSentinel
        macOS: ~/Library/Application Support/PFRSentinel
        Linux: ~/.local/share/PFRSentinel (XDG standard)
    
    Returns:
        Path to user data directory
    """
    if _HAS_PLATFORM_MODULE:
        return str(get_user_data_dir(APP_DATA_FOLDER))
    
    # Fallback implementation if platform module not available
    if sys.platform == 'win32':
        # Use LOCALAPPDATA on Windows
        local_app_data = os.environ.get('LOCALAPPDATA')
        if not local_app_data:
            # Fallback to APPDATA if LOCALAPPDATA not available
            local_app_data = os.environ.get('APPDATA', os.path.expanduser('~'))
        app_dir = os.path.join(local_app_data, APP_DATA_FOLDER)
    elif sys.platform == 'darwin':
        # macOS: ~/Library/Application Support/
        app_dir = os.path.join(os.path.expanduser('~'), 'Library', 'Application Support', APP_DATA_FOLDER)
    else:
        # Linux/Unix: XDG Base Directory Specification
        xdg_data = os.environ.get('XDG_DATA_HOME')
        if xdg_data:
            app_dir = os.path.join(xdg_data, APP_DATA_FOLDER)
        else:
            app_dir = os.path.join(os.path.expanduser('~'), '.local', 'share', APP_DATA_FOLDER)
    
    # Create directory if it doesn't exist
    os.makedirs(app_dir, exist_ok=True)
    
    return app_dir


def get_log_dir():
    """
    Get log directory path
    
    Cross-platform support:
        Windows: %APPDATA%/PFRSentinel/logs
        macOS: ~/Library/Logs/PFRSentinel
        Linux: ~/.local/share/PFRSentinel/logs
    
    Returns:
        Path to log directory
    """
    if _HAS_PLATFORM_MODULE:
        return str(get_user_log_dir(APP_DATA_FOLDER))
    
    # Fallback implementation
    if sys.platform == 'darwin':
        # macOS: ~/Library/Logs/
        log_dir = os.path.join(os.path.expanduser('~'), 'Library', 'Logs', APP_DATA_FOLDER)
    else:
        log_dir = os.path.join(get_app_data_dir(), 'logs')
    
    os.makedirs(log_dir, exist_ok=True)
    return log_dir


def get_exe_dir():
    """
    Get the directory where the EXE is installed/running from.
    
    Returns:
        Absolute path to the directory containing the executable (or script in dev mode)
    """
    if getattr(sys, 'frozen', False):
        # Running as compiled executable
        return os.path.dirname(sys.executable)
    else:
        # Running from source
        return os.path.dirname(os.path.abspath(__file__))
