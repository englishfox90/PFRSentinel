"""Parse and normalise geographic coordinates entered as free text.

The Settings panel exposes plain text fields for latitude/longitude. Without
normalisation a user can save a value the rest of the app cannot consume —
e.g. a sexagesimal string like ``"31 32 51"``, which ``float()`` rejects,
silently disabling weather, calibration and the all-sky overlay.

``parse_coordinate`` accepts both decimal degrees ("31.33", "-100.457") and
DMS forms ("31 32 51", "31:32:51", "31° 32' 51\"", "100 27 25 W") and returns a
signed decimal-degree float. Sign comes from a leading '-' or a hemisphere
letter (N/E positive, S/W negative). Returns None for blank or unparseable
input so callers can refuse to persist garbage.
"""
import re
from typing import Optional

# Degree/minute/second symbols and separators we treat as token delimiters.
_DMS_SEPARATORS = re.compile(r"[°ºdD:'\"´`’′″,]+")
_HEMISPHERE = re.compile(r"[NSEWnsew]")


def parse_coordinate(text, is_longitude: bool = False) -> Optional[float]:
    """Return signed decimal degrees from decimal or DMS text, else None.

    Args:
        text: The raw field value (any type; coerced to str).
        is_longitude: True applies a ±180° range check, False applies ±90°.
    """
    if text is None:
        return None
    s = str(text).strip()
    if not s:
        return None

    # Pull out an optional hemisphere letter (leading or trailing).
    hemi = None
    mh = _HEMISPHERE.search(s)
    if mh:
        hemi = mh.group(0).upper()
        s = (s[:mh.start()] + s[mh.end():]).strip()

    tokens = _DMS_SEPARATORS.sub(" ", s).split()
    if not tokens or len(tokens) > 3:
        return None

    try:
        nums = [float(t) for t in tokens]
    except ValueError:
        return None

    # Sign is carried by the degrees token only; minutes/seconds are magnitudes.
    negative = tokens[0].lstrip().startswith("-") or nums[0] < 0
    deg = abs(nums[0])
    minutes = abs(nums[1]) if len(nums) > 1 else 0.0
    seconds = abs(nums[2]) if len(nums) > 2 else 0.0
    if minutes >= 60.0 or seconds >= 60.0:
        return None

    value = deg + minutes / 60.0 + seconds / 3600.0
    if negative or hemi in ("S", "W"):
        value = -value

    limit = 180.0 if is_longitude else 90.0
    if abs(value) > limit:
        return None
    return value


def is_unsigned_dms(text) -> bool:
    """True for a degrees-minutes(-seconds) value with no sign and no hemisphere
    letter: the one form whose hemisphere the parser has to assume (north /
    east). Decimal values, signed values and lettered values return False."""
    if text is None:
        return False
    s = str(text).strip()
    if not s or _HEMISPHERE.search(s) or s.startswith("-"):
        return False
    return len(_DMS_SEPARATORS.sub(" ", s).split()) >= 2


def to_decimal_string(value: float, precision: int = 7) -> str:
    """Format a decimal-degree float as a trimmed canonical string."""
    s = f"{value:.{precision}f}".rstrip("0").rstrip(".")
    return "0" if s in ("", "-", "-0") else s
