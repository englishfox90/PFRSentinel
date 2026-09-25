"""
Calibration buffer dump for offline replay (issue #93, package 0).

`CalibrationService` keeps a rolling buffer of per-frame star detections and
never the images. Writing that buffer out as JSON makes a rig's night
replayable on a developer machine: `scripts/dev/allsky/replay_buffer.py`
runs the pole finder, the joint fit and the chance gate on exactly the
detections the service saw, so solver work can be validated against real
skies that nobody here can photograph.

What a dump holds, per frame: ``dt`` (timezone-aware ISO 8601), ``detected``
(``[x, y, flux]`` triples), the measured sky circle, the frame size and
``exposure_s`` when the frame carries it. The per-frame catalogue list
(``above_horizon``) is NOT stored: it is a pure function of ``dt`` and the
site, so the loader rebuilds it through `frame_catalog.above_horizon_stars`
with the catalogue limits the dump records. Nothing else in a frame dict is
written, whatever it carries — a frame that happens to hold an image array
still produces a small file.

Pure module: no Qt. `BufferDumpTrigger` is the service's only entry point;
it owns the three triggers (escape exhaustion, dev-mode basin escapes, the
UI button) and runs automatic dumps on a short-lived daemon thread so the
GUI thread only ever pays for a list copy.
"""
import json
import os
import threading
from dataclasses import asdict, is_dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from services import app_config
from services.dev_mode_config import is_dev_mode_available
from services.logger import app_logger as log

from .fisheye import FisheyeModel
from .frame_catalog import CATALOG_MAX_VMAG, MIN_ALTITUDE_DEG, above_horizon_stars

DUMP_VERSION = 1
DUMP_PREFIX = 'buffer_'
DUMP_SUFFIX = '.json'
DUMP_GLOB = f'{DUMP_PREFIX}*{DUMP_SUFFIX}'
TMP_SUFFIX = '.tmp'
# Microseconds in the stamp: two triggers can fire in the same second (a
# dev-mode escape and the exhaustion it ends in), and `newest_dump` orders
# by name, so the name has to resolve them.
STAMP_FORMAT = '%Y%m%d_%H%M%S_%f'

# Dumps kept per rig. A full 60-frame buffer of 200 detections is ~700 KB;
# five is a night's worth of escape exhaustions plus a couple of manual dumps
# before the diagnostics bundle is exported, for under 4 MB of app data.
MAX_DUMPS = 5

# Site coordinates are rounded to this many decimals before they are
# written. The diagnostics bundle that carries the dump redacts latitude and
# longitude from config.json because it is meant for a public issue; 0.01°
# (~1 km) keeps that promise, while moving every star by at most 0.01°:
# 0.17 mrad, which is 0.2 px on the reporter's 1097 px/rad lens and 0.1 px
# at 750 px. Every tolerance in the solver is 10 px or more, so a replay from
# the rounded site is indistinguishable from one at the exact site.
SITE_DECIMALS = 2

# Frame keys copied verbatim (numbers only). Anything not listed here is
# dropped, which is what keeps image arrays out of the file by construction.
FRAME_SCALARS = ('sky_cx', 'sky_cy', 'sky_r', 'image_width', 'image_height',
                 'exposure_s')

# Attributes `multi_calibrate` sets on a model with setattr rather than as
# dataclass fields (see ALLSKY_HOSTING_SITE_PLAN §0.2). Carried when present
# so a replay can re-run the chance gate exactly as the service did.
# `final_tol_px` and `chance_ratio` became dataclass fields with package 3
# and travel through `asdict` like every other field.
MODEL_EXTRAS = ('chance_expected',)


# ---------------------------------------------------------------------------
# Frame and model (de)serialisation
# ---------------------------------------------------------------------------

def _plain(value):
    """numpy scalar -> Python scalar; everything else unchanged."""
    return value.item() if hasattr(value, 'item') else value


def frame_to_record(frame: dict) -> dict:
    """The JSON-safe subset of one `CalibrationService` frame dict."""
    dt = frame['dt']
    record = {
        'dt': dt.isoformat(),
        'detected': [[float(d[0]), float(d[1]), float(d[2])]
                     for d in frame.get('detected') or ()],
        'n_above_horizon': len(frame.get('above_horizon') or ()),
    }
    for key in FRAME_SCALARS:
        if frame.get(key) is not None:
            record[key] = _plain(frame[key])
    return record


