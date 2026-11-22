"""Decode an Alpaca ImageArray: the ImageBytes binary format and the JSON form.

Pure: bytes / parsed JSON in, numpy out. Both decoders return the image in
numpy's natural orientation, ``(height, width)`` for Rank 2 and
``(height, width, planes)`` for Rank 3, C-contiguous.

Alpaca's array is indexed ``[x][y]`` (``[x][y][plane]`` for Rank 3). ImageBytes
serialises it with the LAST index varying fastest, so for Rank 2 the stream is
``a[0][0], a[0][1], … a[0][Dim2-1], a[1][0], …`` — y fastest, Dimension1 is the
width. This matches the official ``alpyca`` decoder
(``Camera._build_imagedata_nested_list_array``: element ``[i][j]`` is
``a[i*Dim2 + j]`` with ``i < Dimension1``) and its JSON path, where
``Value[x][y]`` is a list of columns.

ImageBytes header: 44 bytes, 11 little-endian int32 —
MetadataVersion(1), ErrorNumber, ClientTransactionID, ServerTransactionID,
DataStart, ImageElementType, TransmissionElementType, Rank, Dimension1,
Dimension2, Dimension3. When ErrorNumber is non-zero the bytes from DataStart
are a UTF-8 error message instead of pixels.
"""
import struct
from dataclasses import dataclass

import numpy as np

from .alpaca_protocol import (
    AlpacaProtocolError,
    ImageElementType,
    error_for,
    raise_for_error,
)

IMAGEBYTES_MIME = "application/imagebytes"
HEADER = struct.Struct("<11i")
HEADER_SIZE = HEADER.size  # 44
METADATA_VERSION = 1

# Little-endian wire dtypes per TransmissionElementType.
TRANSMISSION_DTYPES = {
    ImageElementType.INT16: np.dtype("<i2"),
    ImageElementType.INT32: np.dtype("<i4"),
    ImageElementType.DOUBLE: np.dtype("<f8"),
    ImageElementType.SINGLE: np.dtype("<f4"),
    ImageElementType.UINT64: np.dtype("<u8"),
    ImageElementType.BYTE: np.dtype("u1"),
    ImageElementType.INT64: np.dtype("<i8"),
    ImageElementType.UINT16: np.dtype("<u2"),
    ImageElementType.UINT32: np.dtype("<u4"),
}

# Guard against a hostile or broken header asking for a huge allocation:
# 100 Mpx x 3 planes is far beyond any all-sky sensor.
MAX_ELEMENTS = 300_000_000


@dataclass(frozen=True)
class ImageBytesHeader:
    metadata_version: int
    error_number: int
    client_transaction_id: int
    server_transaction_id: int
    data_start: int
    image_element_type: int
    transmission_element_type: int
    rank: int
    dimension1: int
    dimension2: int
    dimension3: int


def error_text(data) -> str:
    """A device error message as printable text (never raw bytes in a log line)."""
    text = bytes(data).decode("utf-8", errors="replace")
    return "".join(ch if ch.isprintable() and ch != "\ufffd" else "?"
                   for ch in text).strip()[:200]


def parse_header(payload: bytes) -> ImageBytesHeader:
    if len(payload) < HEADER_SIZE:
        raise AlpacaProtocolError(
            f"ImageBytes payload is {len(payload)} bytes, shorter than the "
            f"{HEADER_SIZE}-byte header")
    return ImageBytesHeader(*HEADER.unpack_from(payload, 0))


def _check_shape(rank: int, dims) -> tuple:
    if rank not in (2, 3):
        raise AlpacaProtocolError(f"Unsupported ImageArray rank {rank}")
    shape = tuple(int(d) for d in dims[:rank])
    if any(d <= 0 for d in shape):
        raise AlpacaProtocolError(f"Invalid ImageArray dimensions {shape}")
    count = 1
    for d in shape:
        count *= d
    if count > MAX_ELEMENTS:
        raise AlpacaProtocolError(f"ImageArray of {count} elements is implausibly large")
    return shape


