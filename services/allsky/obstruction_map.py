"""
Equipment map for the all-sky overlay: where, in the output frame, the sky is
not (issue #93, package 1).

The per-night sky-mask vote (``label_stability.SkyMaskHistory``) knows only
the last fifteen frames and starts from nothing every session. On a hosting
field, half the disc is telescopes and mounts; a cloudy or moonlit spell
starves the vote and the labels either vanished wholesale or, once the
renderer fell back to raw brightness, landed on lit equipment. This module
keeps the slow knowledge: a per-pixel probability that a pixel is open sky,
learned over hundreds of frames, saved between sessions, and healed slowly
as scopes move.

The map is a *prior*; the vote is the *evidence*. ``sky_region`` combines
them. The map removes places, never features: a pixel it has no evidence
about reads as sky, so the map can only ever take labels off equipment it has
actually watched stay dark.

Resource budget (plan §8): the map lives on the vote's reduced grid (≤ 512 px
longest edge, one float32 plane ≈ 1 MB), every update is an in-place numpy
op on preallocated planes, and nothing full-resolution is built unless a
caller asks for ``sky_mask_for`` — which is cached and rebuilt only when the
thresholded map actually changed.

Thread safety: one lock around every read and write. The renderer updates
from the image-processor thread; the controller loads, saves and resets from
the GUI thread.
"""
import os
import threading
import time
from typing import Optional, Tuple

import numpy as np

from services.logger import app_logger as log

from .label_stability import MASK_VOTE_MAX_EDGE, to_grid, upsample, vote_grid

# Exponential-average time constant, in frames with evidence. 600 frames is
# about five hours of 30 s exposures, so a telescope parked in a new place is
# half-learned in one night (600·ln 2 ≈ 416 frames) and a scope that moved
# away is forgotten over about two nights. Shorter, and one moonlit night
# would rewrite the map; longer, and a re-arranged rig would carry stale
# equipment for a week. Maintainer's decision, plan §5.4.
HEAL_FRAMES = 600

# The map starts as a plain average and becomes an exponential one: until a
# pixel has seen HEAL_FRAMES frames its value is the mean of the evidence so
# far. A pure EMA from 0.5 lets the FIRST frame decide which side of the
# threshold a pixel sits on (0.4992 vs 0.5008) and then takes hundreds of
# frames to change its mind; the running mean makes the first night's map
# the majority of the first night's frames, which is what the fifteen-frame
# vote already says. Once the count reaches HEAL_FRAMES the two updates are
# the same.

# A frame's per-frame mask must rest on at least this many detections before
# it teaches the map anything: the renderer's own floor for a usable mask.
# Below it (thin cloud, a bright Moon) the discs are few and wide and say
# more about the detector than the sky.
POSITIVE_MIN_DETECTIONS = 10

# Negative evidence — "no star was seen here" — needs a frame that saw plenty
# of stars elsewhere, or a cloudy frame teaches the map that the sky is
# equipment. 40 is about the count on a clear moonlit frame of the reporter's
# rig (§0.3) and well under a clear dark frame's 100–200.
NEGATIVE_MIN_DETECTIONS = 40

# A pixel is sky when its probability is at least this. 0.5 is also the
# starting value, so an unwatched pixel is sky: the map removes places it has
# evidence about and never invents an obstruction.
SKY_THRESHOLD = 0.5
UNKNOWN = 0.5

# Longest edge of the stored plane: the vote's grid, so the two combine
# without resampling. The evidence is made of discs no smaller than 15 px in
# radius, so nothing a label test can see is lost.
MAP_MAX_EDGE = MASK_VOTE_MAX_EDGE

# Saves are throttled: the map moves by at most 1/HEAL_FRAMES per frame, so
# ten minutes of frames is a fraction of a percent of change, and a session
# that crashes loses nothing that matters. Capture stop saves regardless.
SAVE_INTERVAL_S = 600.0

# On-disk format version. Bumped when the arrays or stamps change meaning;
# an old file is discarded, never reinterpreted (a map is cheap to relearn,
# a wrong one costs a night of labels).
FORMAT_VERSION = 1

Stamp = Tuple[int, int, Optional[Tuple[int, ...]]]   # (width, height, crop)


