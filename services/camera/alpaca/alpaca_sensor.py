"""Sensor facts from an Alpaca camera: Bayer phase, flips, and bit depth.

Pure functions. The Bayer pattern is always derived from what the device
reports (SensorType, BayerOffsetX/Y) and the subframe origin — never assumed.
ASCOM defines BayerOffset relative to pixel (0, 0) of the full sensor, and it
does not change with the subframe, so StartX/StartY shift the phase.

Two vocabularies, and they are not the same string:

- *physical* (ASCOM / FITS BAYERPAT convention): the colours of the frame's
  top-left 2x2 tile read row by row, so offset (0, 0) on SensorType RGGB is
  ``"RGGB"``, (1, 0) ``"GRBG"``, (0, 1) ``"GBRG"``, (1, 1) ``"BGGR"``.
- *pipeline*: what ``camera_utils.debayer_raw_image``, ``BAYER_PATTERN`` and the
  camera profiles' ``bayer_pattern`` mean. That function maps the name straight
  onto OpenCV's ``COLOR_Bayer<XY>2RGB`` code, and OpenCV names a pattern from the
  second row's second and third pixels — the physical tile rotated 180°
  (measured: a physical RGGB mosaic of a red scene comes out blue through
  ``COLOR_BayerRG2RGB`` and red through ``COLOR_BayerBG2RGB``). So the pipeline
  name is the physical string reversed, which is why ZWO bodies whose top-left
  pixel is red are configured as "BGGR" here.

``bayer_pattern_for`` returns the pipeline name, so an Alpaca frame feeds the
same code path a ZWO frame of the same physical sensor does.
"""
import threading

import numpy as np

from services.logger import app_logger

from .alpaca_protocol import SensorType

MONO = "MONO"
RGB = "RGB"
BAYER_PATTERNS = ("RGGB", "GRBG", "GBRG", "BGGR")

# ZWO's ASI_FLIP values, which the shared code and profiles already use.
FLIP_NONE, FLIP_HORIZONTAL, FLIP_VERTICAL, FLIP_BOTH = 0, 1, 2, 3

_BASE_TILE = (("R", "G"), ("G", "B"))  # SensorType RGGB at offset (0, 0)

_warned_types = set()
_warned_lock = threading.Lock()


def _warn_once(sensor_type) -> None:
    with _warned_lock:
        if sensor_type in _warned_types:
            return
        _warned_types.add(sensor_type)
    try:
        label = SensorType(sensor_type).name
    except ValueError:
        label = f"unknown ({sensor_type})"
    app_logger.warning(
        f"Alpaca camera reports SensorType {label}; colour reconstruction for it is "
        f"not supported, so frames are treated as monochrome")


def _reset_warnings_for_tests() -> None:
    with _warned_lock:
        _warned_types.clear()


def _tile_pattern(shift_x: int, shift_y: int) -> str:
    return "".join(_BASE_TILE[(row + shift_y) % 2][(col + shift_x) % 2]
                   for row in (0, 1) for col in (0, 1))


def physical_bayer_pattern(sensor_type, offset_x=0, offset_y=0, start_x=0,
                           start_y=0) -> str:
    """The delivered frame's top-left 2x2 tile, row by row (ASCOM / FITS naming).

    Returns one of ``BAYER_PATTERNS``, ``'MONO'`` or ``'RGB'`` (SensorType Color:
    the device already delivers three planes). CMYG / CMYG2 / LRGB and unknown
    types read as ``'MONO'`` with a one-time warning.
    """
    try:
        st = int(sensor_type)
    except (TypeError, ValueError):
        st = -1
    if st == SensorType.MONOCHROME:
        return MONO
    if st == SensorType.COLOR:
        return RGB
    if st != SensorType.RGGB:
        _warn_once(st)
        return MONO
    shift_x = (int(offset_x or 0) + int(start_x or 0)) % 2
    shift_y = (int(offset_y or 0) + int(start_y or 0)) % 2
    return _tile_pattern(shift_x, shift_y)


def pipeline_pattern_name(physical: str) -> str:
    """Physical tile → the name ``debayer_raw_image`` needs (OpenCV's convention)."""
    return physical[::-1] if physical in BAYER_PATTERNS else physical


def bayer_pattern_for(sensor_type, offset_x=0, offset_y=0, start_x=0, start_y=0) -> str:
    """The pipeline ``bayer_pattern`` for this frame (see the module docstring).

    ``'MONO'`` / ``'RGB'`` pass through unchanged.
    """
    return pipeline_pattern_name(
        physical_bayer_pattern(sensor_type, offset_x, offset_y, start_x, start_y))


