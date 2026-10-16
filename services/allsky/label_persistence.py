"""
Debounce for the labels that are actually drawn (issue #144).

Everything upstream of this decides which objects are *eligible* for a
label on a frame: above the horizon cutoff, on open sky (vote, equipment map,
sightings) and inside the top-N budget. Each of those has its own hysteresis,
but none of them smooths the drawn output itself, so an object whose
eligibility flips for a frame still blinks. This module is the fly-wheel on
the drawn set:

  * a label is drawn only after its object has been eligible on
    ``LABEL_SHOW_FRAMES`` consecutive frames, so a one-frame newcomer never
    flashes on;
  * once drawn it stays drawn, at its predicted pixel, for
    ``LABEL_HOLD_FRAMES`` frames after it stops being eligible, so a short
    drop never blinks it off;
  * every drawn label carries an alpha that ramps up over the first frames
    and down over the end of the hold, multiplied into its layer's opacity;
  * a drawn label whose top-N slot has gone to another object is not held:
    it fades out over ``LABEL_FADE_OUT_FRAMES`` while the newcomer fades in.
    The budget's own absent hold (label_stability) is as long as this hold,
    so a label that is merely out of sight keeps its slot and its full hold;
    only a real swap is cut short, and the drawn count exceeds the budget
    for no more than that crossfade.

A held label is drawn whatever the visibility plane says, the persisted
equipment map included. The reporter of #144 accepted that trade: when a
telescope slews in front of a labelled star, the label stays over the
telescope for the hold (~5 minutes of sky) and fades. Nothing here can draw
on a frame the observing gate suppressed (closed roof, no stars, daylight,
a discredited model): the renderer is never called for those frames, and
``overlay_renderer.render_allsky_for_preview`` forgets the drawn set when it
withholds one, so a label is never carried across the gap.
"""
import threading
from dataclasses import dataclass
from typing import Dict, Iterable, Optional, Set

# Every constant here is in frames, and a timelapse collapses frames: at 30 s
# exposures played back at 24 fps one frame is 30 s of sky but 1/24 s of
# video. The 3-frame holds this replaces were 1.5 minutes of sky and an eighth
# of a second on screen, which reads as flicker. Ten frames is ~5 minutes of
# sky (how long a label can stay over a scope that has just slewed in) and
# ~0.4 s of video — long enough to bridge the drops seen in discussion #105.
LABEL_SHOW_FRAMES = 2       # consecutive eligible frames before a first draw
LABEL_HOLD_FRAMES = 10      # frames a drawn label outlives its eligibility

# The fades soften the edges; the hold is what removes the flicker. Three
# frames in is 1/8 s of video; the last four frames of the hold fade out, so a
# drop of a frame or two is drawn at full strength and never dims.
LABEL_FADE_IN_FRAMES = 3
LABEL_FADE_OUT_FRAMES = 4


@dataclass
class _Track:
    streak: int = 0        # consecutive eligible frames
    alpha: float = 0.0     # 0 = not drawn
    missing: int = 0       # frames since last eligible, while drawn


class LabelPersistence:
    """Per-UID on/off debounce and fade of the drawn labels."""

    def __init__(self, show_frames: int = LABEL_SHOW_FRAMES,
                 hold_frames: int = LABEL_HOLD_FRAMES,
                 fade_in_frames: int = LABEL_FADE_IN_FRAMES,
                 fade_out_frames: int = LABEL_FADE_OUT_FRAMES):
        self._show = max(1, int(show_frames))
        self._hold = max(0, int(hold_frames))
        self._fade_in = max(1, int(fade_in_frames))
        self._fade_out = max(0, min(self._hold, int(fade_out_frames)))
        self._tracks: Dict[str, _Track] = {}
        self._started = False
        self._lock = threading.Lock()

    def reset(self) -> None:
        with self._lock:
            self._tracks.clear()
            self._started = False

    def update(self, eligible: Iterable[str],
               budget: Optional[Set[str]] = None) -> Dict[str, float]:
        """Fold one frame in and return ``{uid: alpha}`` for every label to
        draw on it. ``eligible`` is what the frame itself would label;
        ``budget`` is the top-N pick (None when there is no limit), and a
        held label outside it starts its fade-out at once.

        The first frame after a reset (a new session, a new model, a gap
        the observing gate made) has no history to debounce against, so it
        draws what it finds at full strength rather than leaving the first
        preview of a session blank.
        """
        eligible = set(eligible)
        with self._lock:
            if not self._started:
                self._started = True
                self._tracks = {uid: _Track(self._show, 1.0) for uid in eligible}
                return self._drawn_locked()
            for uid in eligible:
                track = self._tracks.setdefault(uid, _Track())
                track.streak += 1
                track.missing = 0
                if track.alpha > 0.0 or track.streak >= self._show:
                    track.alpha = min(1.0, track.alpha + 1.0 / self._fade_in)
            for uid in [u for u in self._tracks if u not in eligible]:
                track = self._tracks[uid]
                track.streak = 0
                if track.alpha <= 0.0:
                    del self._tracks[uid]
                    continue
                track.missing += 1
                if budget is not None and uid not in budget:
                    track.missing = max(track.missing, self._hold - self._fade_out + 1)
                if track.missing > self._hold:
                    del self._tracks[uid]
                    continue
                track.alpha = min(track.alpha, self._held_alpha(track.missing))
            return self._drawn_locked()

    def current(self, eligible: Iterable[str]) -> Dict[str, float]:
        """The drawn set without advancing: a reprocess of a capture already
        counted (``observing_window.SAME_CAPTURE_KEY``) draws what that
        capture drew. Before any frame has been folded in, it draws what is
        eligible, as the first frame would."""
        with self._lock:
            if not self._started:
                return {uid: 1.0 for uid in eligible}
            return self._drawn_locked()

    def _held_alpha(self, missing: int) -> float:
        """Alpha ``missing`` frames into the hold: full until the last
        ``fade_out`` frames, then down in equal steps, never reaching 0."""
        into_fade = missing - (self._hold - self._fade_out)
        if into_fade <= 0:
            return 1.0
        return (self._fade_out - into_fade + 1) / (self._fade_out + 1)

    def _drawn_locked(self) -> Dict[str, float]:
        return {uid: t.alpha for uid, t in self._tracks.items() if t.alpha > 0.0}
