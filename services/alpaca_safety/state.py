"""
Confirmed roof-safety verdict shared between the image pipeline and the Alpaca server.

The pipeline publishes the verdict its RoofSafetyFSM confirms and notes every
processed frame; the server only reads. A verdict nobody has refreshed for
``stale_after_seconds`` reads UNSAFE, so a stalled capture can never leave a
client looking at a stale "safe".
"""
import threading
import time
from datetime import datetime, timezone
from typing import Callable, Optional

DEFAULT_STALE_AFTER_SECONDS = 600


class SafetyState:
    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self._lock = threading.Lock()
        self._clock = clock
        self._safe = False
        self._last_frame: Optional[float] = None
        self._changed_at = datetime.now(timezone.utc)
        self.stale_after_seconds = DEFAULT_STALE_AFTER_SECONDS

    def publish(self, safe: bool) -> bool:
        """Record a confirmed verdict. Returns True when it differs from the last one."""
        with self._lock:
            changed = safe != self._safe
            self._safe = safe
            if changed:
                self._changed_at = datetime.now(timezone.utc)
            return changed

    def note_frame(self) -> None:
        with self._lock:
            self._last_frame = self._clock()

    def is_stale(self) -> bool:
        with self._lock:
            return self._stale_locked()

    def is_safe(self) -> bool:
        with self._lock:
            return self._safe and not self._stale_locked()

    def changed_at(self) -> datetime:
        with self._lock:
            return self._changed_at

    def _stale_locked(self) -> bool:
        if self._last_frame is None:
            return True
        return self._clock() - self._last_frame > self.stale_after_seconds


safety_state = SafetyState()
