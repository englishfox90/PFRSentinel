"""
This rig's clear-sky star count, learned from its own good nights.

The star count depends on the lens, the sensor, the exposure and the light
pollution, so no fixed number can say "clear". Frames the feed is confident
about — roof Open, astronomical night, ML Clear, not sensor noise
(``frame_rules.is_baseline_frame``) — teach it. The bins split by the Moon
(down, or up in an illumination quarter) because a bright Moon washes out
most of the faint stars a moonless sky shows.

What is kept is a store of NIGHTS, not frames (``night_series``): per Moon
bin, one entry per clear night — the night's median count, how many clear
frames it came from, its date — the newest MAX_NIGHTS of them, oldest out by
number of nights and never by age. The baseline is a HIGH percentile across
those nights (BASELINE_PERCENTILE), not their median: "clear sky" means this
rig's good nights, so a hazy night the ML still calls Clear barely moves it
and it sharpens as nights accumulate. Tonight takes part as it goes, once it
has enough clear frames, so a first night behaves like a running median.

The same clear frames' FWHM is kept the same way (one series, not per Moon
bin: seeing does not follow the Moon) and read from the LOW side
(FWHM_REFERENCE_PERCENTILE), because for star width good is small.

Learned on each installation, never shipped: no star count from any other
rig is a default. The file carries a stamp — camera, analysed frame size
and the lens calibration's plate scale — and a frame from a different
camera, frame size or lens (scale off by more than LENS_TOLERANCE) starts
everything over, because the old nights no longer describe this sky.

Persisted to ``<app-data>/imaging_conditions/star_baseline.json`` with the
night in progress included, so a restart mid-night loses nothing older than
the last save: written atomically (temp file + ``os.replace``), at most
every SAVE_INTERVAL_S plus ``save_now`` at capture stop and shutdown,
bounded in size, and read back tolerantly — a truncated, oversized, foreign
or older-version file loads as "no baseline yet", never as an error.
Version 1 files (240 frame counts per bin) are not migrated.
"""
from __future__ import annotations

import json
import math
import os
import tempfile
import threading
import time
from typing import Callable, Dict, Optional, Tuple

from services.logger import app_logger
from services.utils_paths import get_app_data_dir

from .night_series import (  # noqa: F401 — re-exported for callers
    MAX_NIGHTS, MAX_NIGHT_SAMPLES, MIN_NIGHT_FRAMES, NightSeries, night_key,
)

FILE_VERSION = 2
FILENAME = 'star_baseline.json'
SUBDIR = 'imaging_conditions'

# Starting guesses, not measurements. The 75th percentile of the stored
# nights' medians: high enough that the figure is this rig's good nights,
# not so high that one exceptional night sets it alone.
BASELINE_PERCENTILE = 75.0
# The mirror image for star width, where good is LOW: the 25th percentile
# of the nights' median FWHM is what this rig's sharp nights look like.
FWHM_REFERENCE_PERCENTILE = 25.0

MAX_FILE_BYTES = 256 * 1024
SAVE_INTERVAL_S = 600.0
MAX_STAR_COUNT = 100_000
FWHM_MIN_PX = 0.3
FWHM_MAX_PX = 100.0
# A refinement of the same calibration moves a1 by well under 1 %; a
# re-focused, swapped or re-mounted lens moves it by more than this.
LENS_TOLERANCE = 0.05

MOON_DOWN = 'moon_down'
MOON_UNKNOWN = 'moon_unknown'
_ILLUMINATION_EDGES = (25.0, 50.0, 75.0)
MOON_BINS = (MOON_DOWN, 'moon_up_0_25', 'moon_up_25_50', 'moon_up_50_75',
             'moon_up_75_100', MOON_UNKNOWN)


def moon_bin(moon_up, illumination_pct) -> str:
    """The baseline bin for the Moon's state. ``moon_up`` None = no site."""
    if moon_up is None:
        return MOON_UNKNOWN
    if not moon_up:
        return MOON_DOWN
    try:
        illum = float(illumination_pct)
    except (TypeError, ValueError):
        return MOON_UNKNOWN
    if not math.isfinite(illum):
        return MOON_UNKNOWN
    for i, edge in enumerate(_ILLUMINATION_EDGES):
        if illum < edge:
            return MOON_BINS[1 + i]
    return MOON_BINS[4]


def default_baseline_path() -> str:
    return os.path.join(get_app_data_dir(), SUBDIR, FILENAME)


def _star_series() -> NightSeries:
    return NightSeries(0, MAX_STAR_COUNT)


