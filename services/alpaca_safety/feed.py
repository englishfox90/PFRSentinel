"""
Feeds each processed frame's roof verdict into the Alpaca safety state.

Runs its own RoofSafetyFSM rather than sharing the ASCOM file's: that FSM only
advances when its file write is durable, so with the file output off it would
never confirm anything. Same Policy A rules, one more sink.
"""
from typing import Any, Dict, Optional

from services.ascom_safety_fsm import RoofSafetyFSM, UNSAFE_RESULT
from services.logger import app_logger

from .state import SafetyState, safety_state, DEFAULT_STALE_AFTER_SECONDS

CONFIG_KEY = 'alpaca_safety_monitor'


class AlpacaSafetyFeed:
    def __init__(self, state: SafetyState = safety_state):
        self._state = state
        self._fsm: Optional[RoofSafetyFSM] = None

    def update(self, ml_results: Optional[Dict[str, Any]], ml_config: Dict[str, Any]) -> None:
        """ml_results is None when ML produced nothing usable this frame (fed as UNSAFE)."""
        cfg = ml_config.get(CONFIG_KEY, {})
        if not cfg.get('enabled', False):
            self._fsm = None
            return
        try:
            if self._fsm is None:
                # Frames went unseen while this was off, so a verdict confirmed
                # before then proves nothing: start over from UNSAFE.
                self._fsm = RoofSafetyFSM(writer=self._publish)
                self._state.publish(False)
            self._state.stale_after_seconds = cfg.get(
                'stale_after_seconds', DEFAULT_STALE_AFTER_SECONDS)
            self._fsm.update(ml_results if ml_results is not None else UNSAFE_RESULT,
                             {'min_confidence': cfg.get('min_confidence', 0.7),
                              'heartbeat_seconds': 0})
            self._state.note_frame()
        except Exception as e:
            app_logger.debug(f"Alpaca safety update skipped: {e}")

    def _publish(self, ml_results: Dict[str, Any], fsm_config: Dict[str, Any]) -> bool:
        from services.ascom_safety import is_safe_open
        safe = is_safe_open(ml_results, fsm_config.get('min_confidence', 0.7))
        if self._state.publish(safe):
            app_logger.info(f"Alpaca SafetyMonitor: now {'SAFE' if safe else 'UNSAFE'} "
                            f"(roof {ml_results.get('roof_status', 'N/A')})")
        return True
