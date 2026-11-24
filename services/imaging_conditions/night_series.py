"""
One measurement kept as a history of NIGHTS, plus the night in progress.

``star_baseline`` keeps one of these per Moon bin for the clear-sky star
count and one for the FWHM. A night is the app's observing night — local
noon to the next local noon (``timelapse_window.observing_night``, the same
cutoff as the image library) — so it never splits at midnight.

The night in progress keeps its samples (``NightSamples``); when a sample
arrives for another night it is closed into one entry — median, frame count,
date — but only if it holds enough frames to be a night at all: ten clear
minutes between clouds are not one. The history is oldest-out by NUMBER OF
NIGHTS, never by calendar age, so a month with the roof closed removes
nothing.

The figure read from it is a percentile across the stored nights, with the
night in progress taking part once it has enough frames, so a rig's first
night behaves like a plain running median.

Pure data: no clock, no file, no lock. ``to_dict`` / ``from_dict`` are the
file form, and ``from_dict`` accepts anything — whatever cannot be read is
dropped, never raised.
"""
from __future__ import annotations

import math
import statistics
from datetime import date, datetime
from typing import List, Optional

# Starting guesses, not measurements. 60 nights is a couple of months of
# clear nights per Moon bin: long enough that one hazy night is one voice
# in many, short enough that a slow change of the site still gets in.
MAX_NIGHTS = 60
# Frames a night needs before it counts, when the caller names no figure
# (the feed passes ``baseline_min_samples``, 30 by default).
MIN_NIGHT_FRAMES = 30
# The night in progress keeps at most this many samples. When full it drops
# every second one and from then on keeps every second new frame (then every
# fourth, ...), so the kept samples always span the whole night evenly and
# the median stays a median of the night, not of its last hours.
MAX_NIGHT_SAMPLES = 720
MAX_FRAMES = 10_000_000
MAX_STRIDE = 1 << 16


def night_key(now: Optional[datetime] = None) -> str:
    """ISO date of the observing night containing ``now``. An aware datetime
    is read in the host's local zone, like the timelapse and the library."""
    from services.timelapse_window import observing_night

    if now is None:
        now = datetime.now()
    elif now.tzinfo is not None:
        now = now.astimezone().replace(tzinfo=None)
    return observing_night(now).isoformat()


def percentile(values, pct: float) -> Optional[float]:
    """Linear-interpolated percentile (0-100); None for no values."""
    ordered = sorted(float(v) for v in values)
    if not ordered:
        return None
    rank = max(0.0, min(100.0, float(pct))) / 100.0 * (len(ordered) - 1)
    lo = int(math.floor(rank))
    hi = min(lo + 1, len(ordered) - 1)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (rank - lo)


