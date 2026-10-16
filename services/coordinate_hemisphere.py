"""Resolve the hemisphere of a coordinate the user entered without one.

A degrees-minutes-seconds value with no N/S/E/W letter and no minus sign has
to be read as *some* hemisphere; the config migration reads it as north / east
so the app keeps working, and records the original text under
``weather.hemisphere_unconfirmed`` so the GUI can ask once instead of deciding
silently. This module owns that record: the default to pre-select in the
prompt, and applying the answer to the stored decimal.

The default is a guess, not a verdict. Latitude defaults to north because
nearly every user is. Longitude follows the PC clock: a machine running at a
negative UTC offset is almost certainly west of Greenwich. Both are only the
pre-selected option; the user confirms.
"""
from datetime import datetime
from typing import Dict, Optional

from .coordinates import to_decimal_string

UNCONFIRMED_KEY = "hemisphere_unconfirmed"

# Which pair of letters each field is choosing between, positive first.
HEMISPHERES = {
    "latitude": ("N", "S"),
    "longitude": ("E", "W"),
}
NEGATIVE = {"S", "W"}


def pending_confirmations(weather: Optional[dict]) -> Dict[str, str]:
    """``{field: original_text}`` for coordinates awaiting a hemisphere."""
    if not isinstance(weather, dict):
        return {}
    pending = weather.get(UNCONFIRMED_KEY)
    if not isinstance(pending, dict):
        return {}
    return {k: str(v) for k, v in pending.items() if k in HEMISPHERES and weather.get(k)}


def default_hemisphere(field: str, utc_offset_hours: Optional[float] = None) -> str:
    """The option to pre-select for ``field``.

    ``utc_offset_hours`` is the host's current UTC offset; read from the clock
    when omitted. Only longitude uses it.
    """
    if field == "latitude":
        return "N"
    if utc_offset_hours is None:
        offset = datetime.now().astimezone().utcoffset()
        utc_offset_hours = offset.total_seconds() / 3600 if offset else 0.0
    return "W" if utc_offset_hours < 0 else "E"


def apply_hemisphere(weather: dict, field: str, hemisphere: str) -> Optional[str]:
    """Sign ``weather[field]`` for ``hemisphere`` and clear its pending mark.

    Returns the canonical value written, or None when the field holds nothing
    parseable (the mark is still cleared: there is nothing left to ask about).
    """
    hemisphere = str(hemisphere).strip().upper()
    if hemisphere not in HEMISPHERES.get(field, ()):
        raise ValueError(f"{hemisphere!r} is not a hemisphere for {field}")
    pending = weather.get(UNCONFIRMED_KEY)
    if isinstance(pending, dict):
        pending.pop(field, None)
    try:
        magnitude = abs(float(weather.get(field)))
    except (TypeError, ValueError):
        return None
    value = -magnitude if hemisphere in NEGATIVE else magnitude
    canonical = to_decimal_string(value)
    weather[field] = canonical
    return canonical


def dismiss_confirmation(weather: dict, field: str) -> None:
    """Forget the pending mark without changing the value — the user edited
    the field themselves, so the question is answered."""
    pending = weather.get(UNCONFIRMED_KEY)
    if isinstance(pending, dict):
        pending.pop(field, None)
