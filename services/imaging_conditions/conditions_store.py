"""
Process-wide store of the latest imaging-conditions evaluation.

The image pipeline's worker thread (camera mode) or the watcher thread
(Directory Watch) publishes one record per frame; the HTTP request thread
and the GUI's refresh timer read it. Every access goes through one lock.
Staleness is decided at read time on a monotonic clock (injected for
tests), so a stalled capture reads ``imaging_ok: false`` with reason
``stale`` without anyone having to publish that — the same rule the Alpaca
SafetyMonitor's state follows.

``events`` (pauses and resumes, newest first) is read from the conditions
history at snapshot time, so an ongoing state has no duration yet.

``snapshot()`` returns the ``GET /conditions`` contract exactly; see
``services/api_conditions.py`` for the field catalog the docs are built from.
"""
from __future__ import annotations

import copy
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

from services.config_defaults import DEFAULT_CONFIG

from .settings import AUTO_STALE_MIN_S, CONFIG_KEY

STALE = 'stale'
WARMING_UP = 'warming_up'
DISABLED = 'disabled'


def _iso(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    return value.astimezone(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')


def empty_record() -> dict:
    return {
        'imaging_ok': False,
        'reasons': [WARMING_UP],
        'frame_ok': None,
        'changed_at': None,
        'frames_considered': 0,
        'bad_streak': 0,
        'good_streak': 0,
        'roof': {'state': 'Unknown', 'confidence': None},
        'sky': {'condition': None, 'confidence': None, 'stars_visible': None},
        'stars': {'count': None, 'baseline': None, 'ratio': None, 'trend': None,
                  'change_pct': None, 'window_frames': None},
        'cloud_cover_pct': None,
        'cloud_cover_source': None,
        'fwhm': {'px': None, 'arcsec': None, 'night_median_px': None, 'change_pct': None},
        'sun_altitude_deg': None,
        'moon': {'up': None, 'illumination_pct': None},
        'baseline_bin': None,
        'baseline_status': {'state': 'learning', 'samples': 0,
                            'needed': int(DEFAULT_CONFIG[CONFIG_KEY]['baseline_min_samples']),
                            'bin': None},
    }


def _history_events() -> list:
    from .conditions_history import get_conditions_history
    return get_conditions_history().events_payload()


class ConditionsStore:
    def __init__(self, clock: Callable[[], float] = time.monotonic,
                 events_fn: Optional[Callable[[], list]] = None):
        self._lock = threading.Lock()
        self._clock = clock
        self._events_fn = events_fn if events_fn is not None else _history_events
        self._enabled = True
        self._record = empty_record()
        self._updated_at: Optional[datetime] = None
        self._last_frame: Optional[float] = None
        self._stale_after_s = AUTO_STALE_MIN_S

    def set_enabled(self, enabled: bool) -> None:
        with self._lock:
            if bool(enabled) == self._enabled:
                return
            self._enabled = bool(enabled)
            # Frames went unjudged while off: nothing earlier stands.
            self._record = empty_record()
            self._updated_at = None
            self._last_frame = None

    def enabled(self) -> bool:
        with self._lock:
            return self._enabled

    def publish(self, record: dict, *, updated_at: datetime, stale_after_s: float) -> None:
        with self._lock:
            self._enabled = True
            self._record = copy.deepcopy(record)
            self._updated_at = updated_at
            self._last_frame = self._clock()
            self._stale_after_s = float(stale_after_s)

    def last_frame_age_s(self) -> Optional[float]:
        with self._lock:
            return None if self._last_frame is None else self._clock() - self._last_frame

    def snapshot(self) -> dict:
        """The ``GET /conditions`` payload."""
        with self._lock:
            record = copy.deepcopy(self._record)
            enabled = self._enabled
            updated_at = self._updated_at
            age = None if self._last_frame is None else max(0.0, self._clock() - self._last_frame)
            stale_after = self._stale_after_s
        changed_at = record.pop('changed_at', None)
        payload = {
            'enabled': enabled,
            'updated_at': _iso(updated_at),
            'age_s': None if age is None else round(age, 1),
            'stale': False,
            'stale_after_s': round(stale_after, 1),
        }
        if not enabled:
            record = empty_record()
            record['reasons'] = [DISABLED]
            changed_at = None
            payload.update(updated_at=None, age_s=None)
        elif age is not None and age > stale_after:
            payload['stale'] = True
            if record.get('imaging_ok') and updated_at is not None:
                changed_at = updated_at + timedelta(seconds=stale_after)
            record['imaging_ok'] = False
            record['reasons'] = [STALE]
        payload['imaging_ok'] = bool(record.pop('imaging_ok'))
        payload['reasons'] = list(record.pop('reasons'))
        payload['changed_at'] = _iso(changed_at)
        payload.update(record)
        payload['events'] = self._events() if enabled else []
        return payload

    def _events(self) -> list:
        try:
            return list(self._events_fn() or [])
        except Exception:
            return []

    def compact(self) -> dict:
        """The ``conditions`` block of ``/status``."""
        snap = self.snapshot()
        return {key: snap[key] for key in ('imaging_ok', 'reasons', 'changed_at', 'stale')}

    def reset(self) -> None:
        with self._lock:
            self._enabled = True
            self._record = empty_record()
            self._updated_at = None
            self._last_frame = None
            self._stale_after_s = AUTO_STALE_MIN_S


_store: Optional[ConditionsStore] = None
_store_lock = threading.Lock()


def get_conditions_store() -> ConditionsStore:
    global _store
    with _store_lock:
        if _store is None:
            _store = ConditionsStore()
        return _store


def reset_conditions_store() -> None:
    """Replace the singleton (tests)."""
    global _store
    with _store_lock:
        _store = None