def _to_numpy_orientation(arr: np.ndarray, rank: int) -> np.ndarray:
    """[x][y](…[plane]) → (H, W[, planes]): one copy, C order, owns its data.

    Always copied (never a view on the download buffer), so callers may modify
    it in place whatever the frame shape.
    """
    if rank == 2:
        return np.array(arr.T, order="C")
    return np.array(arr.transpose(1, 0, 2), order="C")


def parse_imagebytes(payload: bytes) -> np.ndarray:
    """Decode an ``application/imagebytes`` body to ``(H, W)`` / ``(H, W, P)``.

    Raises the mapped Alpaca error when the header carries an ErrorNumber, and
    ``AlpacaProtocolError`` for a malformed or truncated payload.
    """
    header = parse_header(payload)
    if header.metadata_version != METADATA_VERSION:
        raise AlpacaProtocolError(
            f"Unsupported ImageBytes MetadataVersion {header.metadata_version}")
    start_ok = HEADER_SIZE <= header.data_start <= len(payload)
    if header.error_number != 0:
        # A device error wins over a malformed DataStart: report what the
        # driver said, read from the header's end when DataStart is unusable.
        start = header.data_start if start_ok else HEADER_SIZE
        raise error_for(header.error_number, error_text(payload[start:start + 1024]))
    if not start_ok:
        raise AlpacaProtocolError(f"Invalid ImageBytes DataStart {header.data_start}")

    try:
        dtype = TRANSMISSION_DTYPES[ImageElementType(header.transmission_element_type)]
    except (ValueError, KeyError):
        raise AlpacaProtocolError(
            f"Unknown ImageBytes TransmissionElementType "
            f"{header.transmission_element_type}") from None

    shape = _check_shape(header.rank, (header.dimension1, header.dimension2,
                                       header.dimension3))
    count = 1
    for d in shape:
        count *= d
    needed = count * dtype.itemsize
    available = len(payload) - header.data_start
    if available < needed:
        raise AlpacaProtocolError(
            f"ImageBytes payload truncated: {available} data bytes, {needed} expected")
    flat = np.frombuffer(payload, dtype=dtype, count=count, offset=header.data_start)
    return _to_numpy_orientation(flat.reshape(shape), header.rank)


def _ci_get(obj: dict, key: str, default=None):
    for k, v in obj.items():
        if isinstance(k, str) and k.lower() == key.lower():
            return v
    return default


def parse_imagearray_json(obj) -> np.ndarray:
    """Decode a JSON ImageArray reply (``{"Type", "Rank", "Value": [[…]]}``).

    ``Value`` is indexed ``[x][y]``; the result has the same orientation and
    values as ``parse_imagebytes`` on the same image.
    """
    if not isinstance(obj, dict):
        raise AlpacaProtocolError("ImageArray JSON reply is not an object")
    raise_for_error(_ci_get(obj, "ErrorNumber", 0), _ci_get(obj, "ErrorMessage", ""))
    value = _ci_get(obj, "Value")
    if not isinstance(value, list) or not value:
        raise AlpacaProtocolError("ImageArray JSON reply has no Value array")
    element_type = _ci_get(obj, "Type", ImageElementType.INT32)
    dtype = np.float64 if element_type in (ImageElementType.DOUBLE,
                                           ImageElementType.SINGLE) else np.int64
    try:
        arr = np.asarray(value, dtype=dtype)
    except (TypeError, ValueError):
        raise AlpacaProtocolError("ImageArray JSON Value is ragged or non-numeric") from None
    rank = _ci_get(obj, "Rank", arr.ndim)
    if arr.ndim != rank:
        raise AlpacaProtocolError(
            f"ImageArray JSON Rank {rank} does not match its {arr.ndim}-d Value")
    _check_shape(int(rank), arr.shape + (0,))
    if dtype is np.int64 and arr.size and arr.min() >= -(2 ** 31) and arr.max() < 2 ** 31:
        arr = arr.astype(np.int32)
    return _to_numpy_orientation(arr, int(rank))
