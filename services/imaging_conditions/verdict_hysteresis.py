"""
Frame verdicts -> ``imaging_ok``, with hysteresis both ways.

Same shape as ``observing_window._NoStarsStreak``: a run of ``bad_frames``
bad frames switches imaging off, a run of ``good_frames`` good frames
switches it back on, and a frame of the other kind breaks the run. One
passing cloud must not abort a sub-exposure and one clear gap must not
restart a sequence under cloud.

A start is the exception (installation day, 2026-11-24: NINA read "not OK"
only because too few frames had been judged). Until a bad frame has been
seen since the start or the last ``reset``, nothing has said the sky is bad,
so the first good frame is enough; once one has, the full ``good_frames``
run applies. ``export`` / ``restore`` carry the streaks across an app
restart (``session_state``).
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
        self._bad_seen = False
        self._changed_at: Optional[datetime] = None

    def reset(self, now: Optional[datetime] = None) -> None:
        """Start over from not-OK. With ``now``, an OK state ending here is a
        transition and is stamped; without it the history is forgotten too."""
        with self._lock:
            if now is None:
                self._changed_at = None
            elif self._ok:
                self._changed_at = now
            self._ok = self._ever_ok = self._bad_seen = False
            self._bad = self._good = self._frames = 0

    def observe(self, frame_ok: bool, *, bad_frames: int, good_frames: int,
                now: datetime) -> bool:
        """Count one frame. Returns True when ``imaging_ok`` changed."""
        with self._lock:
            self._frames += 1
            if frame_ok:
                self._good += 1
                self._bad = 0
                needed = good_frames if self._bad_seen else 1
                if not self._ok and self._good >= needed:
                    self._ok = self._ever_ok = True
                    self._changed_at = now
                    return True
            else:
                self._bad += 1
                self._good = 0
                self._bad_seen = True
                if self._ok and self._bad >= bad_frames:
                    self._ok = False
                    self._changed_at = now
                    return True
            return False

    def state(self) -> dict:
        with self._lock:
            return {'imaging_ok': self._ok, 'bad_streak': self._bad,
                    'good_streak': self._good, 'frames': self._frames,
                    'ever_ok': self._ever_ok, 'bad_seen': self._bad_seen,
                    'changed_at': self._changed_at}

    def export(self) -> dict:
        """The state as JSON-safe values, for ``restore`` after a restart."""
        state = self.state()
        changed = state['changed_at']
        state['changed_at'] = None if changed is None else changed.isoformat()
        return state

    def restore(self, data) -> bool:
        """Adopt an ``export``. Anything malformed changes nothing."""
        try:
            ok, ever_ok, bad_seen = (data['imaging_ok'], data['ever_ok'], data['bad_seen'])
            counts = [data['bad_streak'], data['good_streak'], data['frames']]
            changed = data.get('changed_at')
            changed = None if changed is None else datetime.fromisoformat(changed)
        except (KeyError, TypeError, ValueError):
            return False
        if not all(isinstance(v, bool) for v in (ok, ever_ok, bad_seen)):
            return False
        if not all(isinstance(v, int) and not isinstance(v, bool) and v >= 0 for v in counts):
            return False
        if changed is not None and changed.tzinfo is None:
            return False
        with self._lock:
            self._ok, self._ever_ok, self._bad_seen = ok, ever_ok or ok, bad_seen
            self._bad, self._good, self._frames = counts
            self._changed_at = changed
        return True
