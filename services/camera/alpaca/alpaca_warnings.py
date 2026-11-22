"""Warnings for one Alpaca capture session, limited across reconnects.

A device object is rebuilt on every reconnect, so a "warn once" kept on it
repeats after each one — every few minutes during an outage. ``SessionWarnings``
lives on the ``AlpacaConnection``, which lasts the whole session.
"""
import threading
import time
from typing import Callable

from services.logger import app_logger

REPEAT_EVERY_S = 900.0


class SessionWarnings:
    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        self._seen = {}
        self._lock = threading.Lock()
        self.state = {}   # small per-session facts the frame path keeps (counters)

    def _due(self, key, every_s) -> int:
        """0 when muted, else how many times ``key`` came up since last logged."""
        now = self._clock()
        with self._lock:
            last, count = self._seen.get(key, (None, 0))
            count += 1
            if last is not None and (every_s is None or now - last < every_s):
                self._seen[key] = (last, count)
                return 0
            self._seen[key] = (now, 0)
            return count

    def once(self, key, message: str) -> bool:
        """WARNING the first time ``key`` comes up this session; True if logged."""
        if self._due(key, None):
            app_logger.warning(message)
            return True
        return False

    def debug_once(self, key, message: str) -> None:
        if self._due(key, None):
            app_logger.debug(message)

    def limited(self, key, message: str, every_s: float = REPEAT_EVERY_S) -> bool:
        """WARNING at most once per ``every_s``, with the muted repeats counted."""
        count = self._due(key, every_s)
        if not count:
            return False
        app_logger.warning(message if count == 1 else f"{message} (seen {count} times)")
        return True


__all__ = ["SessionWarnings", "REPEAT_EVERY_S"]
