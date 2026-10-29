"""
ASCOM Alpaca request handling for one SafetyMonitor device (pure — no sockets).

Implements the Management API and ISafetyMonitorV3 as described at
https://ascom-standards.org/api. IsSafe is fail-safe: it reads False whenever
the device is disconnected or the verdict is stale.
"""
import itertools
import json
import platform
import threading
import uuid
from typing import Dict, Tuple

from version import __version__

from .state import SafetyState

INTERFACE_VERSION = 3
DEVICE_NAME = "PFR Sentinel Roof"
DEVICE_DESCRIPTION = "Roof safety from PFR Sentinel's confirmed ML roof verdict"
# Stable across restarts without storing anything: clients key saved profiles on it.
UNIQUE_ID = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"safetymonitor.pfrsentinel.{platform.node()}"))

NOT_IMPLEMENTED = 0x400

Response = Tuple[int, str, bytes]


class SafetyMonitorDevice:
    def __init__(self, state: SafetyState):
        self._state = state
        self._lock = threading.Lock()
        self._connected = False
        self._server_tx = itertools.count(1)

    @property
    def connected(self) -> bool:
        with self._lock:
            return self._connected

    def handle(self, method: str, path: str, params: Dict[str, str]) -> Response:
        """method is GET/PUT, path the URL path, params the merged query/form fields."""
        lower = {k.lower(): v for k, v in params.items()}
        parts = [p for p in path.lower().split('/') if p]

        if parts[:1] == ['management']:
            return self._management(method, parts[1:], lower)
        if len(parts) == 5 and parts[:2] == ['api', 'v1'] and parts[2] == 'safetymonitor':
            if parts[3] != '0':
                return _text(400, f"No SafetyMonitor device number {parts[3]}")
            return self._device(method, parts[4], lower)
        return _text(404, f"Unknown path {path}")

    def _management(self, method, parts, params) -> Response:
        if method != 'GET':
            return _text(405, "Management API is GET only")
        if parts == ['apiversions']:
            return self._ok(params, [1])
        if parts == ['v1', 'description']:
            return self._ok(params, {
                "ServerName": "PFR Sentinel",
                "Manufacturer": "PFR Sentinel",
                "ManufacturerVersion": __version__,
                "Location": platform.node(),
            })
        if parts == ['v1', 'configureddevices']:
            return self._ok(params, [{
                "DeviceName": DEVICE_NAME,
                "DeviceType": "SafetyMonitor",
                "DeviceNumber": 0,
                "UniqueID": UNIQUE_ID,
            }])
        return _text(404, "Unknown management endpoint")

    def _device(self, method, member, params) -> Response:
        if method == 'GET':
            getters = {
                'issafe': lambda: self.connected and self._state.is_safe(),
                'connected': lambda: self.connected,
                'connecting': lambda: False,
                'name': lambda: DEVICE_NAME,
                'description': lambda: DEVICE_DESCRIPTION,
                'driverinfo': lambda: f"PFR Sentinel {__version__} Alpaca SafetyMonitor",
                'driverversion': lambda: __version__,
                'interfaceversion': lambda: INTERFACE_VERSION,
                'supportedactions': lambda: [],
                'devicestate': self._device_state,
            }
            if member in getters:
                return self._ok(params, getters[member]())
            return self._error(params, NOT_IMPLEMENTED, f"{member} is not implemented")

        if method == 'PUT':
            if member == 'connected':
                value = params.get('connected', '').strip().lower()
                if value not in ('true', 'false'):
                    return _text(400, "Connected must be True or False")
                return self._set_connected(params, value == 'true')
            if member == 'connect':
                return self._set_connected(params, True)
            if member == 'disconnect':
                return self._set_connected(params, False)
            return self._error(params, NOT_IMPLEMENTED, f"{member} is not implemented")

        return _text(405, f"{method} is not supported")

    def _set_connected(self, params, value: bool) -> Response:
        with self._lock:
            self._connected = value
        return self._ok(params, None)

    def _device_state(self):
        return [
            {"Name": "IsSafe", "Value": self.connected and self._state.is_safe()},
            {"Name": "TimeStamp", "Value": self._state.changed_at().strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3]},
        ]

    def _ok(self, params, value) -> Response:
        return self._envelope(params, value, 0, "")

    def _error(self, params, number: int, message: str) -> Response:
        return self._envelope(params, None, number, message)

    def _envelope(self, params, value, number, message) -> Response:
        body = {
            "ClientTransactionID": _client_tx(params),
            "ServerTransactionID": next(self._server_tx),
            "ErrorNumber": number,
            "ErrorMessage": message,
        }
        if value is not None:
            body["Value"] = value
        return 200, 'application/json', json.dumps(body).encode('utf-8')


def _client_tx(params) -> int:
    try:
        value = int(params.get('clienttransactionid', 0))
    except (TypeError, ValueError):
        return 0
    return value if 0 <= value <= 0xFFFFFFFF else 0


def _text(status: int, message: str) -> Response:
    return status, 'text/plain; charset=utf-8', message.encode('utf-8')