def record_to_frame(record: dict, lat: float, lon: float,
                    max_vmag: float = CATALOG_MAX_VMAG,
                    min_alt_deg: float = MIN_ALTITUDE_DEG) -> dict:
    """Rebuild the frame dict `CalibrationService._detect_frame` produces."""
    dt = datetime.fromisoformat(record['dt'])
    frame = {
        'dt': dt,
        'detected': [tuple(float(v) for v in d) for d in record.get('detected', ())],
        'above_horizon': above_horizon_stars(dt, lat, lon, max_vmag, min_alt_deg),
    }
    for key in FRAME_SCALARS:
        if key in record:
            frame[key] = record[key]
    return frame


def model_to_dict(model) -> Optional[dict]:
    """`FisheyeModel` (or a dict) -> JSON dict with the setattr extras, or None."""
    if model is None:
        return None
    data = asdict(model) if is_dataclass(model) else dict(model)
    for key in MODEL_EXTRAS:
        value = getattr(model, key, None) if is_dataclass(model) else data.get(key)
        if value is not None:
            data[key] = float(value)
    return data


def model_from_dict(data: Optional[dict]) -> Optional[FisheyeModel]:
    """Inverse of `model_to_dict`; unknown keys ignored, extras re-attached."""
    if not data:
        return None
    fields = FisheyeModel.__dataclass_fields__
    model = FisheyeModel(**{k: v for k, v in data.items() if k in fields})
    for key in MODEL_EXTRAS:
        if data.get(key) is not None:
            setattr(model, key, float(data[key]))
    return model


# ---------------------------------------------------------------------------
# Files
# ---------------------------------------------------------------------------

def write_dump(frames: List[dict], model, lat: float, lon: float, path,
               now: Optional[datetime] = None) -> Path:
    """Write ``frames`` (+ ``model`` and site) to exactly ``path``.

    No rotation: this is the primitive `dump_buffer` and the dev scripts
    share. The file is written beside its target and renamed into place so
    a diagnostics export never picks up a half-written dump.
    """
    path = Path(path)
    now = now if now is not None else datetime.now().astimezone()
    from version import __version__  # top-level module; resolved at call time
    payload = {
        'version': DUMP_VERSION,
        'created_at': now.isoformat(timespec='seconds'),
        'app_version': __version__,
        'lat': round(float(lat), SITE_DECIMALS),
        'lon': round(float(lon), SITE_DECIMALS),
        'catalog': {'max_vmag': CATALOG_MAX_VMAG, 'min_alt_deg': MIN_ALTITUDE_DEG},
        'model': model_to_dict(model),
        'frames': [frame_to_record(f) for f in frames],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + TMP_SUFFIX)
    with open(tmp, 'w', encoding='utf-8') as fh:
        json.dump(payload, fh)
    os.replace(tmp, path)
    return path


def load_buffer(path) -> Tuple[List[dict], Optional[dict], float, float]:
    """``(frames, model_dict, lat, lon)`` from a dump written by `write_dump`.

    ``frames`` have the exact shape the service builds (aware datetimes,
    tuple detections, the catalogue list recomputed for the recorded site).
    ``model_dict`` is the raw dict (or None); `model_from_dict` turns it into
    a `FisheyeModel`.
    """
    with open(path, encoding='utf-8') as fh:
        payload = json.load(fh)
    version = payload.get('version')
    if version != DUMP_VERSION:
        raise ValueError(f"Unsupported buffer dump version {version!r} in {path}")
    lat = float(payload['lat'])
    lon = float(payload['lon'])
    catalog = payload.get('catalog') or {}
    max_vmag = float(catalog.get('max_vmag', CATALOG_MAX_VMAG))
    min_alt = float(catalog.get('min_alt_deg', MIN_ALTITUDE_DEG))
    frames = [record_to_frame(r, lat, lon, max_vmag, min_alt)
              for r in payload.get('frames', ())]
    return frames, payload.get('model'), lat, lon


def list_dumps(out_dir) -> List[Path]:
    """Dump files in ``out_dir``, oldest first. Stamped names sort by time."""
    out_dir = Path(out_dir)
    if not out_dir.is_dir():
        return []
    return sorted(p for p in out_dir.glob(DUMP_GLOB) if p.is_file())