class ObstructionMap:
    """Per-pixel sky probability on the reduced grid, stamped with the
    output frame size and crop it was learned for."""

    def __init__(self, heal_frames: int = HEAL_FRAMES):
        self._heal = max(1, int(heal_frames))
        self._lock = threading.Lock()
        # Writers are serialised separately from the state lock: the watch
        # path renders on a two-worker pool and the GUI thread saves at
        # capture stop, so two saves can overlap. Each writes a temp file
        # and os.replace()s it, so the file on disk is always a whole one.
        self._save_lock = threading.Lock()
        self._plane: Optional[np.ndarray] = None    # float32 grid
        self._counts: Optional[np.ndarray] = None   # uint32 grid, evidence frames
        self._alpha: Optional[np.ndarray] = None    # float32 scratch planes,
        self._delta: Optional[np.ndarray] = None    # preallocated with the map
        self._stamp: Optional[Stamp] = None
        self._frames_seen = 0
        # Change generation: bumped by every update/reset, recorded by save,
        # so an update that lands during a write is not marked as saved.
        self._generation = 0
        self._saved_generation = 0
        self._last_save_t = -float('inf')
        # sky_mask_for cache: the grid mask it was built from and the result.
        self._cache_small: Optional[np.ndarray] = None
        self._cache_full: Optional[np.ndarray] = None
        self._cache_stamp: Optional[Stamp] = None

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    @property
    def frames_seen(self) -> int:
        with self._lock:
            return self._frames_seen

    @property
    def stamp(self) -> Optional[Stamp]:
        with self._lock:
            return self._stamp

    @property
    def is_known(self) -> bool:
        """True once at least one frame has taught the map something."""
        with self._lock:
            return self._plane is not None and self._frames_seen > 0

    def small_sky_mask(self, width: int, height: int,
                       crop: Optional[Tuple[int, ...]] = None) -> Optional[np.ndarray]:
        """Bool plane on the grid, True where the map says sky, or None when
        the map is unknown or was learned for another frame size or crop."""
        with self._lock:
            return self._small_locked(_stamp(width, height, crop))

    def _small_locked(self, stamp: Stamp) -> Optional[np.ndarray]:
        if self._plane is None or self._frames_seen == 0 or self._stamp != stamp:
            return None
        return self._plane >= SKY_THRESHOLD

    def sky_mask_for(self, width: int, height: int,
                     crop: Optional[Tuple[int, ...]] = None) -> Optional[np.ndarray]:
        """Full-resolution bool plane, True where the map says sky.

        None when the map has learned nothing yet or was learned for a
        different frame size or ``crop`` (None = uncropped) — it is never
        rescaled, because a resize or a moved crop puts the equipment at
        different pixels. Nearest-neighbour upsample, matching the vote;
        rebuilt only when the thresholded map changed since the last call.

        The returned array is SHARED and READ-ONLY: the same object comes
        back until the map changes, so a caller must never write into it.
        Interface contract for the calibration pool (package 4): keep this
        name and signature.
        """
        stamp = _stamp(width, height, crop)
        with self._lock:
            small = self._small_locked(stamp)
            if small is None:
                return None
            if (self._cache_full is not None and self._cache_stamp == stamp
                    and np.array_equal(small, self._cache_small)):
                return self._cache_full
            full = upsample(small, (stamp[1], stamp[0]))
            full.flags.writeable = False
            self._cache_small, self._cache_full, self._cache_stamp = small, full, stamp
            return full

    def sky_probability(self) -> Optional[np.ndarray]:
        """Copy of the grid plane (diagnostics, dev tools)."""
        with self._lock:
            return None if self._plane is None else self._plane.copy()

    # ------------------------------------------------------------------
    # Learn
    # ------------------------------------------------------------------

    def update(
        self,
        sky_mask: Optional[np.ndarray],
        *,
        n_detections: int,
        frame_is_observable: bool,
        full_shape: Optional[tuple] = None,
        reach_mask: Optional[np.ndarray] = None,
        sky_region: Optional[np.ndarray] = None,
        exclude: Optional[np.ndarray] = None,
        crop: Optional[Tuple[int, ...]] = None,
    ) -> bool:
        """Fold one frame's evidence in. Returns True when the map changed.

        ``sky_mask``: detection discs (non-zero = sky seen).
        ``reach_mask``: the same discs at twice the radius; a pixel of
        ``sky_region`` outside it, on a frame with at least
        NEGATIVE_MIN_DETECTIONS detections, is evidence of equipment.
        ``exclude`` (the Moon's glare disc) is exempt from that: glare blanks
        detections without the pixel being equipment.
        ``frame_is_observable``: the observable-sky gate's verdict. False means
        the frame teaches nothing — a lit roof or a daytime frame must not
        write equipment over the whole sky.
        ``full_shape``: (height, width) of the output frame. Planes are then
        either at that resolution (strided down, no copy) or already on its
        grid. Without it every plane is taken as full resolution.
        ``crop``: the OUTPUT_CROP stamp; a different crop or frame size
        discards the map rather than rescaling it.
        """
        if not frame_is_observable or sky_mask is None:
            return False
        if n_detections < POSITIVE_MIN_DETECTIONS:
            return False
        shape = tuple(full_shape[:2]) if full_shape else np.asarray(sky_mask).shape[:2]
        h, w = int(shape[0]), int(shape[1])
        stamp = _stamp(w, h, crop)

        positive = to_grid(sky_mask, shape) > 0
        negative = None
        if n_detections >= NEGATIVE_MIN_DETECTIONS and sky_region is not None:
            negative = to_grid(sky_region, shape) > 0
            if reach_mask is not None:
                negative &= ~(to_grid(reach_mask, shape) > 0)
            if exclude is not None:
                negative &= ~(to_grid(exclude, shape) > 0)
            negative &= ~positive

        with self._lock:
            if self._plane is None or self._stamp != stamp:
                self._start_locked(stamp, positive.shape)
            self._fold_locked(positive, 1.0)
            if negative is not None:
                self._fold_locked(negative, 0.0)
            self._frames_seen += 1
            self._generation += 1
        return True

    def _start_locked(self, stamp: Stamp, grid_shape) -> None:
        if self._plane is not None:
            log.info(
                f"allsky equipment map: frame changed from "
                f"{self._stamp[0]}x{self._stamp[1]} crop={self._stamp[2]} to "
                f"{stamp[0]}x{stamp[1]} crop={stamp[2]}; starting a new map "
                f"({self._frames_seen} frames discarded)")
        self._plane = np.full(grid_shape, UNKNOWN, dtype=np.float32)
        self._counts = np.zeros(grid_shape, dtype=np.uint32)
        self._alpha = np.empty(grid_shape, dtype=np.float32)
        self._delta = np.empty(grid_shape, dtype=np.float32)
        self._stamp = stamp
        self._frames_seen = 0

    def _fold_locked(self, where: np.ndarray, target: float) -> None:
        """Running mean up to HEAL_FRAMES frames of evidence, EMA after —
        in place on the preallocated planes, no temporaries."""
        alpha, delta = self._alpha, self._delta
        np.add(self._counts, 1, out=alpha, casting='unsafe')   # frames incl. this
        np.minimum(alpha, float(self._heal), out=alpha)        # capped at HEAL
        np.reciprocal(alpha, out=alpha)                        # 1/n, then 1/HEAL
        np.subtract(target, self._plane, out=delta)
        delta *= alpha
        delta *= where                 # zero where no evidence: 30x faster than where=
        self._plane += delta
        self._counts += where
        np.minimum(self._counts, self._heal, out=self._counts)

    def reset(self) -> None:
        with self._lock:
            had = self._frames_seen
            self._plane = self._counts = self._alpha = self._delta = None
            self._stamp = None
            self._frames_seen = 0
            self._generation += 1
            self._cache_small = self._cache_full = self._cache_stamp = None
        if had:
            log.info(f"allsky equipment map reset ({had} frames of evidence forgotten)")

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save(self, path: str, now: Optional[float] = None) -> bool:
        """Write the map to ``path`` (uncompressed npz, ~1.5 MB). An empty map
        removes the file. Returns True on success.

        Atomic: the npz goes to a temp file beside ``path`` and is
        os.replace()d over it, so a crash or an overlapping writer can never
        leave a truncated map for the next start to choke on.
        """
        with self._save_lock:
            with self._lock:
                stamp, frames_seen, generation = self._stamp, self._frames_seen, self._generation
                snapshot = (None if self._plane is None
                            else (self._plane.copy(), self._counts.copy()))
            try:
                if snapshot is None:
                    if os.path.isfile(path):
                        os.remove(path)
                else:
                    self._write_atomic(path, stamp, frames_seen, snapshot)
            except OSError as e:
                log.warning(f"allsky equipment map: could not save {path}: {e}")
                return False
            with self._lock:
                self._saved_generation = generation
                self._last_save_t = float(now if now is not None else time.monotonic())
            return True

    def _write_atomic(self, path, stamp, frames_seen, snapshot) -> None:
        crop = np.array(stamp[2] if stamp[2] else [], dtype=np.int64)
        tmp = f"{path}.{os.getpid()}.tmp"
        try:
            with open(tmp, 'wb') as fh:
                np.savez(
                    fh, version=FORMAT_VERSION, plane=snapshot[0], counts=snapshot[1],
                    width=stamp[0], height=stamp[1], crop=crop,
                    frames_seen=frames_seen, heal_frames=self._heal,
                )
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)

    def _dirty_locked(self) -> bool:
        return self._generation != self._saved_generation

    def maybe_save(self, path: str, now: Optional[float] = None) -> bool:
        """Save when something changed and SAVE_INTERVAL_S has passed."""
        t = float(now if now is not None else time.monotonic())
        with self._lock:
            due = self._dirty_locked() and (t - self._last_save_t) >= SAVE_INTERVAL_S
        return self.save(path, now=t) if due else False

    def save_if_dirty(self, path: str) -> bool:
        with self._lock:
            dirty = self._dirty_locked()
        return self.save(path) if dirty else False

    def load(self, path: str) -> bool:
        """Replace the map with the one at ``path``. Returns True on success.

        A missing file is normal (first run). A malformed, truncated or
        foreign file is logged and ignored and the map starts empty: this
        runs unguarded at startup on a 24/7 box, and a map is cheap to
        relearn — so it catches everything, zipfile.BadZipFile included.
        """
        if not os.path.isfile(path):
            return False
        try:
            with np.load(path) as data:
                if int(data['version']) != FORMAT_VERSION:
                    log.info(f"allsky equipment map: {path} is format "
                             f"{int(data['version'])}, expected {FORMAT_VERSION}; ignored")
                    return False
                plane = np.ascontiguousarray(data['plane'], dtype=np.float32)
                counts = np.ascontiguousarray(data['counts'], dtype=np.uint32)
                width, height = int(data['width']), int(data['height'])
                crop_arr = np.asarray(data['crop']).ravel()
                crop = tuple(int(v) for v in crop_arr) if crop_arr.size else None
                frames_seen = int(data['frames_seen'])
        except Exception as e:
            log.warning(f"allsky equipment map: could not read {path}, starting "
                        f"fresh: {type(e).__name__}: {e}")
            return False
        _, grid_shape = vote_grid((height, width)) if width > 0 and height > 0 else (1, None)
        if plane.ndim != 2 or plane.shape != counts.shape or plane.shape != grid_shape:
            log.warning(f"allsky equipment map: {path} is inconsistent; ignored")
            return False
        with self._lock:
            self._plane, self._counts = plane, counts
            self._alpha = np.empty(plane.shape, dtype=np.float32)
            self._delta = np.empty(plane.shape, dtype=np.float32)
            self._stamp = (width, height, crop)
            self._frames_seen = frames_seen
            self._generation += 1
            self._saved_generation = self._generation
            self._last_save_t = time.monotonic()
            self._cache_small = self._cache_full = self._cache_stamp = None
        log.info(f"allsky equipment map loaded: {width}x{height} crop={crop}, "
                 f"{frames_seen} frames of evidence")
        return True


def _stamp(width, height, crop) -> Stamp:
    return (int(width), int(height), tuple(int(v) for v in crop) if crop else None)


_map = ObstructionMap()


def get_obstruction_map() -> ObstructionMap:
    """The process-wide equipment map, shared by the renderer, the
    calibration pool and the controller — one rig, one map."""
    return _map
