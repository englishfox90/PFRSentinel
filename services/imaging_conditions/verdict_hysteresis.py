"""
Frame verdicts -> ``imaging_ok``, with hysteresis both ways.

Same shape as ``observing_window._NoStarsStreak``: a run of ``bad_frames``
bad frames switches imaging off, a run of ``good_frames`` good frames
switches it back on, and a frame of the other kind breaks the run. One
passing cloud must not abort a sub-exposure and one clear gap must not
restart a sequence under cloud. It starts off: nothing is vouched for until
``good_frames`` good frames have been seen, and ``reset`` (after the feed
went stale, or the night ended) starts from there again.
"""
from __future__ import annotations

import threading
from datetime import datetime
from typing import Optional


class VerdictHysteresis:
    def __init__(self):
        self._lock = threading.Lock()
        self._ok = False
        self._bad = self._good = self._frames = 0
        self._ever_ok = False
        self._changed_at: Optional[datetime] = None

    def reset(self, now: Optional[datetime] = None) -> None:
        """Start over from not-OK. With ``now``, an OK state ending here is a
        transition and is stamped; without it the history is forgotten too."""
        with self._lock:
            if now is None:
                self._changed_at = None
            elif self._ok:
                self._changed_at = now
            self._ok = self._ever_ok = False
            self._bad = self._good = self._frames = 0

    def observe(self, frame_ok: bool, *, bad_frames: int, good_frames: int,
                now: datetime) -> bool:
        """Count one frame. Returns True when ``imaging_ok`` changed."""
        with self._lock:
            self._frames += 1
            if frame_ok:
                self._good += 1
                self._bad = 0
                if not self._ok and self._good >= good_frames:
                    self._ok = self._ever_ok = True
                    self._changed_at = now
                    return True
            else:
                self._bad += 1
                self._good = 0
                if self._ok and self._bad >= bad_frames:
                    self._ok = False
                    self._changed_at = now
                    return True
            return False

    def state(self) -> dict:
        with self._lock:
            return {'imaging_ok': self._ok, 'bad_streak': self._bad,
                    'good_streak': self._good, 'frames': self._frames,
                    'ever_ok': self._ever_ok,
                    'changed_at': self._changed_at}
