"""
Heartbeat writer for process supervision.

Writes a timestamp to a heartbeat file every N seconds so an external
supervisor (Task Scheduler, sentinel_supervisor.py, etc.) can detect
when the application has hung or crashed.
"""
import json
import os
import threading
import time
from datetime import datetime, timezone

from .logger import app_logger
from .utils_paths import get_app_data_dir

# Default heartbeat interval in seconds
DEFAULT_INTERVAL = 30

# Heartbeat is considered stale after this many missed intervals
STALE_MULTIPLIER = 3


def get_heartbeat_path():
    """Return the path to the heartbeat file."""
    return os.path.join(get_app_data_dir(), 'heartbeat.json')


def write_heartbeat(path=None):
    """Write current timestamp to the heartbeat file.

    Args:
        path: Optional override path. Defaults to get_heartbeat_path().

    Returns:
        True if written successfully, False otherwise.
    """
    if path is None:
        path = get_heartbeat_path()

    data = {
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'pid': os.getpid(),
    }

    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w') as f:
            json.dump(data, f)
        return True
    except OSError as e:
        app_logger.error(f"Failed to write heartbeat: {e}")
        return False


def read_heartbeat(path=None):
    """Read the heartbeat file.

    Args:
        path: Optional override path. Defaults to get_heartbeat_path().

    Returns:
        dict with 'timestamp' (datetime) and 'pid' (int), or None on failure.
    """
    if path is None:
        path = get_heartbeat_path()

    try:
        with open(path, 'r') as f:
            data = json.load(f)
        ts = datetime.fromisoformat(data['timestamp'])
        return {'timestamp': ts, 'pid': data.get('pid')}
    except (OSError, json.JSONDecodeError, KeyError, ValueError):
        return None


def is_heartbeat_stale(path=None, interval=DEFAULT_INTERVAL):
    """Check whether the heartbeat file is stale (too old).

    Args:
        path: Optional override path.
        interval: Expected heartbeat interval in seconds.

    Returns:
        True if stale or unreadable, False if healthy.
    """
    hb = read_heartbeat(path)
    if hb is None:
        return True

    ts = hb['timestamp']
    # Ensure timezone-aware comparison
    now = datetime.now(timezone.utc)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)

    age = (now - ts).total_seconds()
    return age > interval * STALE_MULTIPLIER


class HeartbeatWriter:
    """Background thread that periodically writes heartbeat timestamps."""

    def __init__(self, interval=DEFAULT_INTERVAL, path=None):
        self._interval = interval
        self._path = path
        self._stop_event = threading.Event()
        self._thread = None

    def start(self):
        """Start the heartbeat writer thread."""
        if self._thread and self._thread.is_alive():
            return

        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run, name='heartbeat-writer', daemon=True
        )
        self._thread.start()
        app_logger.info(f"Heartbeat writer started (interval={self._interval}s)")

    def stop(self):
        """Stop the heartbeat writer thread."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None
        app_logger.info("Heartbeat writer stopped")

    def _run(self):
        """Writer loop."""
        while not self._stop_event.is_set():
            write_heartbeat(self._path)
            self._stop_event.wait(self._interval)
