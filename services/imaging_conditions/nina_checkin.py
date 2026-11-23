"""
When was ``GET /conditions`` last asked for, and by whom.

The route handler records every request that reached the auth check
(``web_conditions``); Live Monitoring shows "NINA last checked 4 s ago", or
that NINA hasn't checked in. The NINA plugin identifies itself with the
User-Agent ``PFRSentinel-NINA/<version>`` (``SentinelClient.cs``), so its
requests are told apart from curl or a script reading the same route. Only a
short label is kept, never the token or the full header.
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from typing import Callable, Optional

NINA_AGENT_PREFIX = 'PFRSentinel-NINA'
NINA = 'NINA plugin'


def client_label(user_agent) -> str:
    agent = str(user_agent or '').strip()
    if agent.startswith(NINA_AGENT_PREFIX):
        return NINA
    lowered = agent.lower()
    if lowered.startswith('curl/'):
        return 'curl'
    if 'powershell' in lowered:
        return 'PowerShell'
    if lowered.startswith('python'):
        return 'a Python script'
    if 'mozilla' in lowered:
        return 'a web browser'
    return 'another client'


class NinaCheckins:
    def __init__(self, clock: Callable[[], float] = time.monotonic,
                 wall: Callable[[], datetime] = lambda: datetime.now(timezone.utc)):
        self._clock = clock
        self._wall = wall
        self._lock = threading.Lock()
        self._last_any: Optional[dict] = None
        self._last_nina: Optional[dict] = None

    def record(self, user_agent, *, accepted: bool) -> None:
        label = client_label(user_agent)
        entry = {'mono': self._clock(), 'at': self._wall(), 'client': label,
                 'accepted': bool(accepted)}
        with self._lock:
            self._last_any = entry
            if label == NINA:
                self._last_nina = entry

    def last(self) -> dict:
        """``{'nina': {...} | None, 'any': {...} | None}``; each entry carries
        ``age_s``, ``at``, ``client`` and ``accepted``."""
        now = self._clock()
        with self._lock:
            entries = {'nina': self._last_nina, 'any': self._last_any}
        out = {}
        for key, entry in entries.items():
            if entry is None:
                out[key] = None
                continue
            out[key] = {'age_s': max(0.0, now - entry['mono']), 'at': entry['at'],
                        'client': entry['client'], 'accepted': entry['accepted']}
        return out

    def reset(self) -> None:
        with self._lock:
            self._last_any = self._last_nina = None


_checkins: Optional[NinaCheckins] = None
_checkins_lock = threading.Lock()


def get_nina_checkins() -> NinaCheckins:
    global _checkins
    with _checkins_lock:
        if _checkins is None:
            _checkins = NinaCheckins()
        return _checkins


def reset_nina_checkins() -> None:
    global _checkins
    with _checkins_lock:
        _checkins = None
