"""
The last few hours of imaging-conditions verdicts, for the Live Monitoring
chart and event list (and the ``events`` block of ``GET /conditions``).

The feed records one sample per judged frame (``record``) on the image
worker thread; the GUI timer and the HTTP thread read copies. Every access
goes through one lock, and the ring is bounded twice: by age
(``MAX_AGE_S``) and by count (``MAX_SAMPLES``), so a fast cadence cannot
grow it past a few megabytes.

Events — imaging paused / resumed — are derived as samples arrive: a change
of ``imaging_ok``, or a gap longer than the staleness limit while imaging was
OK (frames stopped, so NINA was told to pause at ``last frame + stale_after``).
They are rare (a few a night), so they are also kept on disk at
``<app-data>/imaging_conditions/events.json`` — written atomically on each
new event, bounded to ``MAX_EVENTS`` and to ``EVENT_MAX_AGE_S`` on load — so
a restart in the middle of the night still shows why imaging paused. The
samples are not persisted: the chart refills in minutes and a ring of
thousands of points written every frame is not worth the disk traffic.
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, List, Optional, Tuple

from services.logger import app_logger
from services.utils_paths import get_app_data_dir

MAX_AGE_S = 12 * 3600.0
MAX_SAMPLES = 8000
MAX_EVENTS = 60
EVENT_MAX_AGE_S = 24 * 3600.0
MAX_FILE_BYTES = 64 * 1024
API_EVENTS = 10
FILENAME = 'events.json'
SUBDIR = 'imaging_conditions'

PAUSED = 'paused'
RESUMED = 'resumed'
STALE_REASON = 'stale'
RESTARTED_REASON = 'restarted'


@dataclass(frozen=True)
class Sample:
    at: datetime
    star_index: Optional[int]
    baseline: Optional[float]
    imaging_ok: bool
    frame_ok: Optional[bool]
    reasons: Tuple[str, ...]


@dataclass(frozen=True)
class Event:
    at: datetime
    kind: str
    reasons: Tuple[str, ...]


def default_events_path() -> str:
    return os.path.join(get_app_data_dir(), SUBDIR, FILENAME)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')


def _parse(value) -> Optional[datetime]:
    try:
        stamp = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except (TypeError, ValueError):
        return None
    return stamp if stamp.tzinfo is not None else None


def _star_index(record: dict) -> Optional[int]:
    count = (record.get('stars') or {}).get('count')
    return count if isinstance(count, int) and not isinstance(count, bool) else None


class ConditionsHistory:
    def __init__(self, path: Optional[str] = None, *, persist: bool = True,
                 max_samples: int = MAX_SAMPLES, max_age_s: float = MAX_AGE_S,
                 wall: Callable[[], datetime] = lambda: datetime.now(timezone.utc)):
        self._path = path
        self._persist = persist
        self._max_age = timedelta(seconds=max_age_s)
        self._wall = wall
        self._lock = threading.Lock()
        self._samples: deque = deque(maxlen=max_samples)
        self._events: deque = deque(maxlen=MAX_EVENTS)
        self._ok: Optional[bool] = None
        self._loaded = not persist

    @property
    def path(self) -> str:
        return self._path or default_events_path()

    # -- writing (image worker thread) ------------------------------------

    def record(self, record: dict, *, at: datetime, stale_after_s: float) -> None:
        sample = Sample(at=at, star_index=_star_index(record),
                        baseline=(record.get('stars') or {}).get('baseline'),
                        imaging_ok=bool(record.get('imaging_ok')),
                        frame_ok=record.get('frame_ok'),
                        reasons=tuple(record.get('reasons') or ()))
        with self._lock:
            self._ensure_loaded_locked()
            added = self._derive_events_locked(sample, stale_after_s)
            self._samples.append(sample)
            self._prune_locked(at)
            snapshot = list(self._events) if added else None
        if snapshot is not None and self._persist:
            self._write(snapshot)

    def _derive_events_locked(self, sample: Sample, stale_after_s: float) -> bool:
        added = False
        prev = self._samples[-1] if self._samples else None
        if (prev is not None and self._ok
                and (sample.at - prev.at).total_seconds() > stale_after_s):
            self._events.append(Event(prev.at + timedelta(seconds=stale_after_s),
                                      PAUSED, (STALE_REASON,)))
            self._ok = False
            added = True
        if sample.imaging_ok and not self._ok:
            self._events.append(Event(sample.at, RESUMED, ()))
            added = True
        elif not sample.imaging_ok and self._ok:
            self._events.append(Event(sample.at, PAUSED, sample.reasons))
            added = True
        self._ok = sample.imaging_ok
        return added

    def _prune_locked(self, now: datetime) -> None:
        cutoff = now - self._max_age
        while self._samples and self._samples[0].at < cutoff:
            self._samples.popleft()

    # -- reading (GUI / HTTP threads) --------------------------------------

    def samples(self) -> List[Sample]:
        with self._lock:
            self._ensure_loaded_locked()
            cutoff = self._wall() - self._max_age
            return [s for s in self._samples if s.at >= cutoff]

    def events(self) -> List[dict]:
        """Newest first: ``at``, ``kind``, ``reasons``, and ``ended_at`` /
        ``duration_s`` (None while the state still holds)."""
        with self._lock:
            self._ensure_loaded_locked()
            events = list(self._events)
        out = []
        for i, ev in enumerate(events):
            ended = events[i + 1].at if i + 1 < len(events) else None
            out.append({'at': ev.at, 'kind': ev.kind, 'reasons': list(ev.reasons),
                        'ended_at': ended,
                        'duration_s': None if ended is None
                        else round(max(0.0, (ended - ev.at).total_seconds()), 1)})
        out.reverse()
        return out

    def events_payload(self, limit: int = API_EVENTS) -> List[dict]:
        """The ``events`` block of ``GET /conditions``: JSON-ready, newest first."""
        return [{'at': _iso(e['at']), 'kind': e['kind'], 'reasons': e['reasons'],
                 'duration_s': e['duration_s']} for e in self.events()[:max(0, limit)]]

    def reset(self) -> None:
        with self._lock:
            self._samples.clear()
            self._events.clear()
            self._ok = None
            self._loaded = True

    # -- persistence -------------------------------------------------------

    def _ensure_loaded_locked(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        events = load_events(self.path, self._wall())
        if events and events[-1].kind == RESUMED:
            # The app stopped while imaging was OK; the feed starts over from
            # not-OK, so that state ended here.
            events.append(Event(self._wall(), PAUSED, (RESTARTED_REASON,)))
        self._events.extend(events[-MAX_EVENTS:])
        self._ok = False if events else None

    def _write(self, events: List[Event]) -> bool:
        path = self.path
        data = {'version': 1, 'events': [
            {'at': _iso(e.at), 'kind': e.kind, 'reasons': list(e.reasons)} for e in events]}
        tmp = None
        try:
            folder = os.path.dirname(path)
            os.makedirs(folder, exist_ok=True)
            with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=folder,
                                             prefix='.events.', suffix='.tmp',
                                             delete=False) as f:
                tmp = f.name
                json.dump(data, f)
            os.replace(tmp, path)
            return True
        except Exception as e:
            app_logger.warning(f"Imaging conditions: could not save the event list: {e}")
            if tmp and os.path.isfile(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass
            return False


def load_events(path: str, now: datetime) -> List[Event]:
    """Events newer than ``EVENT_MAX_AGE_S`` from ``path``, oldest first. A
    missing, oversized or garbled file reads as no events, never an error."""
    try:
        if not os.path.isfile(path) or os.path.getsize(path) > MAX_FILE_BYTES:
            return []
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
    except Exception as e:
        app_logger.debug(f"Imaging conditions: event list not loaded: {e}")
        return []
    rows = data.get('events') if isinstance(data, dict) else None
    if not isinstance(rows, list):
        return []
    cutoff = now - timedelta(seconds=EVENT_MAX_AGE_S)
    events = []
    for row in rows:
        if not isinstance(row, dict) or row.get('kind') not in (PAUSED, RESUMED):
            continue
        at = _parse(row.get('at'))
        reasons = row.get('reasons')
        if at is None or at < cutoff or at > now + timedelta(minutes=5):
            continue
        if not isinstance(reasons, list) or not all(isinstance(r, str) for r in reasons):
            reasons = []
        events.append(Event(at, row['kind'], tuple(r[:40] for r in reasons[:8])))
    events.sort(key=lambda e: e.at)
    return events[-MAX_EVENTS:]


_history: Optional[ConditionsHistory] = None
_history_lock = threading.Lock()


def get_conditions_history() -> ConditionsHistory:
    global _history
    with _history_lock:
        if _history is None:
            _history = ConditionsHistory()
        return _history


def reset_conditions_history() -> None:
    """Drop the singleton (tests)."""
    global _history
    with _history_lock:
        _history = None
