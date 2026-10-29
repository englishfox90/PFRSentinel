"""
Starts, stops and restarts the Alpaca SafetyMonitor server to match config.

reconcile() is idempotent: call it at startup and after every settings save.
"""
import threading
from typing import Any, Dict, Optional

from services.logger import app_logger

from .feed import CONFIG_KEY
from .protocol import SafetyMonitorDevice
from .server import AlpacaSafetyServer
from .state import SafetyState, safety_state

DEFAULT_PORT = 11111


class AlpacaSafetyService:
    def __init__(self, state: SafetyState = safety_state, server_factory=AlpacaSafetyServer):
        self._state = state
        self._device = SafetyMonitorDevice(state)
        self._server_factory = server_factory
        self._server: Optional[AlpacaSafetyServer] = None
        self._lock = threading.Lock()

    @property
    def running(self) -> bool:
        return self._server is not None

    def reconcile(self, ml_config: Dict[str, Any]) -> None:
        cfg = ml_config.get(CONFIG_KEY, {}) if isinstance(ml_config, dict) else {}
        wanted = (cfg.get('bind', '0.0.0.0'), int(cfg.get('port', DEFAULT_PORT)),
                  bool(cfg.get('discovery', True)))
        with self._lock:
            if not cfg.get('enabled', False):
                self._stop_locked()
                return
            if self._server is not None and self._settings() == wanted:
                return
            self._stop_locked()
            server = self._server_factory(self._device, bind=wanted[0], port=wanted[1],
                                          discovery=wanted[2])
            if server.start():
                self._server = server
            else:
                app_logger.warning("Alpaca SafetyMonitor not started; clients will see it offline")

    def stop(self) -> None:
        with self._lock:
            self._stop_locked()

    def _settings(self):
        s = self._server
        return (s.bind, s.requested_port, s.discovery)

    def _stop_locked(self) -> None:
        if self._server is None:
            return
        try:
            self._server.stop()
        except Exception as e:
            app_logger.debug(f"Alpaca SafetyMonitor stop failed: {e}")
        self._server = None
        app_logger.info("Alpaca SafetyMonitor stopped")


alpaca_safety_service = AlpacaSafetyService()