def _fwhm_series() -> NightSeries:
    return NightSeries(FWHM_MIN_PX, FWHM_MAX_PX)


class StarBaseline:
    """Thread-safe store of clear nights with lazy load and throttled saves."""

    def __init__(self, path: Optional[str] = None, *,
                 clock: Callable[[], float] = time.monotonic,
                 save_interval_s: float = SAVE_INTERVAL_S,
                 night_fn: Callable[[], str] = night_key):
        self._path = path
        self._clock = clock
        self._save_interval_s = save_interval_s
        self._night_fn = night_fn
        self._lock = threading.Lock()
        self._bins: Dict[str, NightSeries] = {}
        self._fwhm = _fwhm_series()
        self._stamp: Optional[dict] = None
        self._loaded = False
        self._dirty = False
        self._last_save: Optional[float] = None

    @property
    def path(self) -> str:
        return self._path or default_baseline_path()

    def add(self, bin_name: str, star_count, night: Optional[str] = None,
            min_frames: int = MIN_NIGHT_FRAMES) -> None:
        """One clear frame's star count for ``night`` (default: the observing
        night now). ``min_frames`` is what the night in progress must hold to
        enter the history when a later night replaces it."""
        if bin_name not in MOON_BINS:
            return
        night = night or self._night_fn()
        with self._lock:
            self._ensure_loaded_locked()
            series = self._bins.get(bin_name)
            if series is None:
                series = _star_series()
            if series.add(night, star_count, min_frames):
                self._bins[bin_name] = series
                self._dirty = True

    def add_fwhm(self, fwhm_px, night: Optional[str] = None,
                 min_frames: int = MIN_NIGHT_FRAMES) -> None:
        """One clear frame's FWHM (px) for ``night``."""
        night = night or self._night_fn()
        with self._lock:
            self._ensure_loaded_locked()
            if self._fwhm.add(night, fwhm_px, min_frames):
                self._dirty = True

    def baseline(self, bin_name: str, min_samples: int) -> Optional[float]:
        """Clear-sky count for the bin: the BASELINE_PERCENTILE across its
        nights of at least ``min_samples`` clear frames (tonight included
        once it has them), or None with no such night."""
        with self._lock:
            self._ensure_loaded_locked()
            series = self._bins.get(bin_name)
            value = series.value(BASELINE_PERCENTILE, min_samples) if series else None
        return value if value is not None and value > 0 else None

    def status(self, bin_name: str, min_samples: int, night: Optional[str] = None) -> dict:
        """``baseline_status`` for the contract. Learning until the bin has
        one night of ``min_samples`` clear frames: ``samples`` is then
        tonight's clear frames. Ready from there on: ``samples`` is every
        clear frame behind the baseline and ``nights`` the nights it is
        taken across."""
        needed = max(1, int(min_samples))
        with self._lock:
            self._ensure_loaded_locked()
            series = self._bins.get(bin_name)
            nights = series.contributing(needed) if series else []
            tonight = series.tonight_frames(night) if series else 0
        if nights:
            return {'state': 'ready', 'samples': sum(n['frames'] for n in nights),
                    'needed': needed, 'bin': bin_name, 'nights': len(nights)}
        return {'state': 'learning', 'samples': tonight, 'needed': needed,
                'bin': bin_name, 'nights': 0}

    def fwhm_reference(self, min_samples: int) -> Tuple[Optional[float], int]:
        """``(px, nights)``: this rig's sharp-night FWHM — the
        FWHM_REFERENCE_PERCENTILE across the nights' medians — and how many
        nights it is taken across. ``(None, 0)`` before the first."""
        with self._lock:
            self._ensure_loaded_locked()
            nights = self._fwhm.contributing(min_samples)
            value = self._fwhm.value(FWHM_REFERENCE_PERCENTILE, min_samples)
        return value, len(nights)

    def nights(self, bin_name: str) -> list:
        """The stored nights of a bin, oldest first (copies)."""
        with self._lock:
            self._ensure_loaded_locked()
            series = self._bins.get(bin_name)
            return [dict(n) for n in series.nights] if series else []

    def ensure_stamp(self, stamp: dict) -> bool:
        """Adopt ``stamp`` (camera, width, height, lens_a1). Returns True when
        it differs from the stored one and every night was dropped."""
        with self._lock:
            self._ensure_loaded_locked()
            old = self._stamp
            if old is None:
                self._stamp = dict(stamp)
                self._dirty = True
                return False
            if stamps_match(old, stamp):
                if old.get('lens_a1') is None and stamp.get('lens_a1') is not None:
                    self._stamp = dict(stamp)   # a first calibration is not a lens change
                    self._dirty = True
                return False
            self._bins = {}
            self._fwhm = _fwhm_series()
            self._stamp = dict(stamp)
            self._dirty = True
        app_logger.info("Imaging conditions: camera, frame size or lens changed; "
                        "learning this sky's star baseline again")
        return True

    def sample_count(self, bin_name: str) -> int:
        """Clear frames of the bin's night in progress."""
        with self._lock:
            self._ensure_loaded_locked()
            series = self._bins.get(bin_name)
            return series.tonight_frames() if series else 0

    def maybe_save(self) -> bool:
        """Save when dirty and the throttle allows. Returns True if written."""
        now = self._clock()
        with self._lock:
            if not self._dirty:
                return False
            if self._last_save is not None and now - self._last_save < self._save_interval_s:
                return False
            data = {'version': FILE_VERSION, 'stamp': self._stamp,
                    'stars': {k: v.to_dict() for k, v in self._bins.items()},
                    'fwhm': self._fwhm.to_dict()}
            self._dirty = False
            self._last_save = now
        if self._write(data):
            return True
        with self._lock:
            self._dirty = True
        return False

    def save_now(self) -> bool:
        with self._lock:
            self._last_save = None
        return self.maybe_save()

    def reset(self) -> None:
        """Forget every night, on disk too (files only)."""
        with self._lock:
            self._bins = {}
            self._fwhm = _fwhm_series()
            self._stamp = None
            self._loaded = True
            self._dirty = False
        try:
            if os.path.isfile(self.path):
                os.remove(self.path)
        except OSError as e:
            app_logger.warning(f"Imaging conditions: could not remove star baseline: {e}")

    # -- persistence -------------------------------------------------------

    def _ensure_loaded_locked(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        self._bins, self._fwhm, self._stamp = load_file(self.path)

    def _write(self, data: dict) -> bool:
        path = self.path
        tmp = None
        try:
            folder = os.path.dirname(path)
            os.makedirs(folder, exist_ok=True)
            with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=folder,
                                             prefix='.star_baseline.', suffix='.tmp',
                                             delete=False) as f:
                tmp = f.name
                json.dump(data, f)
            os.replace(tmp, path)
            return True
        except Exception as e:
            app_logger.warning(f"Imaging conditions: could not save star baseline: {e}")
            if tmp and os.path.isfile(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass
            return False


def stamps_match(a: dict, b: dict) -> bool:
    if (a.get('camera'), a.get('width'), a.get('height')) != \
            (b.get('camera'), b.get('width'), b.get('height')):
        return False
    la, lb = a.get('lens_a1'), b.get('lens_a1')
    if la is None or lb is None:
        return True
    return abs(la - lb) <= LENS_TOLERANCE * max(abs(la), abs(lb))


def _clean_stamp(stamp) -> Optional[dict]:
    if not isinstance(stamp, dict):
        return None
    lens = stamp.get('lens_a1')
    lens = float(lens) if isinstance(lens, (int, float)) and not isinstance(lens, bool) \
        and math.isfinite(lens) and lens > 0 else None
    def _dim(v):
        return int(v) if isinstance(v, int) and not isinstance(v, bool) and v >= 0 else None
    camera = stamp.get('camera')
    return {'camera': camera if isinstance(camera, str) else '',
            'width': _dim(stamp.get('width')), 'height': _dim(stamp.get('height')),
            'lens_a1': lens}


def load_file(path: str):
    """``(bins, fwhm, stamp)`` from a baseline file: ``{bin: NightSeries}``,
    the FWHM ``NightSeries`` and the stamp. Anything unusable — missing,
    oversized, not JSON, another version — is ``({}, empty series, None)``."""
    empty = ({}, _fwhm_series(), None)
    try:
        if not os.path.isfile(path):
            return empty
        if os.path.getsize(path) > MAX_FILE_BYTES:
            app_logger.warning("Imaging conditions: star baseline file too large; starting over")
            return empty
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
    except Exception as e:
        app_logger.warning(f"Imaging conditions: star baseline unreadable ({e}); starting over")
        return empty
    if not isinstance(data, dict) or data.get('version') != FILE_VERSION:
        return empty
    stars = data.get('stars')
    if not isinstance(stars, dict):
        return empty
    bins = {}
    for name, raw in stars.items():
        if name not in MOON_BINS:
            continue
        series = _star_series().load(raw)
        if not series.is_empty():
            bins[name] = series
    return bins, _fwhm_series().load(data.get('fwhm')), _clean_stamp(data.get('stamp'))