def _number(value, lo: float, hi: float):
    """``value`` if it is a finite number in range (an int stays an int, so
    star counts stay compact in the file), else None."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value) or not lo <= value <= hi:
        return None
    return value if isinstance(value, int) else round(float(value), 2)


def _whole(value, lo: int, hi: int) -> Optional[int]:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if lo <= value <= hi else None


def _valid_night(value) -> bool:
    if not isinstance(value, str) or len(value) != 10:
        return False
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


class NightSamples:
    """The night in progress: its date, how many frames it has seen and an
    evenly thinned sample of their values."""

    def __init__(self, night: str, max_samples: int = MAX_NIGHT_SAMPLES):
        self.night = night
        self.frames = 0
        self.stride = 1
        self.samples: List[float] = []
        self._max = max(2, int(max_samples))

    def add(self, value: float) -> None:
        if self.frames < MAX_FRAMES and self.frames % self.stride == 0:
            self.samples.append(value)
            if len(self.samples) > self._max and self.stride < MAX_STRIDE:
                del self.samples[1::2]
                self.stride *= 2
        self.frames = min(self.frames + 1, MAX_FRAMES)

    def median(self) -> Optional[float]:
        return float(statistics.median(self.samples)) if self.samples else None

    def to_dict(self) -> dict:
        return {'date': self.night, 'frames': self.frames, 'stride': self.stride,
                'samples': list(self.samples)}

    @classmethod
    def from_dict(cls, data, lo: float, hi: float,
                  max_samples: int = MAX_NIGHT_SAMPLES) -> Optional['NightSamples']:
        if not isinstance(data, dict) or not _valid_night(data.get('date')):
            return None
        raw = data.get('samples')
        if not isinstance(raw, list):
            return None
        tonight = cls(data['date'], max_samples)
        for item in raw[-tonight._max:]:
            value = _number(item, lo, hi)
            if value is not None:
                tonight.samples.append(value)
        if not tonight.samples:
            return None
        stride = _whole(data.get('stride'), 1, MAX_STRIDE) or 1
        tonight.stride = stride if stride & (stride - 1) == 0 else 1
        frames = _whole(data.get('frames'), 0, MAX_FRAMES) or 0
        tonight.frames = max(frames, len(tonight.samples))
        return tonight


class NightSeries:
    """Stored nights (oldest first) and the night in progress."""

    def __init__(self, lo: float, hi: float, *,
                 max_nights: int = MAX_NIGHTS, max_samples: int = MAX_NIGHT_SAMPLES):
        self._lo, self._hi = lo, hi
        self._max_nights = max(1, int(max_nights))
        self._max_samples = max_samples
        self.nights: List[dict] = []
        self.tonight: Optional[NightSamples] = None

    def add(self, night: str, value, min_frames: int = MIN_NIGHT_FRAMES) -> bool:
        """One frame's value for ``night``. A frame of another night closes
        the one in progress first. False when the value is not usable."""
        value = _number(value, self._lo, self._hi)
        if value is None or not _valid_night(night):
            return False
        if self.tonight is not None and self.tonight.night != night:
            self.close_night(min_frames)
        if self.tonight is None:
            self.tonight = NightSamples(night, self._max_samples)
        self.tonight.add(value)
        return True

    def close_night(self, min_frames: int = MIN_NIGHT_FRAMES) -> None:
        """Move the night in progress into the history if it is a night at
        all; either way it is no longer in progress."""
        tonight, self.tonight = self.tonight, None
        if tonight is None or tonight.frames < max(1, int(min_frames)):
            return
        entry = {'date': tonight.night, 'median': round(tonight.median(), 2),
                 'frames': tonight.frames}
        # The same date twice (a clock stepped back): one night, the fuller one.
        for i, old in enumerate(self.nights):
            if old['date'] == entry['date']:
                if entry['frames'] >= old['frames']:
                    self.nights[i] = entry
                return
        self.nights.append(entry)
        del self.nights[:-self._max_nights]

    def tonight_frames(self, night: Optional[str] = None) -> int:
        """Frames of the night in progress; 0 when it is not ``night``."""
        if self.tonight is None or (night is not None and self.tonight.night != night):
            return 0
        return self.tonight.frames

    def contributing(self, min_frames: int = MIN_NIGHT_FRAMES) -> List[dict]:
        """The nights the figure is taken across: stored nights with enough
        frames and the night in progress once it has them. A night in
        progress left over from an earlier date counts the same way — it is
        a finished night that simply has not been closed yet."""
        needed = max(1, int(min_frames))
        nights = [n for n in self.nights if n['frames'] >= needed]
        if self.tonight is not None and self.tonight.frames >= needed:
            nights.append({'date': self.tonight.night, 'median': self.tonight.median(),
                           'frames': self.tonight.frames})
        return nights

    def value(self, pct: float, min_frames: int = MIN_NIGHT_FRAMES) -> Optional[float]:
        return percentile([n['median'] for n in self.contributing(min_frames)], pct)

    def clear(self) -> None:
        self.nights = []
        self.tonight = None

    def is_empty(self) -> bool:
        return not self.nights and self.tonight is None

    def to_dict(self) -> dict:
        return {'nights': [dict(n) for n in self.nights],
                'tonight': self.tonight.to_dict() if self.tonight is not None else None}

    def load(self, data) -> 'NightSeries':
        """Fill from a file's dict; anything unusable is left out."""
        self.clear()
        if not isinstance(data, dict):
            return self
        seen = set()
        nights = data.get('nights')
        for item in (nights if isinstance(nights, list) else []):
            if not isinstance(item, dict) or not _valid_night(item.get('date')):
                continue
            median = _number(item.get('median'), self._lo, self._hi)
            frames = _whole(item.get('frames'), 1, MAX_FRAMES)
            if median is None or frames is None or item['date'] in seen:
                continue
            seen.add(item['date'])
            self.nights.append({'date': item['date'], 'median': float(median),
                                'frames': frames})
        del self.nights[:-self._max_nights]
        self.tonight = NightSamples.from_dict(data.get('tonight'), self._lo, self._hi,
                                              self._max_samples)
        return self
