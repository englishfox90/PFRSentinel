"""
Frame-to-frame stability for all-sky overlay labels.

Every frame is rendered from scratch: star detection -> sky mask -> top-N
ranking -> label placement. Each stage is noisy at its margin, and the fixed
label budget turns one marginal flip into two label changes: an object that
drops out of the mask frees a slot, the next-ranked object takes it, and both
swap back a frame later (issues #31, #13). This module holds the small amount
of state that lets consecutive frames agree:

  * ``SkyMaskHistory`` — majority vote over the last few detection masks, plus
    a hold-over for frames that produce no usable mask (too few detections),
    so a single noisy frame cannot flip a region between sky and obstruction.
  * ``StickySelection`` — objects already on screen keep their slot while they
    stay visible and within a rank margin of the budget. A newcomer displaces
    an incumbent only when it out-ranks it by more than that margin.
  * ``slot_memory`` — the placement slot a label used last frame is tried
    first, so a new neighbour does not flip it to the other side of its star.

State is keyed by nothing: the renderer serves one live frame stream, and a
recalibration or size change is handled inside each piece.
"""
import threading
from collections import deque
from typing import Deque, Dict, List, Optional, Set

import numpy as np

MASK_VOTE_DEPTH = 3       # frames in the majority vote
MASK_HOLD_FRAMES = 3      # frames to reuse the last vote when detection fails
RANK_MARGIN = 3           # incumbents survive up to this far past the budget


class SkyMaskHistory:
    """Temporal majority vote over per-frame sky-visibility masks."""

    def __init__(self, depth: int = MASK_VOTE_DEPTH,
                 hold_frames: int = MASK_HOLD_FRAMES):
        self._depth = max(1, int(depth))
        self._hold = max(0, int(hold_frames))
        self._frames: Deque[np.ndarray] = deque()
        self._vote: Optional[np.ndarray] = None
        self._misses = 0

    @property
    def depth(self) -> int:
        return len(self._frames)

    def reset(self) -> None:
        self._frames.clear()
        self._vote = None
        self._misses = 0

    def update(self, mask: Optional[np.ndarray]) -> Optional[np.ndarray]:
        """Fold this frame's mask in and return the smoothed mask.

        ``mask`` is a 0/255 uint8 array, or None when the frame yielded no
        usable detection mask. A None frame returns the previous vote for up to
        ``hold_frames`` consecutive misses, then None so the caller can fall
        back; the history is cleared at that point because a fresh stretch of
        frames should not be out-voted by stale ones.
        """
        if mask is None:
            self._misses += 1
            if self._vote is not None and self._misses <= self._hold:
                return self._vote
            self.reset()
            return None

        sky = np.asarray(mask) > 0
        if self._frames and self._frames[0].shape != sky.shape:
            self.reset()
        self._misses = 0
        self._frames.append(sky)
        while len(self._frames) > self._depth:
            self._frames.popleft()

        n = len(self._frames)
        votes = np.zeros(sky.shape, dtype=np.uint8)
        for f in self._frames:
            votes += f
        # "Sky in at least half the frames, rounding up": one frame is itself,
        # two frames is either, three frames needs two.
        self._vote = np.where(votes * 2 >= n, 255, 0).astype(np.uint8)
        return self._vote


class StickySelection:
    """Top-N selection with hysteresis for objects already on screen."""

    def __init__(self, rank_margin: int = RANK_MARGIN):
        self._margin = max(0, int(rank_margin))
        self._shown: Set[str] = set()

    @property
    def shown(self) -> Set[str]:
        return set(self._shown)

    def reset(self) -> None:
        self._shown.clear()

    def select(self, ranked: List[str], top_n: int) -> Set[str]:
        """Pick up to ``top_n`` UIDs from ``ranked`` (brightest first).

        An incumbent stays eligible while its rank is under ``top_n + margin``
        and competes with a ``margin``-rank bonus, so a newcomer has to
        out-rank it by more than the margin to take its slot. Objects absent
        from ``ranked`` (invisible this frame) drop out immediately; the mask
        vote upstream is what stops a single frame from doing that.
        """
        if top_n <= 0:
            self._shown = set(ranked)
            return set(ranked)

        entries = []
        for rank, uid in enumerate(ranked):
            if uid in self._shown:
                if rank < top_n + self._margin:
                    entries.append((rank - self._margin, rank, uid))
            elif rank < top_n:
                entries.append((rank, rank, uid))
        entries.sort()
        chosen = {uid for _, _, uid in entries[:top_n]}
        self._shown = chosen
        return set(chosen)


class LabelStabilizer:
    """All per-stream label state, guarded for the one rendering thread."""

    def __init__(self):
        self.masks = SkyMaskHistory()
        self.selection = StickySelection()
        self.slot_memory: Dict[str, int] = {}
        self._lock = threading.Lock()

    def smooth_mask(self, mask: Optional[np.ndarray]) -> Optional[np.ndarray]:
        with self._lock:
            return self.masks.update(mask)

    def select(self, ranked: List[str], top_n: int) -> Set[str]:
        with self._lock:
            return self.selection.select(ranked, top_n)

    def reset(self) -> None:
        with self._lock:
            self.masks.reset()
            self.selection.reset()
            self.slot_memory.clear()


_stabilizer = LabelStabilizer()


def get_label_stabilizer() -> LabelStabilizer:
    """The process-wide stabilizer shared by every overlay render."""
    return _stabilizer


def reset_label_stability() -> None:
    """Forget all frame-to-frame state (tests, and a new capture session)."""
    _stabilizer.reset()
