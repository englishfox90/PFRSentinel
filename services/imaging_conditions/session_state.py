"""
The feed's short-term memory, kept across an app restart.

The verdict streaks, the recent star counts the trend is taken against, the
night's FWHM readings and the frame intervals live in memory, so a restart
in the middle of a night used to start from nothing: no verdict until new
frames arrived, no trend for three frames, no FWHM reference for ten. They
are saved here as frames come in and adopted by the first frame after a
restart — but only while they are younger than the staleness limit, the
same gap after which the running feed starts over anyway. An older file
describes a sky nobody has looked at since and is ignored.

``<app-data>/imaging_conditions/session_state.json``: written atomically,
at most every SAVE_INTERVAL_S (and at once when the verdict changes), read
tolerantly — a truncated, oversized, foreign or future-dated file loads as
nothing, never as an error. The learned clear-sky baseline is not here; it
has its own file and no age limit (``star_baseline``).
"""
from __future__ import annotations

import json
import math
import os
import tempfile
import threading
from datetime import datetime
from typing import List, Optional

from services.logger import app_logger
from services.utils_paths import get_app_data_dir

FILE_VERSION = 1
FILENAME = 'session_state.json'
SUBDIR = 'imaging_conditions'
SAVE_INTERVAL_S = 20.0
MAX_FILE_BYTES = 64 * 1024
MAX_STAR_COUNT = 100_000
MAX_FWHM_PX = 200.0
MAX_INTERVAL_S = 24 * 3600.0


def default_state_path() -> str:
    return os.path.join(get_app_data_dir(), SUBDIR, FILENAME)


def _ints(values, limit: int) -> List[int]:
    if not isinstance(values, list):
        return []
    return [v for v in values[-limit:]
            if isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= MAX_STAR_COUNT]


def _floats(values, limit: int, ceiling: float) -> List[float]:
    if not isinstance(values, list):
        return []
    return [float(v) for v in values[-limit:]
            if isinstance(v, (int, float)) and not isinstance(v, bool)
            and math.isfinite(v) and 0 < v <= ceiling]


class SessionState:
    """Throttled atomic writer and tolerant reader of the feed's state."""

    def __init__(self, path: Optional[str] = None, *,
                 save_interval_s: float = SAVE_INTERVAL_S):
        self._path = path
        self._save_interval_s = save_interval_s
        self._lock = threading.Lock()
        self._last_save: Optional[datetime] = None

    @property
    def path(self) -> str:
        return self._path or default_state_path()

    def save(self, *, at: datetime, verdict: dict, counts, fwhm, intervals,
             force: bool = False) -> bool:
        """Write the state unless one was written within the throttle."""
        with self._lock:
            if (not force and self._last_save is not None
                    and 0 <= (at - self._last_save).total_seconds() < self._save_interval_s):
                return False
            self._last_save = at
        return self._write({'version': FILE_VERSION, 'saved_at': at.isoformat(),
                            'verdict': verdict, 'counts': list(counts),
                            'fwhm': list(fwhm), 'intervals': list(intervals)})

    def load(self, *, now: datetime, counts_max: int, fwhm_max: int,
             intervals_max: int) -> Optional[dict]:
        """The saved state with its age in seconds, or None when there is
        none worth reading. The caller decides whether the age is recent."""
        path = self.path
        try:
            if not os.path.isfile(path) or os.path.getsize(path) > MAX_FILE_BYTES:
                return None
            with open(path, encoding='utf-8') as f:
                data = json.load(f)
            if not isinstance(data, dict) or data.get('version') != FILE_VERSION:
                return None
            saved_at = datetime.fromisoformat(data['saved_at'])
            age_s = (now - saved_at).total_seconds()
        except Exception as e:
            app_logger.debug(f"Imaging conditions: saved session state unreadable: {e}")
            return None
        if not math.isfinite(age_s) or age_s < 0 or not isinstance(data.get('verdict'), dict):
            return None
        return {'age_s': age_s, 'verdict': data['verdict'],
                'counts': _ints(data.get('counts'), counts_max),
                'fwhm': _floats(data.get('fwhm'), fwhm_max, MAX_FWHM_PX),
                'intervals': _floats(data.get('intervals'), intervals_max, MAX_INTERVAL_S)}

    def _write(self, data: dict) -> bool:
        path = self.path
        tmp = None
        try:
            folder = os.path.dirname(path)
            os.makedirs(folder, exist_ok=True)
            with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=folder,
                                             prefix='.session_state.', suffix='.tmp',
                                             delete=False) as f:
                tmp = f.name
                json.dump(data, f)
            os.replace(tmp, path)
            return True
        except Exception as e:
            app_logger.debug(f"Imaging conditions: could not save session state: {e}")
            if tmp and os.path.isfile(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass
            return False
