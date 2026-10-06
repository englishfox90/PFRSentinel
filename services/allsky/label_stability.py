"""
Frame-to-frame stability for all-sky overlay labels.

Every frame is rendered from scratch: star detection -> sky mask -> top-N
ranking -> label placement. Each stage is noisy at its margin, and the fixed
label budget turns one marginal flip into two label changes: an object that
drops out of the mask frees a slot, the next-ranked object takes it, and both
swap back a frame later (issues #31, #13). This module holds the small amount
of state that lets consecutive frames agree:

  * ``SkyMaskHistory`` — majority vote over recent detection masks, plus a
    hold-over for frames that produce no usable mask (too few detections), so
    neither a noisy frame nor a run of them — an exposure change lasts many
    frames, not one — can flip a region between sky and obstruction, with a
    lower threshold to stay sky than to become it. The vote
    is never thrown away for lack of frames: after the hold it is *stale*,
    and the caller (``sky_region``) lets the persisted equipment map take
    over until real detections return.
  * ``StickySelection`` — objects already on screen keep their slot while they
    stay visible and within a rank margin of the budget. A newcomer displaces
    an incumbent only when it out-ranks it by more than that margin.
  * ``slot_memory`` — the placement slot a label used last frame is tried
    first, so a new neighbour does not flip it to the other side of its star.
  * ``sightings`` (``star_sightings.StarSightings``) — a star detected at
    its own predicted pixel keeps its label whatever the vote says, held a
    few frames past the last sighting.
  * ``persistence`` (``label_persistence.LabelPersistence``) — the drawn
    labels themselves: two eligible frames before a first draw, a hold and
    a fade after the last one.

State is keyed by nothing: the renderer serves one live frame stream, and a
recalibration or size change is handled inside each piece.
"""
import math
import threading
from collections import deque
from typing import Deque, Dict, List, Optional, Set, Tuple

import numpy as np

from .label_persistence import LABEL_HOLD_FRAMES, LabelPersistence
from .star_sightings import StarSightings

# What counts as open sky is a property of the installation — the pier, the
# walls, the scopes — and changes slowly. A 3-frame vote absorbed a single
# noisy frame but simply followed anything longer: when auto-exposure steps,
# or dusk fades, the per-frame mask shrinks for as long as the change lasts,
# and the labels over the lost sky went with it (discussion #76). 15 frames is
# ~7 minutes at 30 s exposures: long enough to ride out an exposure ramp, short
# enough that a telescope slewing across the field is followed rather than
# labelled over for the rest of the night.
MASK_VOTE_DEPTH = 15      # frames in the majority vote
MASK_HOLD_FRAMES = 15     # frames to reuse the last vote when detection fails
RANK_MARGIN = 3           # incumbents survive up to this far past the budget

# A pixel becomes sky at half the frames that could judge it, but once sky it
# stays sky until fewer than a quarter of them say so. With one threshold, a
# pixel whose detection discs come and go — the gaps between stars, the edge
# of the open sky — sat on the line and flipped every frame or two, and the
# label over it with it. Discussion #105's 2026-09-30 calibration buffer (48
# frames) replayed through the vote: 111 label visibility flips, 16 of 70
# named stars flipping three times or more, often on alternate frames; with
# the keep fraction 65 and 7, and the flips left are runs, not alternation.
# Two thresholds make it a Schmitt trigger: a lasting obstruction still
# clears the label, a few frames later than before.
MASK_KEEP_FRACTION = 0.25

# An object on screen that drops out of the visible set keeps its slot in
# the top-N budget for this many frames, so the next-ranked object does not
# flash into its slot while the drop lasts and flash out again when it
# returns. It is as long as the drawn-label hold (label_persistence) because
# the label is still on screen, fading, for that long: a shorter slot hold
# let a newcomer appear beside it and pushed the drawn count past the budget
# (issue #144). A lasting drop frees the slot after the hold.
ABSENT_HOLD_FRAMES = LABEL_HOLD_FRAMES

# The vote is kept at reduced resolution. Fifteen full-resolution masks of a
# 3552 px frame are ~190 MB; the mask is made of circles no smaller than 15 px
# in radius, so nothing a label test can see is lost at this size. The
# equipment map (obstruction_map) shares this grid, so the two combine
# without resampling.
MASK_VOTE_MAX_EDGE = 512


