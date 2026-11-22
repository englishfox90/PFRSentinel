"""Alpaca wire encodings for the simulator: JSON envelopes, ImageBytes, JSON ImageArray.

Written from the ASCOM Alpaca API and the ``alpyca`` client's decoder, not
from ``services/camera/alpaca`` — so a round trip through the client checks
two independent implementations against each other.

The Alpaca array is ``[x][y]`` (``[x][y][plane]``); ImageBytes serialises it
with the last index fastest, JSON as nested lists ``Value[x][y]``.
"""
import json
import struct

import numpy as np

JSON_MIME = "application/json"
IMAGEBYTES_MIME = "application/imagebytes"

EL_INT32, EL_BYTE, EL_UINT16, EL_INT16, EL_DOUBLE = 2, 6, 8, 1, 3
_DTYPES = {1: "<i2", 2: "<i4", 3: "<f8", 4: "<f4", 5: "<u8", 6: "u1", 7: "<i8",
           8: "<u2", 9: "<u4"}


def envelope(value, client_tx: int, server_tx: int, error_number: int = 0,
             error_message: str = "", has_value: bool = True) -> dict:
    """A ValueResponse (GET) or, with ``has_value=False``, a MethodResponse (PUT)."""
    body = {}
    if has_value and error_number == 0:
        body["Value"] = value
    body.update({"ClientTransactionID": int(client_tx), "ServerTransactionID": int(server_tx),
                 "ErrorNumber": int(error_number), "ErrorMessage": error_message})
    return body


def json_bytes(obj) -> bytes:
    return json.dumps(obj, separators=(",", ":")).encode("utf-8")


def _alpaca_order(image: np.ndarray) -> np.ndarray:
    """(H, W[, P]) → the [x][y][, p] array."""
    return image.T if image.ndim == 2 else image.transpose(1, 0, 2)


def pick_transmission_type(image: np.ndarray) -> int:
    if image.dtype.kind == "f":
        return EL_DOUBLE
    lo, hi = (int(image.min()), int(image.max())) if image.size else (0, 0)
    if lo >= 0 and hi <= 255:
        return EL_BYTE
    if lo >= 0 and hi <= 65535:
        return EL_UINT16
    if lo >= -32768 and hi <= 32767:
        return EL_INT16
    return EL_INT32


def encode_imagebytes(image: np.ndarray, *, client_tx: int = 0, server_tx: int = 0,
                      transmission_type=None, image_type: int = EL_INT32) -> bytes:
    """``application/imagebytes`` body for an (H, W) or (H, W, P) image."""
    image = np.asarray(image)
    if image.ndim not in (2, 3):
        raise ValueError("ImageBytes carries rank 2 or rank 3 arrays only")
    ttype = pick_transmission_type(image) if transmission_type is None else int(transmission_type)
    xy = _alpaca_order(image)
    dims = list(xy.shape) + [0] * (3 - xy.ndim)
    header = struct.pack("<11i", 1, 0, int(client_tx), int(server_tx), 44, int(image_type),
                         ttype, xy.ndim, *dims)
    data = np.ascontiguousarray(xy).astype(_DTYPES[ttype], copy=False).tobytes()
    return header + data


def encode_imagebytes_error(error_number: int, message: str, *, client_tx: int = 0,
                            server_tx: int = 0) -> bytes:
    header = struct.pack("<11i", 1, int(error_number), int(client_tx), int(server_tx), 44,
                         0, 0, 0, 0, 0, 0)
    return header + message.encode("utf-8")


def imagearray_json(image: np.ndarray, client_tx: int, server_tx: int) -> dict:
    image = np.asarray(image)
    body = {"Type": EL_DOUBLE if image.dtype.kind == "f" else EL_INT32, "Rank": image.ndim}
    body.update(envelope(_alpaca_order(image).tolist(), client_tx, server_tx))
    return body