def newest_dump(out_dir) -> Optional[Path]:
    dumps = list_dumps(out_dir)
    return dumps[-1] if dumps else None


def prune_dumps(out_dir, keep: int = MAX_DUMPS) -> List[Path]:
    """Delete all but the newest ``keep`` dumps, plus any ``.tmp`` left by a
    write that died mid-way. Files only, never folders."""
    removed = []
    dumps = list_dumps(out_dir)
    stale_tmps = sorted(p for p in Path(out_dir).glob(f'{DUMP_GLOB}{TMP_SUFFIX}')
                        if p.is_file()) if Path(out_dir).is_dir() else []
    for stale in dumps[:max(0, len(dumps) - keep)] + stale_tmps:
        if not os.path.isfile(stale):
            continue
        try:
            os.remove(stale)
            removed.append(stale)
        except OSError as e:
            log.warning(f"Could not remove old calibration buffer dump {stale}: {e}")
    return removed


def _stamped_path(out_dir: Path, now: datetime) -> Path:
    return out_dir / f'{DUMP_PREFIX}{now.strftime(STAMP_FORMAT)}{DUMP_SUFFIX}'


def dump_buffer(frames: List[dict], model, lat: float, lon: float, out_dir,
                now: Optional[datetime] = None) -> Path:
    """Write a stamped dump into ``out_dir`` and keep only the newest MAX_DUMPS."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    now = now if now is not None else datetime.now().astimezone()
    path = write_dump(frames, model, lat, lon, _stamped_path(out_dir, now), now)
    prune_dumps(out_dir)
    return path


# ---------------------------------------------------------------------------
# Triggers (wired into CalibrationService)
# ---------------------------------------------------------------------------

def should_dump_on_escape() -> bool:
    """Every basin escape is dumped in a dev build only: a production rig
    dumps once, when escapes are exhausted, which is the buffer the plan
    wants to replay; a developer wants each escape's input."""
    return is_dev_mode_available()


class BufferDumpTrigger:
    """Owns when and how the service's buffer is dumped.

    ``snapshot`` is called under ``lock`` and returns
    ``(frames, model, lat, lon)`` straight from the service's private state;
    the list is copied before the lock is released. Automatic triggers
    serialise on a daemon thread; `dump_now` (the UI button) writes inline
    and returns the path. Nothing here raises into the caller.
    """

    def __init__(self, lock, snapshot: Callable[[], tuple], out_dir=None) -> None:
        self._lock = lock
        self._snapshot = snapshot
        self._out_dir = out_dir
        self._io_lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None

    def escape_started(self) -> None:
        if should_dump_on_escape():
            self._dump_async('basin escape')

    def exhaustion_reached(self) -> None:
        """The service reached escape exhaustion — its one-shot warning site."""
        self._dump_async('escape exhaustion')

    def dump_now(self) -> Optional[Path]:
        """Write the buffer now; None when it is empty or the write failed."""
        return self._dump('on demand', self._take())

    def wait(self, timeout: float = 5.0) -> None:
        """Block until the in-flight automatic dump has finished (tests)."""
        thread = self._thread
        if thread is not None:
            thread.join(timeout)

    # -- internals -----------------------------------------------------------

    def _take(self) -> tuple:
        with self._lock:
            frames, model, lat, lon = self._snapshot()
            return list(frames), model, lat, lon

    def _dump_async(self, reason: str) -> None:
        snapshot = self._take()
        self._thread = threading.Thread(
            target=self._dump, args=(reason, snapshot),
            name='allsky-buffer-dump', daemon=True)
        self._thread.start()

    def _dump(self, reason: str, snapshot: tuple) -> Optional[Path]:
        frames, model, lat, lon = snapshot
        if not frames:
            log.info(f"Calibration buffer dump skipped ({reason}): buffer is empty")
            return None
        try:
            with self._io_lock:
                path = dump_buffer(frames, model, lat, lon,
                                   self._out_dir or app_config.get_allsky_buffer_dir())
        except Exception as e:
            log.warning(f"Calibration buffer dump failed ({reason}): {e}")
            return None
        log.info(f"Calibration buffer dumped ({reason}): {len(frames)} frame(s) "
                 f"-> {path}")
        return path