def flip_bayer_pattern(pattern: str, flip: int, width=None, height=None) -> str:
    """The pattern after a software flip of the mosaic (either vocabulary: a
    180° rotation commutes with both flips).

    A horizontal flip moves the last column to the front, so it swaps the
    column phase only when the width is even (an odd width keeps it); the same
    for a vertical flip and the height. Width/height default to even.
    """
    if pattern not in BAYER_PATTERNS:
        return pattern
    flip = int(flip or 0)
    swap_cols = flip in (FLIP_HORIZONTAL, FLIP_BOTH) and not (width and int(width) % 2)
    swap_rows = flip in (FLIP_VERTICAL, FLIP_BOTH) and not (height and int(height) % 2)
    tile = [[pattern[0], pattern[1]], [pattern[2], pattern[3]]]
    if swap_cols:
        tile = [[r[1], r[0]] for r in tile]
    if swap_rows:
        tile = [tile[1], tile[0]]
    return "".join(tile[0] + tile[1])


def apply_flip(arr: np.ndarray, flip: int) -> np.ndarray:
    """Flip an (H, W[, P]) array like ZWO's ASI_FLIP; contiguous result."""
    flip = int(flip or 0)
    if flip == FLIP_HORIZONTAL:
        arr = arr[:, ::-1]
    elif flip == FLIP_VERTICAL:
        arr = arr[::-1, :]
    elif flip == FLIP_BOTH:
        arr = arr[::-1, ::-1]
    return np.ascontiguousarray(arr)


def _valid_max_adu(max_adu) -> int:
    """MaxADU as the largest pixel value. A driver reporting 2**n (65536, 4096)
    means n bits — the count of levels, not the top one — so it reads as
    2**n - 1; taken literally 65536 would be 17-bit and halve every value."""
    try:
        value = int(max_adu)
    except (TypeError, ValueError):
        return 65535
    if value <= 0:
        return 65535
    if value >= 256 and value & (value - 1) == 0:
        return value - 1
    return value


def effective_max_adu(max_adu) -> int:
    """The largest pixel value MaxADU really allows (see ``_valid_max_adu``)."""
    return _valid_max_adu(max_adu)


def sensor_bit_depth(max_adu) -> int:
    """Bits the sensor actually delivers (MaxADU 4095 → 12)."""
    return _valid_max_adu(max_adu).bit_length()


def transfer_bit_depth(max_adu) -> int:
    """8 when every value fits a byte, else 16 (the RAW8 / RAW16 analogue)."""
    return 8 if _valid_max_adu(max_adu) <= 255 else 16


def to_raw_array(arr: np.ndarray, max_adu, *, inplace: bool = False) -> tuple:
    """Normalise device pixels to ZWO's raw layout: ``(array, bit_depth)``.

    MaxADU <= 255 → uint8, values clipped. Otherwise uint16, clipped to
    [0, MaxADU] and MSB-aligned (shifted left by ``16 - bits``), which is how
    ZWO delivers RAW16, so ``debayer_raw_image``'s ``>> 8`` and the 16-bit
    histogram stay correct. A MaxADU above 16 bits is shifted right instead.
    An unknown/invalid MaxADU reads as 65535 (no shift).

    Clipping happens in the input's own dtype and the shift in the output's,
    so the only allocation is the output (none for a uint16 input with
    ``inplace``). ``inplace=True`` lets it overwrite ``arr`` — for a frame
    nobody else holds, as the capture path's freshly decoded one.
    """
    max_adu = _valid_max_adu(max_adu)
    data = np.asarray(arr)
    owned = inplace and data.flags.writeable
    if data.dtype.kind == "f":
        data = np.rint(data, out=data if owned else None)
        owned = True
    if max_adu <= 255:
        out_dtype, depth, high, shift = np.uint8, 8, 255, 0
    else:
        out_dtype, depth, high = np.uint16, 16, max_adu
        shift = 16 - max_adu.bit_length()
    if data.dtype.kind in "iu":
        high = min(high, int(np.iinfo(data.dtype).max))
    data = np.clip(data, 0, high, out=data if owned else None)
    if shift < 0:
        if data.dtype.kind == "f":
            data = data.astype(np.int64)
        np.right_shift(data, -shift, out=data)
        shift = 0
    out = data.astype(out_dtype, copy=False)
    if shift > 0:
        np.left_shift(out, shift, out=out)
    return out, depth


def to_raw_bytes(arr: np.ndarray, max_adu) -> tuple:
    """``to_raw_array`` as row-major little-endian bytes: ``(bytes, bit_depth)``."""
    raw, depth = to_raw_array(arr, max_adu)
    if depth == 16:
        raw = raw.astype("<u2", copy=False)
    return np.ascontiguousarray(raw).tobytes(), depth
