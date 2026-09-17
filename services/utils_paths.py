"""
Path utilities for PyInstaller resource handling
Resolves paths correctly whether running from source or as bundled EXE
"""
import os
import sys
import tempfile

from platformdirs import user_data_dir

# Import app configuration for centralized naming
try:
    from .app_config import APP_DATA_FOLDER
except ImportError:
    APP_DATA_FOLDER = "PFRSentinel"  # Fallback


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
        # Running from source - go up to project root (this file is in services/)
        base_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    return os.path.join(base_path, relative_path)


def get_app_data_dir():
    r"""
    Get application data directory (for logs, user config, etc.)

    Returns:
        Windows: %LOCALAPPDATA%\{APP_DATA_FOLDER}
        macOS:   ~/Library/Application Support/{APP_DATA_FOLDER}
        Linux:   ~/.local/share/{APP_DATA_FOLDER} (honours $XDG_DATA_HOME)
    """
    # Windows reads the LOCALAPPDATA environment variable first, exactly as
    # this function always has: platformdirs resolves the known folder through
    # SHGetKnownFolderPath and ignores that variable, so rigs that relocated
    # AppData by setting it would silently come up with an empty data root —
    # no config, no camera profiles, no all-sky calibration. platformdirs is
    # the fallback, and the only path off Windows.
    local_app_data = os.environ.get('LOCALAPPDATA') if sys.platform == 'win32' else None
    if local_app_data:
        app_dir = os.path.join(local_app_data, APP_DATA_FOLDER)
    else:
        # appauthor=False is load-bearing: platformdirs otherwise falls back to
        # `appauthor or appname` on Windows and returns
        # %LOCALAPPDATA%\PFRSentinel\PFRSentinel, orphaning every existing config.
        app_dir = user_data_dir(APP_DATA_FOLDER, appauthor=False)

    # Create directory if it doesn't exist. In sandboxed/dev environments the
    # home directory may be read-only; use the temp directory rather than
    # failing module import.
    try:
        os.makedirs(app_dir, exist_ok=True)
    except PermissionError:
        app_dir = os.path.join(tempfile.gettempdir(), APP_DATA_FOLDER)
        os.makedirs(app_dir, exist_ok=True)

    return app_dir


def get_log_dir():
    r"""
    Get log directory path

    Returns:
        Path to <app data dir>/logs
    """
    # Lowercase 'logs', and built by hand rather than via
    # platformdirs.user_log_dir: that helper appends 'Logs' on Windows and
    # redirects to ~/Library/Logs on macOS, re-splitting the single root.
    log_dir = os.path.join(get_app_data_dir(), 'logs')
    os.makedirs(log_dir, exist_ok=True)
    return log_dir


def get_ml_contribution_dir():
    r"""
    Get ML data contribution directory path.

    Returns:
        Path to <app data dir>/ml_contribution
    """
    ml_dir = os.path.join(get_app_data_dir(), 'ml_contribution')
    os.makedirs(ml_dir, exist_ok=True)
    return ml_dir


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
