"""Readout mode and pixel range for a server whose MaxADU is not what it sends.

INDIGO's Alpaca bridge reports ``MaxADU = 4096`` for an ASI676MC in every
readout mode, yet its default mode "RAW 8 3552x3552" delivers 0-255 and
"RAW 16 3552x3552" delivers the 12-bit data MSB-aligned (0-65520). Trusting
MaxADU makes the first 16x too dark and clips 15/16 of the second.

Two remedies, both per session:

- ``preferred_readout_mode`` moves an 8-bit mode to the 16-bit mode of the same
  kind and size when the server names its modes that way. Names that carry no
  bit depth (ASCOM's "Normal" / "Fast") are never touched.
- ``adopt_pixel_range`` widens the ceiling to 65535 the first time a frame
  arrives above MaxADU, instead of clipping it. The widened ceiling is kept in
  ``SessionWarnings.state`` so it outlives reconnects and a later dark frame
  (all below MaxADU) is not scaled differently from a bright one.
"""
import re
from typing import Optional, Sequence

from .alpaca_sensor import effective_max_adu

ADOPTED_MAX_ADU = "adopted_max_adu"
SIXTEEN_BIT_MAX = 65535

_DEPTH = re.compile(r"(?i)(?:\b(?:raw|y|mono|l|gray|grey)\s*-?\s*(8|16)\b|\b(8|16)\s*-?\s*bits?\b)")
_SIZE = re.compile(r"(\d+)\s*[x×]\s*(\d+)")
_KIND = re.compile(r"(?i)^\s*([a-z]+)")


def readout_bit_depth(name) -> Optional[int]:
    """8 or 16 when a readout mode's name says so ("RAW 8", "RAW16", "16-bit"), else None."""
    match = _DEPTH.search(str(name or ""))
    if not match:
        return None
    return int(match.group(1) or match.group(2))


def _kind(name: str) -> str:
    match = _KIND.match(name)
    return match.group(1).lower() if match else ""


def _size(name: str):
    match = _SIZE.search(name)
    return (int(match.group(1)), int(match.group(2))) if match else None


def preferred_readout_mode(names: Sequence, current) -> Optional[int]:
    """The 16-bit mode to switch to from an 8-bit ``current``, or None to leave it.

    Only an 8-bit mode moves, only to a 16-bit mode of the same kind ("RAW",
    "Y") and the same frame size, so binning and colour handling never change.
    """
    if not isinstance(names, (list, tuple)):
        return None
    names = [str(n) for n in names]
    try:
        current = int(current)
    except (TypeError, ValueError):
        return None
    if not 0 <= current < len(names) or readout_bit_depth(names[current]) != 8:
        return None
    kind, size = _kind(names[current]), _size(names[current])
    for index, name in enumerate(names):
        if readout_bit_depth(name) == 16 and _kind(name) == kind and _size(name) == size:
            return index
    return None


def pixel_ceiling(max_adu, state: dict) -> int:
    """The value the session scales and clips frames to: an adopted 65535, else MaxADU."""
    adopted = state.get(ADOPTED_MAX_ADU)
    return adopted if adopted is not None else effective_max_adu(max_adu)


def adopt_pixel_range(image_max, max_adu, state: dict, warnings) -> int:
    """Widen the session ceiling when a frame arrives above it; return the ceiling.

    A frame above MaxADU but within 16 bits means the driver's MaxADU is not
    the range it sends: the session adopts 65535 (data used as delivered,
    no shift) and says so once. Values beyond 16 bits are still clipped.
    """
    top = pixel_ceiling(max_adu, state)
    try:
        peak = int(image_max)
    except (TypeError, ValueError):
        return top
    if peak <= top:
        return top
    # A camera that claims 8 bits keeps its 8-bit transfer depth (RAW8 frames,
    # calibration buffers): widening it would change the array type under them.
    if peak <= SIXTEEN_BIT_MAX and effective_max_adu(max_adu) > 255:
        state[ADOPTED_MAX_ADU] = SIXTEEN_BIT_MAX
        warnings.once("max-adu",
                      f"Alpaca camera delivered pixels above its MaxADU {max_adu} (max {peak}): "
                      f"the driver's MaxADU is wrong. Using the full 16-bit range for this session")
        return SIXTEEN_BIT_MAX
    warnings.once("max-adu", f"Alpaca camera delivered pixels above its MaxADU {max_adu} "
                             f"(max {peak}): the driver's MaxADU is wrong or its data is not "
                             f"right-aligned; values above {top} are clipped")
    return top


__all__ = ["readout_bit_depth", "preferred_readout_mode", "pixel_ceiling",
           "adopt_pixel_range", "ADOPTED_MAX_ADU", "SIXTEEN_BIT_MAX"]
