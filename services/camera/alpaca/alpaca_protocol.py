"""ASCOM Alpaca protocol constants and the exceptions the Alpaca backend raises.

Pure: no I/O. Error numbers, CameraState and SensorType follow the ASCOM
Alpaca API / ICameraV3-V4 (the same values the official ``alpyca`` client
uses in ``alpaca/exceptions.py`` and ``alpaca/camera.py``).

Exception mapping is chosen so the shared capture loop needs no Alpaca
knowledge: anything that means "the device went away" (NotConnected, a
socket/HTTP failure, a malformed reply) is a ``CameraDisconnectedError``, which
the loop answers with a reconnect.
"""
from enum import IntEnum
from types import SimpleNamespace
from typing import Optional

from ..errors import CameraDisconnectedError, CameraError, CameraTimeoutError

DEFAULT_PORT = 11111
DISCOVERY_PORT = 32227
DISCOVERY_MESSAGE = b"alpacadiscovery1"
API_VERSION = 1

# Alpaca error numbers (ErrorNumber in the JSON envelope / ImageBytes header).
NOT_IMPLEMENTED = 0x400
INVALID_VALUE = 0x401
VALUE_NOT_SET = 0x402
NOT_CONNECTED = 0x407
PARKED = 0x408
SLAVED = 0x409
INVALID_OPERATION = 0x40B
ACTION_NOT_IMPLEMENTED = 0x40C
OPERATION_CANCELLED = 0x40E
DRIVER_ERROR_MIN = 0x500
DRIVER_ERROR_MAX = 0xFFF


class CameraState(IntEnum):
    IDLE = 0
    WAITING = 1
    EXPOSING = 2
    READING = 3
    DOWNLOAD = 4
    ERROR = 5


class SensorType(IntEnum):
    MONOCHROME = 0
    COLOR = 1       # full RGB per pixel: ImageArray is Rank 3
    RGGB = 2        # Bayer; the phase comes from BayerOffsetX/Y
    CMYG = 3
    CMYG2 = 4
    LRGB = 5


class ImageElementType(IntEnum):
    UNKNOWN = 0
    INT16 = 1
    INT32 = 2
    DOUBLE = 3
    SINGLE = 4
    UINT64 = 5
    BYTE = 6
    INT64 = 7
    UINT16 = 8
    UINT32 = 9


class AlpacaError(CameraError):
    """A device-level Alpaca error (non-zero ErrorNumber, or an HTTP 4xx)."""

    def __init__(self, number: Optional[int], message: str = ""):
        self.number = number
        self.message = message or ""
        label = f"0x{number:X}" if isinstance(number, int) else "Alpaca"
        super().__init__(f"{label}: {self.message}" if self.message else label)


class AlpacaNotImplementedError(AlpacaError):
    """0x400: an optional member the device does not implement."""


class AlpacaInvalidValueError(AlpacaError):
    """0x401 (or HTTP 400): the device rejected a parameter value."""


class AlpacaInvalidOperationError(AlpacaError):
    """0x40B: valid request, wrong moment (e.g. ImageArray before an image)."""


class AlpacaCancelledError(AlpacaError):
    """0x40E: the operation was cancelled (also a locally aborted download)."""


class AlpacaNotConnectedError(AlpacaError, CameraDisconnectedError):
    """0x407: the driver is not connected to the camera."""


class AlpacaTransportError(CameraDisconnectedError):
    """The server could not be reached, or answered with something unusable."""


class AlpacaTimeoutError(AlpacaTransportError, CameraTimeoutError):
    """An HTTP request or image download exceeded its time bound."""


class AlpacaProtocolError(AlpacaTransportError):
    """A reply that does not follow the Alpaca wire format (bad JSON, header)."""


class AlpacaImageBytesError(AlpacaProtocolError):
    """An ImageBytes payload that cannot be decoded; the same image may still
    download as JSON (INDIGO 2.0 sends it 44 bytes short)."""


_ERROR_CLASSES = {
    NOT_IMPLEMENTED: AlpacaNotImplementedError,
    ACTION_NOT_IMPLEMENTED: AlpacaNotImplementedError,
    INVALID_VALUE: AlpacaInvalidValueError,
    INVALID_OPERATION: AlpacaInvalidOperationError,
    NOT_CONNECTED: AlpacaNotConnectedError,
    OPERATION_CANCELLED: AlpacaCancelledError,
}


def error_for(number: int, message: str = "") -> AlpacaError:
    """The exception instance for a non-zero Alpaca ErrorNumber."""
    cls = _ERROR_CLASSES.get(number, AlpacaError)
    return cls(number, message)


def raise_for_error(number, message: str = "") -> None:
    """Raise the mapped exception when ``number`` is a non-zero error."""
    try:
        n = int(number or 0)
    except (TypeError, ValueError):
        raise AlpacaProtocolError(f"Unreadable ErrorNumber {number!r}") from None
    if n != 0:
        raise error_for(n, str(message or ""))


# The zwoasi constant subset the shared capture loop and CameraCalibration
# read off ``session.asi``. Values are zwoasi's own (checked against the
# installed zwoasi), so the Alpaca facade is a drop-in for that code.
ALPACA_SDK = SimpleNamespace(
    ASI_EXP_IDLE=0,
    ASI_EXP_WORKING=1,
    ASI_EXP_SUCCESS=2,
    ASI_EXP_FAILED=3,
    ASI_GAIN=0,
    ASI_EXPOSURE=1,
    ASI_BRIGHTNESS=5,
    ASI_TEMPERATURE=8,
    ASI_FLIP=9,
    ASI_IMG_RAW8=0,
    ASI_IMG_RAW16=2,
)
