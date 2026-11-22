"""ASCOM Alpaca camera backend (issue #43; design in docs/ALPACA_CAMERA_PLAN.md).

No Qt in this package: the controllers run it on their own threads.
"""
from .alpaca_discovery import AlpacaDeviceInfo, cameras_on_server, discover_cameras, discover_servers
from .alpaca_http import AlpacaHttpClient
from .alpaca_protocol import (
    ALPACA_SDK,
    AlpacaError,
    AlpacaNotConnectedError,
    AlpacaNotImplementedError,
    AlpacaTransportError,
    CameraState,
    SensorType,
)

__all__ = [
    "ALPACA_SDK",
    "AlpacaDeviceInfo",
    "AlpacaError",
    "AlpacaHttpClient",
    "AlpacaNotConnectedError",
    "AlpacaNotImplementedError",
    "AlpacaTransportError",
    "CameraState",
    "SensorType",
    "cameras_on_server",
    "discover_cameras",
    "discover_servers",
]
