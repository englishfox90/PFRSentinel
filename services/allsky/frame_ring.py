"""
Long-baseline frame ring for the calibration service.

The rolling 60-frame buffer is FIFO by count with no notion of time, so on
a rig capturing every 30 s it spans half an hour, and after a day gap it
happily fits dawn frames together with the evening's (H14: an 825-min
"span" on the reporter's rig, 1069 min on the reference rig). Two things
need the opposite: the rotation-pole fit (pole_from_rotation) wants frames
hours apart within ONE night, because the axis sharpens with the arc, and
the orientation search wants frames a wrong orientation cannot line up.

The ring keeps one frame per LONG_RING_SPACING_MIN, up to LONG_RING_LEN
(six hours), and starts over when a frame arrives more than NIGHT_GAP_HOURS
after the previous entry, or earlier than it (a clock step). It stores the
same detection-only frame dicts the buffer does — no images.

Thread-safe: offer() runs on the image-processor worker thread, frames()
on the GUI thread when a refinement is launched.
"""
import threading
from typing import List, Optional

# One frame every ten minutes: a star 200 px from the pole moves ~9 px
# between entries, more than any match tolerance, so consecutive entries
# are independent evidence for the rotation fit; a 30 s cadence would fill
# the ring with near-duplicates in 18 minutes.
LONG_RING_SPACING_MIN = 10.0

# 36 entries × 10 min = six hours, a full dark night in summer, most of one
# in winter; the rotation fit's precision saturates well before that.
LONG_RING_LEN = 36

# A gap longer than this is a day (the daytime suppression stops feeds
# for 10–14 h); the frames on either side of it are different nights and
# must never be fitted together (H14).
NIGHT_GAP_HOURS = 6.0


class FrameRing:
    def __init__(self, spacing_min: float = LONG_RING_SPACING_MIN,
                 maxlen: int = LONG_RING_LEN,
                 night_gap_hours: float = NIGHT_GAP_HOURS):
        self._spacing_s = float(spacing_min) * 60.0
        self._maxlen = int(maxlen)
        self._gap_s = float(night_gap_hours) * 3600.0
        self._lock = threading.Lock()
        self._frames: List[dict] = []

    def offer(self, frame: Optional[dict]) -> bool:
        """Keep `frame` if it is due; True when it was kept."""
        if not frame or frame.get('dt') is None:
            return False
        dt = frame['dt']
        with self._lock:
            if self._frames:
                gap = (dt - self._frames[-1]['dt']).total_seconds()
                if gap < 0 or gap > self._gap_s:
                    self._frames.clear()
                elif gap < self._spacing_s:
                    return False
            self._frames.append(frame)
            if len(self._frames) > self._maxlen:
                del self._frames[0]
            return True

    def frames(self) -> List[dict]:
        """Snapshot, oldest first (the dicts are shared, never mutated)."""
        with self._lock:
            return list(self._frames)

    def span_minutes(self) -> float:
        with self._lock:
            if len(self._frames) < 2:
                return 0.0
            return (self._frames[-1]['dt'] - self._frames[0]['dt']).total_seconds() / 60.0

    def clear(self) -> None:
        with self._lock:
            self._frames.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._frames)