def vote_grid(full_shape) -> Tuple[int, Tuple[int, int]]:
    """(step, (rows, cols)) of the reduced grid for a full-resolution shape."""
    h, w = int(full_shape[0]), int(full_shape[1])
    step = max(1, math.ceil(max(h, w) / MASK_VOTE_MAX_EDGE))
    return step, ((h + step - 1) // step, (w + step - 1) // step)


def to_grid(plane, full_shape) -> np.ndarray:
    """A plane on the grid passes through; one at full resolution is strided
    down (a view, no copy). Anything else is a caller error."""
    step, small = vote_grid(full_shape)
    arr = np.asarray(plane)
    if arr.shape[:2] == small:
        return arr
    if arr.shape[:2] == (int(full_shape[0]), int(full_shape[1])):
        return arr[::step, ::step]
    raise ValueError(f"plane {arr.shape} is neither full {tuple(full_shape[:2])} "
                     f"nor grid {small}")


def upsample(small: np.ndarray, full_shape) -> np.ndarray:
    """Nearest-neighbour back to full resolution."""
    step, _ = vote_grid(full_shape)
    h, w = int(full_shape[0]), int(full_shape[1])
    if step == 1:
        return small
    return np.repeat(np.repeat(small, step, axis=0), step, axis=1)[:h, :w]


class SkyMaskHistory:
    """Temporal majority vote over per-frame sky-visibility masks.

    Each frame contributes two grid planes: where it saw sky, and where it
    was in a position to say anything at all (``counted``). A full frame
    counts everywhere outside the Moon's glare; a sparse frame
    (``add_partial``) counts only inside its own detection discs, so it can
    add sky but never take it away. A pixel no frame could judge is left to
    the equipment map and reads as sky here.
    """

    def __init__(self, depth: int = MASK_VOTE_DEPTH,
                 hold_frames: int = MASK_HOLD_FRAMES,
                 keep_fraction: float = MASK_KEEP_FRACTION):
        self._depth = max(1, min(255, int(depth)))   # votes are summed in uint8
        self._hold = max(0, int(hold_frames))
        # Integer form of the keep threshold: sky stays sky while
        # votes * _keep_den >= counts * _keep_num. 0.5 is the single threshold.
        keep = min(0.5, max(0.0, float(keep_fraction)))
        self._keep_num, self._keep_den = round(keep * 100), 100
        self._frames: Deque[tuple] = deque()         # (sky, counted), on the grid
        self._votes: Optional[np.ndarray] = None     # running sum of sky
        self._counts: Optional[np.ndarray] = None    # running sum of counted
        self._shape: Optional[tuple] = None          # full-resolution shape
        self._small: Optional[np.ndarray] = None     # vote on the grid, 0/255
        self._full: Optional[np.ndarray] = None      # materialised on demand
        self._misses = 0

    @property
    def depth(self) -> int:
        return len(self._frames)

    @property
    def small_vote(self) -> Optional[np.ndarray]:
        """The vote on the reduced grid (0/255 uint8), fresh or stale."""
        return self._small

    @property
    def vote(self) -> Optional[np.ndarray]:
        """The vote at full resolution. Built on first use after a fold and
        held until the next one, so a run of held frames returns one array.
        The renderer works on the grid and never asks for this."""
        if self._small is None:
            return None
        if self._full is None:
            self._full = upsample(self._small, self._shape)
        return self._full

    @property
    def is_stale(self) -> bool:
        """True when the vote has outlived its hold with no new evidence.

        The vote is still returned — it is the best per-night knowledge there
        is — but the caller should prefer the equipment map where one exists
        (discussion #76: a cloudy hour used to wipe the vote and hand the
        labels to a raw-brightness test that passed lit equipment).
        """
        return self._small is not None and self._misses > self._hold

    def reset(self) -> None:
        self._clear_frames()
        self._shape = None
        self._small = None
        self._full = None
        self._misses = 0

    def _clear_frames(self) -> None:
        self._frames.clear()
        self._votes = None
        self._counts = None

    def update(self, mask: Optional[np.ndarray],
               exclude: Optional[np.ndarray] = None,
               full_shape: Optional[tuple] = None) -> Optional[np.ndarray]:
        """Fold this frame's mask in and return the smoothed mask.

        ``mask`` is a 0/255 uint8 array, or None when the frame yielded no
        usable detection mask. A None frame returns the previous vote; after
        ``hold_frames`` consecutive misses that vote is ``is_stale`` but still
        returned. The first real mask after a stale run replaces the history
        rather than being out-voted by it.

        ``exclude`` (bool) marks pixels this frame cannot judge — the Moon's
        glare disc, where the glare itself blanks the detections — and they
        are left out of the frame count there. Planes are full resolution,
        or on the grid for ``full_shape`` when that is given.
        """
        if mask is None:
            self.note_miss()
        else:
            self.fold_grid(mask, exclude=exclude, full_shape=full_shape)
        return self.vote

    def add_partial(self, mask: np.ndarray,
                    exclude: Optional[np.ndarray] = None,
                    full_shape: Optional[tuple] = None) -> Optional[np.ndarray]:
        """Fold a sparse frame in: it votes sky inside its discs and abstains
        elsewhere. Three to nine detections on a moonlit or hazy frame are
        real sky, but too few to say where the sky is *not*."""
        self.fold_grid(mask, partial=True, exclude=exclude, full_shape=full_shape)
        return self.vote

    def note_miss(self) -> None:
        """A frame with no usable mask: the vote is held, then goes stale."""
        self._misses += 1

    def fold_grid(self, mask: np.ndarray, *, partial: bool = False,
                  exclude: Optional[np.ndarray] = None,
                  full_shape: Optional[tuple] = None) -> None:
        """Advance the vote without materialising the full-resolution result:
        the production path, which works on the grid throughout."""
        counted = (np.asarray(mask) > 0) if partial else None
        full_shape = tuple(full_shape[:2]) if full_shape else np.asarray(mask).shape[:2]
        if self._shape is not None and self._shape != full_shape:
            self.reset()
        elif self.is_stale:
            self._clear_frames()
        self._shape = full_shape
        # What was sky before this frame, for the keep threshold. A history
        # just started or restarted after a stale run has no standing sky:
        # the stale vote is not evidence the new frames must out-vote.
        previous = (self._small > 0) if (self._small is not None
                                         and self._votes is not None) else None
        sky = np.ascontiguousarray(to_grid(mask, full_shape) > 0)
        if counted is None:
            seen = np.ones(sky.shape, dtype=bool)
        else:
            seen = np.ascontiguousarray(to_grid(counted, full_shape) > 0)
        if exclude is not None:
            seen &= ~(to_grid(exclude, full_shape) > 0)
        sky &= seen

        self._misses = 0
        if self._votes is None:
            self._votes = np.zeros(sky.shape, dtype=np.uint8)
            self._counts = np.zeros(sky.shape, dtype=np.uint8)
        self._frames.append((sky, seen))
        self._votes += sky
        self._counts += seen
        while len(self._frames) > self._depth:
            old_sky, old_seen = self._frames.popleft()
            self._votes -= old_sky
            self._counts -= old_seen

        # "Sky in at least half the frames that could tell, rounding up": one
        # frame is itself, two frames is either, three frames needs two. A
        # pixel no frame could judge is sky here; the equipment map decides.
        # Already sky, it stays so down to the keep fraction (MASK_KEEP_FRACTION).
        votes = self._votes.astype(np.uint16)
        counts = self._counts.astype(np.uint16)
        sky_now = votes * 2 >= counts
        if previous is not None and previous.shape == sky_now.shape:
            sky_now |= previous & (votes * self._keep_den >= counts * self._keep_num)
        self._small = np.where(sky_now, 255, 0).astype(np.uint8)
        self._full = None


class StickySelection:
    """Top-N selection with hysteresis for objects already on screen."""

    def __init__(self, rank_margin: int = RANK_MARGIN,
                 absent_hold: int = ABSENT_HOLD_FRAMES):
        self._margin = max(0, int(rank_margin))
        self._absent_hold = max(0, int(absent_hold))
        self._shown: Set[str] = set()
        self._absent: Dict[str, int] = {}   # held incumbent -> frames missing

    @property
    def shown(self) -> Set[str]:
        return set(self._shown)

    def reset(self) -> None:
        self._shown.clear()
        self._absent.clear()

    def peek(self, ranked: List[str], top_n: int) -> Set[str]:
        """What ``select`` would return, without changing any state."""
        probe = StickySelection(self._margin, self._absent_hold)
        probe._shown, probe._absent = set(self._shown), dict(self._absent)
        return probe.select(ranked, top_n)

    def select(self, ranked: List[str], top_n: int) -> Set[str]:
        """Pick up to ``top_n`` UIDs from ``ranked`` (brightest first).

        An incumbent stays eligible while its rank is under ``top_n + margin``
        and competes with a ``margin``-rank bonus, so a newcomer has to
        out-rank it by more than the margin to take its slot. An incumbent
        absent from ``ranked`` (invisible this frame) keeps its slot for
        ``absent_hold`` frames (ABSENT_HOLD_FRAMES) and is returned with the
        chosen set — whether it is drawn meanwhile is the drawn-label
        persistence's call (label_persistence) — then drops out.
        """
        if top_n <= 0:
            self._shown = set(ranked)
            self._absent.clear()
            return set(ranked)

        present = set(ranked)
        held = []
        absent: Dict[str, int] = {}
        for uid in self._shown:
            if uid in present:
                continue
            missing = self._absent.get(uid, 0) + 1
            if missing <= self._absent_hold:
                held.append(uid)
                absent[uid] = missing

        held = sorted(held)[:top_n]
        absent = {uid: absent[uid] for uid in held}

        entries = []
        for rank, uid in enumerate(ranked):
            if uid in self._shown:
                if rank < top_n + self._margin:
                    entries.append((rank - self._margin, rank, uid))
            elif rank < top_n:
                entries.append((rank, rank, uid))
        entries.sort()
        free = max(0, top_n - len(held))
        chosen = {uid for _, _, uid in entries[:free]} | set(held)
        self._shown = chosen
        self._absent = absent
        return set(chosen)


class LabelStabilizer:
    """All per-stream label state, guarded for the one rendering thread."""

    def __init__(self):
        self.masks = SkyMaskHistory()
        self.selection = StickySelection()
        self.sightings = StarSightings()
        self.persistence = LabelPersistence()
        self.slot_memory: Dict[str, int] = {}
        self._lock = threading.Lock()

    def smooth_mask(self, mask: Optional[np.ndarray]) -> Optional[np.ndarray]:
        with self._lock:
            return self.masks.update(mask)

    def fold_mask(self, mask: Optional[np.ndarray], full_shape: tuple,
                  exclude: Optional[np.ndarray] = None, partial: bool = False) -> None:
        """Advance the vote with grid planes; nothing is materialised."""
        with self._lock:
            if mask is None:
                self.masks.note_miss()
            else:
                self.masks.fold_grid(mask, partial=partial, exclude=exclude,
                                     full_shape=full_shape)

    def current_small_vote(self) -> tuple:
        """(vote on the grid, is_stale) without advancing the vote — a
        reprocess of the same capture must not count the frame twice."""
        with self._lock:
            return self.masks.small_vote, self.masks.is_stale

    def select(self, ranked: List[str], top_n: int, advance: bool = True) -> Set[str]:
        """The top-N pick; a reprocess (``advance`` False) reads it without
        counting the frame again."""
        with self._lock:
            if advance:
                return self.selection.select(ranked, top_n)
            return self.selection.peek(ranked, top_n)

    def drawn_labels(self, eligible, advance: bool = True,
                     budget: Optional[Set[str]] = None) -> Dict[str, float]:
        """``{uid: alpha}`` of the labels to draw this frame from the UIDs
        the frame itself would label and the top-N pick (``budget``).
        ``advance`` is False for a reprocess of a capture already counted,
        which reads without advancing."""
        if advance:
            return self.persistence.update(eligible, budget)
        return self.persistence.current(eligible)

    def reset(self) -> None:
        with self._lock:
            self.masks.reset()
            self.selection.reset()
            self.slot_memory.clear()
        self.sightings.reset()
        self.persistence.reset()


_stabilizer = LabelStabilizer()


def get_label_stabilizer() -> LabelStabilizer:
    """The process-wide stabilizer shared by every overlay render."""
    return _stabilizer


def forget_drawn_labels() -> None:
    """Forget only the drawn-label hold: a frame the observing gate
    withheld must not leave labels to be carried over to the next one."""
    _stabilizer.persistence.reset()


def reset_label_stability() -> None:
    """Forget all frame-to-frame state: a new capture session, a new or
    cleared calibration model (the projection every label rests on), tests."""
    _stabilizer.reset()
