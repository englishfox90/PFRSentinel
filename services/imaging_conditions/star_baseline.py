"""
This rig's clear-sky star count, learned from its own good frames.

The star count ``sky_evidence`` measures depends on the lens, the sensor,
the exposure and the light pollution, so no fixed number can say "clear".
Frames the feed is confident about — roof Open, astronomical night, ML
Clear, not sensor noise — teach a per-bin median here; the bins split by
the Moon (down, or up in an illumination quarter) because a bright Moon
washes out most of the faint stars a moonless sky shows.

Learned on each installation, never shipped: no star count from any other
rig is a default. The file carries a stamp — camera, analysed frame size
and the lens calibration's plate scale — and a frame from a different
camera, frame size or lens (scale off by more than LENS_TOLERANCE) starts
the baseline over, because the old counts no longer describe this sky.

Persisted to ``<app-data>/imaging_conditions/star_baseline.json``: written
atomically (temp file + ``os.replace``), at most every SAVE_INTERVAL_S,
bounded to MAX_SAMPLES per bin, and read back tolerantly — a truncated,
oversized or foreign file loads as "no baseline yet", never as an error.
"""
from __future__ import annotations

import json
import math
import os
import statistics
import tempfile
import threading
import time
from typing import Callable, Dict, List, Optional

from services.logger import app_logger
from services.utils_paths import get_app_data_dir

FILE_VERSION = 1
FILENAME = 'star_baseline.json'
SUBDIR = 'imaging_conditions'

# A couple of hours of clear frames at a 30 s cadence per bin: enough for a
# stable median, short enough that a re-pointed camera or a new exposure
# regime replaces the old figure within a night or two.
MAX_SAMPLES = 240
MAX_FILE_BYTES = 256 * 1024
SAVE_INTERVAL_S = 600.0
MAX_STAR_COUNT = 100_000
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


class StarBaseline:
    """Thread-safe per-bin sample store with lazy load and throttled saves."""

    def __init__(self, path: Optional[str] = None, *, max_samples: int = MAX_SAMPLES,
                 clock: Callable[[], float] = time.monotonic,
                 save_interval_s: float = SAVE_INTERVAL_S):
        self._path = path
        self._max = max_samples
        self._clock = clock
        self._save_interval_s = save_interval_s
        self._lock = threading.Lock()
        self._bins: Dict[str, List[int]] = {}
        self._stamp: Optional[dict] = None
        self._loaded = False
        self._dirty = False
        self._last_save: Optional[float] = None

    @property
    def path(self) -> str:
        return self._path or default_baseline_path()

    def add(self, bin_name: str, star_count: int) -> None:
        if bin_name not in MOON_BINS or not _valid_count(star_count):
            return
        with self._lock:
            self._ensure_loaded_locked()
            samples = self._bins.setdefault(bin_name, [])
            samples.append(int(star_count))
            del samples[:-self._max]
            self._dirty = True

    def baseline(self, bin_name: str, min_samples: int) -> Optional[float]:
        """Median clear-sky count for the bin, or None with too few samples."""
        with self._lock:
            self._ensure_loaded_locked()
            samples = self._bins.get(bin_name) or []
            if len(samples) < max(1, int(min_samples)):
                return None
            value = float(statistics.median(samples))
        return value if value > 0 else None

    def status(self, bin_name: str, min_samples: int) -> dict:
        """``baseline_status`` for the contract: learning until the bin has
        ``min_samples`` clear frames, then ready."""
        needed = max(1, int(min_samples))
        samples = self.sample_count(bin_name)
        return {'state': 'ready' if samples >= needed else 'learning',
                'samples': samples,
                'needed': needed, 'bin': bin_name}

    def ensure_stamp(self, stamp: dict) -> bool:
        """Adopt ``stamp`` (camera, width, height, lens_a1). Returns True when
        it differs from the stored one and the samples were dropped."""
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
            self._stamp = dict(stamp)
            self._dirty = True
        app_logger.info("Imaging conditions: camera, frame size or lens changed; "
                        "learning this sky's star baseline again")
        return True

    def sample_count(self, bin_name: str) -> int:
        with self._lock:
            self._ensure_loaded_locked()
            return len(self._bins.get(bin_name) or [])

    def maybe_save(self) -> bool:
        """Save when dirty and the throttle allows. Returns True if written."""
        now = self._clock()
        with self._lock:
            if not self._dirty:
                return False
            if self._last_save is not None and now - self._last_save < self._save_interval_s:
                return False
            data = {'version': FILE_VERSION, 'stamp': self._stamp,
                    'bins': {k: list(v) for k, v in self._bins.items()}}
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
        """Forget every sample, on disk too (files only)."""
        with self._lock:
            self._bins = {}
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
        self._bins, self._stamp = load_file(self.path, self._max)

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


def _valid_count(value) -> bool:
    return (isinstance(value, int) and not isinstance(value, bool)
            and 0 <= value <= MAX_STAR_COUNT)


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


def load_bins(path: str, max_samples: int = MAX_SAMPLES) -> Dict[str, List[int]]:
    """Read a baseline file into ``{bin: [counts]}``; anything unusable is {}."""
    return load_file(path, max_samples)[0]


def load_file(path: str, max_samples: int = MAX_SAMPLES):
    """``(bins, stamp)`` from a baseline file; anything unusable is ``({}, None)``."""
    try:
        if not os.path.isfile(path):
            return {}, None
        if os.path.getsize(path) > MAX_FILE_BYTES:
            app_logger.warning("Imaging conditions: star baseline file too large; starting over")
            return {}, None
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
    except Exception as e:
        app_logger.warning(f"Imaging conditions: star baseline unreadable ({e}); starting over")
        return {}, None
    if not isinstance(data, dict) or data.get('version') != FILE_VERSION:
        return {}, None
    bins = data.get('bins')
    if not isinstance(bins, dict):
        return {}, None
    result = {}
    for name, samples in bins.items():
        if name not in MOON_BINS or not isinstance(samples, list):
            continue
        clean = [int(s) for s in samples if _valid_count(s)]
        if clean:
            result[name] = clean[-max_samples:]
    return result, _clean_stamp(data.get('stamp'))
