"""Which clear-sky baseline a frame is compared with, by the Moon's state.

A bright Moon washes out most of the faint stars a moonless sky shows, so
the baseline is kept per Moon bin (``star_baseline``). The bins:

- ``moon_down`` — the Moon below the horizon;
- ``moon_up_0_25`` / ``moon_up_25_50`` — a thin Moon, wherever it is;
- ``moon_up_50_75_low`` / ``_high`` and ``moon_up_75_100_low`` / ``_high`` —
  a bright Moon, split by its altitude: high up it lights the whole sky and
  hides far more stars than the same Moon near the horizon, so the two must
  not share a reference;
- ``moon_up_50_75`` / ``moon_up_75_100`` — the same bright Moon when its
  altitude is not known. These are also the names files written before the
  altitude split carry: such nights still load, and stay in these bins;
- ``moon_unknown`` — no site configured, so not even up / down is known.

Every bin has to collect its own clear nights, so there are as few as the
physics allows. The altitude split starts at half lit because moonlight
falls steeply with phase — a half Moon gives roughly a tenth of a full
Moon's light — so below that, where the Moon sits changes the star count
little and a split would only leave two sparse bins.
"""
from __future__ import annotations

import math
from typing import Optional

MOON_DOWN = 'moon_down'
MOON_UNKNOWN = 'moon_unknown'
BAND_LOW = 'low'
BAND_HIGH = 'high'

_ILLUMINATION_EDGES = (25.0, 50.0, 75.0)
_UP_BINS = ('moon_up_0_25', 'moon_up_25_50', 'moon_up_50_75', 'moon_up_75_100')

# Starting guesses, not measurements.
# The Moon counts as high from here up.
MOON_HIGH_ALTITUDE_DEG = 30.0
# Only a Moon at least this lit is split by altitude (see the module text).
ALTITUDE_SPLIT_MIN_ILLUMINATION_PCT = 50.0
# The Moon climbs about 15° an hour and its altitude is read from a
# five-minute cache, so it can sit within a degree or so of the line for
# many frames. It changes band only once it is this far past the line.
MOON_ALTITUDE_HYSTERESIS_DEG = 3.0

_SPLIT_BINS = tuple(f"{name}_{band}" for name in _UP_BINS[2:]
                    for band in (BAND_LOW, BAND_HIGH))
MOON_BINS = (MOON_DOWN,) + _UP_BINS + _SPLIT_BINS + (MOON_UNKNOWN,)


def _finite(value) -> Optional[float]:
    if isinstance(value, bool):
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


class MoonAltitudeBand:
    """Low / high with hysteresis, so a Moon at the line keeps one bin."""

    def __init__(self, edge_deg: float = MOON_HIGH_ALTITUDE_DEG,
                 hysteresis_deg: float = MOON_ALTITUDE_HYSTERESIS_DEG):
        self._edge = edge_deg
        self._hysteresis = hysteresis_deg
        self._band: Optional[str] = None

    def update(self, altitude_deg) -> Optional[str]:
        """The band for this altitude; None (and forgotten) when unknown."""
        altitude = _finite(altitude_deg)
        if altitude is None:
            self._band = None
        elif self._band is None:
            self._band = BAND_HIGH if altitude >= self._edge else BAND_LOW
        elif self._band == BAND_LOW and altitude >= self._edge + self._hysteresis:
            self._band = BAND_HIGH
        elif self._band == BAND_HIGH and altitude < self._edge - self._hysteresis:
            self._band = BAND_LOW
        return self._band

    def reset(self) -> None:
        self._band = None


def moon_bin(moon_up, illumination_pct, altitude_band: Optional[str] = None) -> str:
    """The baseline bin for the Moon's state. ``moon_up`` None = no site.
    ``altitude_band`` is ``MoonAltitudeBand.update``'s answer; None (altitude
    unknown) reads a bright Moon's un-split bin."""
    if moon_up is None:
        return MOON_UNKNOWN
    if not moon_up:
        return MOON_DOWN
    illum = _finite(illumination_pct)
    if illum is None:
        return MOON_UNKNOWN
    name = _UP_BINS[-1]
    for i, edge in enumerate(_ILLUMINATION_EDGES):
        if illum < edge:
            name = _UP_BINS[i]
            break
    if illum >= ALTITUDE_SPLIT_MIN_ILLUMINATION_PCT and altitude_band in (BAND_LOW, BAND_HIGH):
        return f"{name}_{altitude_band}"
    return name
