"""
Which verdict the Alpaca SafetyMonitor's IsSafe reports.

``roof`` (the default) is the confirmed roof verdict in ``SafetyState``.
``imaging_conditions`` is the imaging-conditions grade (``imaging_ok`` from
``services/imaging_conditions``): roof open AND a sky worth imaging, with that
module's own hysteresis and staleness. Both read unsafe when their source has
had no frame for too long or is switched off, so neither can leave a client on
a stale "safe".

Read at request time, never cached: a settings change takes effect on the
client's next poll.
"""
import threading
from datetime import datetime
from typing import Callable, Optional, Tuple

from .state import SafetyState

ROOF = 'roof'
IMAGING_CONDITIONS = 'imaging_conditions'
SOURCES = (ROOF, IMAGING_CONDITIONS)
CONFIG_FIELD = 'verdict_source'


def read_source(cfg) -> str:
    """The configured source; anything unrecognised reads as ``roof``."""
    value = cfg.get(CONFIG_FIELD, ROOF) if isinstance(cfg, dict) else ROOF
    value = str(value or '').strip().lower()
    return value if value in SOURCES else ROOF


def _conditions_verdict() -> Tuple[bool, Optional[datetime]]:
    from services.imaging_conditions import get_conditions_store
    return get_conditions_store().verdict()


class SafetyVerdict:
    def __init__(self, state: SafetyState,
                 conditions_fn: Callable[[], Tuple[bool, Optional[datetime]]] = _conditions_verdict):
        self._state = state
        self._conditions_fn = conditions_fn
        self._lock = threading.Lock()
        self._source = ROOF

    @property
    def source(self) -> str:
        with self._lock:
            return self._source

    def set_source(self, source: str) -> bool:
        """Returns True when the source changed."""
        source = source if source in SOURCES else ROOF
        with self._lock:
            changed = source != self._source
            self._source = source
            return changed

    def read(self) -> Tuple[bool, datetime]:
        """``(safe, changed_at)``. A failing conditions store reads unsafe."""
        if self.source == IMAGING_CONDITIONS:
            try:
                ok, changed_at = self._conditions_fn()
            except Exception:
                return False, self._state.changed_at()
            return bool(ok), changed_at or self._state.changed_at()
        return self._state.is_safe(), self._state.changed_at()

    def is_safe(self) -> bool:
        return self.read()[0]
