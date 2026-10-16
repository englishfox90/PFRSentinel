"""
Process-wide store of the target NINA last pushed to ``POST /nina/target``.

The HTTP request thread writes it (``services/web_target.py``) and the overlay
renderer reads it on the capture thread, so every access goes through one lock.
It holds a single target plus the monotonic time it was last received: the
plugin re-sends the same target as a heartbeat, and the renderer stops drawing
it once nothing has arrived for ``stale_after_s`` — a closed NINA must not leave
a marker on the sky all night. A monotonic clock (injected for tests) keeps
staleness immune to wall-clock steps such as a DST change or an NTP correction.

No Qt and no I/O: the target lives in memory only and is forgotten on restart,
which is what a heartbeat-fed value wants.
"""
from __future__ import annotations

import threading
import time
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class NinaTarget:
    """One imaging target. Coordinates are J2000, in degrees.

    ``rotation_deg`` is the sky position angle, degrees east of north, of the
    imaging camera's "up" (height axis); at 0 the width runs E-W.
    """

    name: str
    ra_deg: float
    dec_deg: float
    fov_w_deg: float | None = None
    fov_h_deg: float | None = None
    rotation_deg: float | None = None
    source: str = "nina"

    def as_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "NinaTarget":
        def _opt(key):
            value = d.get(key)
            return None if value is None else float(value)

        return cls(
            name=str(d["name"]),
            ra_deg=float(d["ra_deg"]),
            dec_deg=float(d["dec_deg"]),
            fov_w_deg=_opt("fov_w_deg"),
            fov_h_deg=_opt("fov_h_deg"),
            rotation_deg=_opt("rotation_deg"),
            source=str(d.get("source") or "nina"),
        )


class NinaTargetStore:
    """Thread-safe holder for the last pushed target and when it arrived."""

    def __init__(self, monotonic=time.monotonic):
        self._monotonic = monotonic
        self._lock = threading.Lock()
        self._target: NinaTarget | None = None
        self._received_at: float | None = None

    def set(self, target: NinaTarget | None) -> bool:
        """Store ``target`` (None clears). Returns whether the target changed.

        The receipt time is refreshed even when the target is identical — that
        is what a heartbeat is for.
        """
        now = self._monotonic()
        with self._lock:
            changed = target != self._target
            self._target = target
            self._received_at = now
        return changed

    def current(self, max_age_s: float) -> NinaTarget | None:
        """The stored target, or None if cleared or older than ``max_age_s``."""
        now = self._monotonic()
        with self._lock:
            target, received_at = self._target, self._received_at
        if target is None or received_at is None:
            return None
        if now - received_at > float(max_age_s):
            return None
        return target

    def snapshot(self) -> dict:
        """``{"target": dict | None, "age_s": float | None}`` — plain JSON values."""
        now = self._monotonic()
        with self._lock:
            target, received_at = self._target, self._received_at
        age = None if received_at is None else max(0.0, round(now - received_at, 3))
        return {
            "target": target.as_dict() if target is not None else None,
            "age_s": age,
        }

    def clear(self) -> None:
        with self._lock:
            self._target = None
            self._received_at = None


_store: NinaTargetStore | None = None
_store_lock = threading.Lock()


def get_nina_target_store() -> NinaTargetStore:
    global _store
    with _store_lock:
        if _store is None:
            _store = NinaTargetStore()
        return _store


def reset_nina_target_store() -> None:
    """Forget the singleton (tests). The next get builds a fresh store."""
    global _store
    with _store_lock:
        _store = None
