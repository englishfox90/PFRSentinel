"""
Performance measurement helpers for PFR Sentinel.

Provides processing time tracking, memory usage, and disk space queries.
"""
import os
import sys
import time
import shutil
from typing import Optional

from .logger import app_logger


class ProcessingTimer:
    """Context manager that measures elapsed processing time."""

    def __init__(self):
        self.elapsed = 0.0

    def __enter__(self):
        self._start = time.perf_counter()
        return self

    def __exit__(self, *args):
        self.elapsed = time.perf_counter() - self._start


_MB = 1024 * 1024


def get_process_memory() -> Optional[dict]:
    """Return this process's memory counters in megabytes, or None on failure.

    Keys: ``working_set_mb`` (what Task Manager's "Memory" column shows —
    resident private pages, so a working-set trim lowers it without freeing
    anything), ``peak_working_set_mb`` (lifetime high-water mark, which is
    where a per-frame transient shows up), ``private_mb`` (committed private
    bytes — the number that tracks real retention, because a trim cannot
    touch it) and ``page_faults``. A counter the platform cannot supply is -1.
    """
    try:
        import psutil
        info = psutil.Process(os.getpid()).memory_info()
        return {
            'working_set_mb': info.rss / _MB,
            'peak_working_set_mb': getattr(info, 'peak_wset', -_MB) / _MB,
            'private_mb': getattr(info, 'private', getattr(info, 'data', -_MB)) / _MB,
            'page_faults': int(getattr(info, 'num_page_faults', -1)),
        }
    except ImportError:
        pass
    except OSError as e:
        app_logger.debug(f"psutil memory query failed: {e}")

    if sys.platform == 'win32':
        return _win32_process_memory()
    return _proc_status_memory()


def _win32_process_memory() -> Optional[dict]:
    try:
        import ctypes
        from ctypes import wintypes

        class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        # Private DLL handles with explicit prototypes. Untyped, the 64-bit
        # pseudo-handle from GetCurrentProcess() is truncated to a C int and
        # GetProcessMemoryInfo silently fails (this returned -1.0 on every
        # frozen build); and prototyping ctypes.windll's shared objects would
        # leak the types into every other caller in the process.
        kernel32 = ctypes.WinDLL('kernel32')
        psapi = ctypes.WinDLL('psapi')
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
        psapi.GetProcessMemoryInfo.argtypes = [
            wintypes.HANDLE, ctypes.POINTER(PROCESS_MEMORY_COUNTERS), wintypes.DWORD,
        ]

        counters = PROCESS_MEMORY_COUNTERS()
        counters.cb = ctypes.sizeof(counters)
        if psapi.GetProcessMemoryInfo(
            kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb
        ):
            return {
                'working_set_mb': counters.WorkingSetSize / _MB,
                'peak_working_set_mb': counters.PeakWorkingSetSize / _MB,
                # PagefileUsage is the process's private commit charge.
                'private_mb': counters.PagefileUsage / _MB,
                'page_faults': int(counters.PageFaultCount),
            }
    except (ImportError, AttributeError):
        pass
    except OSError as e:
        app_logger.debug(f"Win32 memory query failed: {e}")
    return None


def _proc_status_memory() -> Optional[dict]:
    """Linux ``/proc/self/status``; other platforms have no cheap equivalent."""
    try:
        with open('/proc/self/status', encoding='ascii', errors='replace') as fh:
            fields = {}
            for line in fh:
                key, _, rest = line.partition(':')
                parts = rest.split()
                if len(parts) >= 2 and parts[1] == 'kB':
                    fields[key] = int(parts[0]) * 1024
        if 'VmRSS' not in fields:
            return None
        return {
            'working_set_mb': fields['VmRSS'] / _MB,
            'peak_working_set_mb': fields.get('VmHWM', -_MB) / _MB,
            # VmData is the closest thing to a private commit charge here.
            'private_mb': fields.get('VmData', -_MB) / _MB,
            'page_faults': -1,
        }
    except OSError:
        return None


def get_memory_usage_mb():
    """Return current process memory usage in megabytes.

    Returns:
        float: resident (working set) memory in MB, or -1.0 on failure.
    """
    info = get_process_memory()
    if info is None:
        return -1.0
    return float(info['working_set_mb'])


def get_disk_space(path):
    """Return disk space info for the given path.

    Args:
        path: Directory or file path to check

    Returns:
        dict with 'total_gb', 'used_gb', 'free_gb', or None on failure.
    """
    try:
        if not path or not os.path.exists(path):
            return None

        usage = shutil.disk_usage(path)
        return {
            'total_gb': round(usage.total / (1024 ** 3), 2),
            'used_gb': round(usage.used / (1024 ** 3), 2),
            'free_gb': round(usage.free / (1024 ** 3), 2),
        }
    except (OSError, ValueError):
        return None
