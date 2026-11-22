"""Alpaca camera discovery and Test Connection, off the GUI thread (issue #43).

Each request runs on a daemon thread; the result comes back through a private
queued signal, so the re-entrancy flag is cleared and the public signal emitted on
the thread this controller lives on (the GUI thread). One discovery and one test
may be in flight at a time; a second request while one runs is ignored.

The discover / probe functions are injectable for tests. The probe module is
imported inside the worker, so this file imports even in a build without it.
"""
import threading
from dataclasses import replace

from PySide6.QtCore import QObject, Signal, Slot

from services.logger import app_logger
from services.camera.capabilities import ALPACA_STATIC

DISCOVERY_TIMEOUT_S = 2.0


def _default_discover(timeout):
    from services.camera.alpaca.alpaca_discovery import discover_cameras
    return discover_cameras(timeout)


def _default_probe(settings):
    from services.camera.alpaca.alpaca_probe import probe_alpaca_camera
    return probe_alpaca_camera(settings)


def _range(value):
    try:
        lo, hi = value
        return (lo, hi) if lo is not None and hi is not None and hi >= lo else None
    except (TypeError, ValueError):
        return None


def capabilities_from_probe(details: dict):
    """`ALPACA_STATIC` refined by a passed probe's details, for the settings display."""
    details = details or {}
    gain, offset = _range(details.get('gain_range')), _range(details.get('offset_range'))
    return replace(
        ALPACA_STATIC,
        bayer_pattern=details.get('bayer_pattern') or None,
        bit_depth=details.get('bit_depth') or None,
        gain_range=(int(gain[0]), int(gain[1])) if gain else None,
        offset_range=(int(offset[0]), int(offset[1])) if offset else None,
        # A probe that read the camera and found no range: the control is absent.
        gain='gain_range' not in details or gain is not None,
        offset='offset_range' not in details or offset is not None,
        exposure_range_s=_range(details.get('exposure_range_s')),
    )


class AlpacaDiscoveryController(QObject):
    devices_found = Signal(list, str)       # AlpacaDeviceInfo list, error ('' on success)
    test_result = Signal(bool, str, dict)   # ok, message, details

    # object, not list/dict: a queued dict is converted to a QVariantMap on the way.
    _discover_done = Signal(object, str)
    _test_done = Signal(bool, str, object)

    def __init__(self, parent=None, discover_fn=None, probe_fn=None,
                 timeout: float = DISCOVERY_TIMEOUT_S):
        super().__init__(parent)
        self._discover_fn = discover_fn or _default_discover
        self._probe_fn = probe_fn or _default_probe
        self._timeout = timeout
        self._discovering = False
        self._testing = False
        self._discover_done.connect(self._finish_discover)
        self._test_done.connect(self._finish_test)

    @property
    def discovering(self) -> bool:
        return self._discovering

    @property
    def testing(self) -> bool:
        return self._testing

    def discover(self) -> bool:
        """Start a discovery; False when one is already running."""
        if self._discovering:
            app_logger.debug("Alpaca discovery already running, ignoring the request")
            return False
        self._discovering = True
        threading.Thread(target=self._run_discover, name="alpaca-discover", daemon=True).start()
        return True

    def test_connection(self, settings: dict) -> bool:
        """Probe the camera `settings` name; False when a test is already running."""
        if self._testing:
            app_logger.debug("Alpaca connection test already running, ignoring the request")
            return False
        self._testing = True
        threading.Thread(target=self._run_test, args=(dict(settings),),
                         name="alpaca-test", daemon=True).start()
        return True

    def _run_discover(self):
        try:
            devices = list(self._discover_fn(self._timeout) or [])
            error = ""
        except Exception as e:
            app_logger.warning(f"Alpaca discovery failed: {e}")
            devices, error = [], str(e) or type(e).__name__
        app_logger.info(f"Alpaca discovery: {len(devices)} camera(s) found")
        self._discover_done.emit(devices, error)

    def _run_test(self, settings):
        try:
            result = self._probe_fn(settings)
            ok, message = bool(result.ok), str(result.message)
            details = dict(result.details or {})
        except ImportError:
            ok, message, details = False, "Alpaca camera support is not in this build", {}
        except Exception as e:
            ok, message, details = False, str(e) or type(e).__name__, {}
        where = f"{settings.get('host', '')}:{settings.get('port', '')} #{settings.get('device_number', 0)}"
        if ok:
            app_logger.info(f"Alpaca test {where}: {message}")
        else:
            app_logger.warning(f"Alpaca test {where} failed: {message}")
        self._test_done.emit(ok, message, details)

    @Slot(object, str)
    def _finish_discover(self, devices, error):
        self._discovering = False
        self.devices_found.emit(devices, error)

    @Slot(bool, str, object)
    def _finish_test(self, ok, message, details):
        self._testing = False
        self.test_result.emit(ok, message, details)
