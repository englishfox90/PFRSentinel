"""
Time coherence of a near-stationary detection track.

pole_finder's drift band judged a stationary candidate by the *extent* of
its hits — the bounding box of a dozen centroids — which has no notion of
time: a saturated pier light whose centroid jitters 1–2 px reads the same
3–4 px extent over twelve frames as Polaris's genuine 2–3 px arc, and on a
rig where the true pole is hidden behind that pier the finder then accepted
the light on every clear night (issue #93, H1).

The signal that separates them is that Polaris *progresses*: its position
is a smooth function of time, while a jittering light only scatters.
Regressing x and y on time gives two numbers:

    progression — length of the fitted displacement over the window, the
                  part of the motion that time explains;
    scatter     — RMS distance of the hits from the fitted line, the part
                  it does not.

Polaris measures progression ≈ the predicted sidereal arc with scatter ≈
the centroid jitter (0.3–0.5 px); a static light measures scatter ≈ its
jitter and a progression that is only the random-walk residue of that
jitter. A track is coherent when progression clears COHERENCE_MIN_RATIO ×
scatter. static_lights uses the same test the other way round: a track
that fails it over ≥ 45 min is a light, not a star.

Pure functions over arrays; no logging.
"""
from dataclasses import dataclass
from typing import Optional, Sequence, Tuple

import numpy as np

# Minimum progression-to-scatter ratio for a coherent track. Measured on
# the synthetic fixture (tests/allsky_synth.py, tests/test_track_coherence
# .py::test_measured_separation) at the reporter's plate scale, where the
# margin is smallest: over a 47-min window with hits on every buffer frame
# (60), Polaris (jitter 0.4 px) ratios were 4.5–8.9 (min over 200 seeds)
# and a static light jittering 1.2 px ratios were 0.1–1.6 (max over 200
# seeds); with only the 12 sampled frames the light's random-walk residue
# reaches 2.2, which is why the coherence hits are gathered from every
# usable frame, not the sample. 2.5 sits under the weakest Polaris and
# above the strongest light with a margin of ~1.6× on each side. Erring
# high is the safe side: a withheld pole skips an optional gate, an
# accepted light vetoes correct models.
COHERENCE_MIN_RATIO = 2.5

# Fewer hits than this and a line through them explains anything.
MIN_HITS = 4


@dataclass(frozen=True)
class TrackCoherence:
    progression_px: float   # |fitted displacement| over the window
    scatter_px: float       # RMS residual distance about the fitted line
    n_hits: int
    span_minutes: float

    @property
    def ratio(self) -> float:
        """progression / scatter; large means time explains the motion."""
        return self.progression_px / max(self.scatter_px, 1e-6)


def track_coherence(t_minutes: Sequence[float], x: Sequence[float],
                    y: Sequence[float]) -> Optional[TrackCoherence]:
    """Regress (x, y) on time; None when there are too few hits to judge."""
    t = np.asarray(t_minutes, dtype=float)
    xs = np.asarray(x, dtype=float)
    ys = np.asarray(y, dtype=float)
    n = len(t)
    if n < MIN_HITS or n != len(xs) or n != len(ys):
        return None
    span = float(t.max() - t.min())
    if span <= 0:
        return None
    tc = t - t.mean()
    sxx = float(np.dot(tc, tc))
    vx = float(np.dot(tc, xs - xs.mean()) / sxx)
    vy = float(np.dot(tc, ys - ys.mean()) / sxx)
    rx = xs - (xs.mean() + vx * tc)
    ry = ys - (ys.mean() + vy * tc)
    # Two parameters per axis are spent on the line; n-2 keeps the RMS
    # unbiased so a 4-hit track does not look tighter than it is.
    scatter = float(np.sqrt((np.dot(rx, rx) + np.dot(ry, ry)) / (n - 2)))
    return TrackCoherence(
        progression_px=float(np.hypot(vx, vy) * span),
        scatter_px=scatter, n_hits=n, span_minutes=span,
    )


def is_coherent(track: Optional[TrackCoherence], predicted_arc_px: float,
                drift_band: Tuple[float, float], floor_px: float,
                min_ratio: float = COHERENCE_MIN_RATIO) -> bool:
    """True when the track moves like a pole star.

    The fitted progression must clear `min_ratio` × scatter AND sit inside
    `drift_band` × `predicted_arc_px` (lower edge floored at `floor_px`) —
    the same band pole_finder applies to the extent, now applied to the
    part of the motion that time explains.
    """
    if track is None:
        return False
    lo = max(drift_band[0] * predicted_arc_px, floor_px)
    hi = drift_band[1] * predicted_arc_px
    if not (lo <= track.progression_px <= hi):
        return False
    return track.progression_px >= min_ratio * track.scatter_px


def is_static(track: Optional[TrackCoherence], min_ratio: float = COHERENCE_MIN_RATIO,
              floor_px: float = 0.0) -> bool:
    """True when time explains none of the track's motion: a fixed light.

    `floor_px` guards a light so steady (scatter ≪ 1 px) that a sub-pixel
    progression would otherwise pass as coherent motion.
    """
    if track is None:
        return False
    return track.progression_px < min_ratio * max(track.scatter_px, floor_px)
