"""How many star matches the greedy matcher finds by pure chance.

Why this exists
---------------
On a high-resolution all-sky rig the match count and the RMS residual carry
almost no orientation information. Issue #33: on a 3552x3552 frame with a
trimmed sky radius of ~1345 px, 354 joint fits in one night converged with
``axis_alt`` anywhere between 60 and 90 degrees and *always* returned ~686
matches at RMS 10.86 +/- 0.5 px, with the final re-match tolerance at 15.5 px.
Those numbers are what random points produce: a wrong-basin fit clears the
``min_total_matches`` floor on coincidence alone, so selecting a cold-start
bootstrap winner by raw match count is a coin flip between contradictory
candidates. This module supplies the missing yardstick — the count a *wrong*
model would get for free — so callers can ask whether a fit is materially
above it.

The model
---------
Detections all live inside the sky disc (``detect_stars`` is handed the sky
circle), so the matcher's search area is ``A = pi * sky_r**2``. Treat the
``N_cat`` catalogue candidates a model projects into that disc as uniformly
scattered over it. For one detection, the number of candidates landing within
``tol`` px is Poisson with mean

    lambda = N_cat * pi * tol**2 / A = N_cat * (tol / sky_r)**2

``_brightness_match`` assigns greedily and one-to-one, so a detection yields at
most one match — it contributes a match exactly when its Poisson draw is
non-zero. Hence per frame

    E[matches] = N_det * (1 - exp(-lambda))            (capped at min(N_det, N_cat))

For small ``lambda`` this collapses to the naive area-ratio product
``N_det * N_cat * (tol / sky_r)**2``; the exponential *is* the saturation
correction for one-to-one claiming, and the explicit cap covers the degenerate
``tol ~ sky_r`` end. The expected residual of a chance pair is the mean radius
of a uniform point in a disc of radius ``tol`` measured in quadrature — the
median of a uniform-in-area radius is ``tol / sqrt(2)``.

Calibration against the reporter's Monte-Carlo
----------------------------------------------
The reporter Monte-Carlo'd the greedy matcher on purely random points with
``N_det = 200``, ``N_cat = 400``, ``sky_r = 1345`` px, summed over the whole
frame buffer. Per frame this model gives ``200 * (1 - exp(-400 * (tol/1345)**2))``:

    tol px   lambda    per frame   x60 frames   Monte-Carlo   error
    43       0.4088    67.1        4027         3685          +9.3 %
    26       0.1495    27.8        1666         1595          +4.5 %
    15.5     0.0531    10.35       621          619           +0.3 %

The buffer size is not stated in the report but falls straight out of the
arithmetic: 619 / 10.35 = 59.8, i.e. **60 frames** (and the looser tolerances
imply 55 and 57 — see the bias note below). 60 frames is also the calibration
service's ring-buffer size, so the three tolerances are consistent with one
buffer.

The residual bias is one-sided and understood: this model ignores *catalogue*
contention (a catalogue star can also be claimed only once), which only bites
once many pairs form, so the over-estimate grows with tolerance. It is
negligible at the tight end, and the gate runs at the final, tightest tolerance
— 15.5 px in the report, where the model is within 0.3 %. The predicted median
chance residual there is 15.5 / sqrt(2) = 10.96 px against 10.86 px observed.

Reading the result
------------------
``is_above_chance`` requires ``n_matches >= CHANCE_MARGIN * expected``. The
reporter's wrong-basin fits sat at 686 vs 619 expected — an 11 % excess, well
inside the Monte-Carlo scatter of the estimate itself — while genuine fits run
several times chance. See ``CHANCE_MARGIN``.

Pure: numpy only, no I/O, no Qt.
"""
from dataclasses import dataclass
from math import exp, sqrt
from typing import List, Optional, Tuple

import numpy as np

# _brightness_match projects only the first min(5 * N_det, 400) catalogue
# entries, so the chance pool must be truncated the same way or the estimate
# describes a matcher that does not exist.
CATALOG_PER_DETECTION = 5
CATALOG_CAP = 400

# A fit must beat chance by this factor to count as informative.
#
# Chosen from the gap in the observed data, not from a significance level. The
# wrong-basin fits in issue #33 scored 686 against 619 expected (1.11x) — an
# excess smaller than this estimator's own bias at loose tolerances, so anything
# below ~1.5x is indistinguishable from noise. Real fits are nowhere near that
# line: they match stars the model actually places, so their count is set by sky
# coverage rather than by tolerance area, and they clear chance by a wide margin:
# the synthetic obstructed-rig frames in tests/ cold-start to 675 matches vs 12.8
# expected (52x) at the schedule's tightest tolerance, and still 4.7x in the
# worst case where the fit converges at iteration 0 and is judged at the loosest.
# 2.0 sits in the empty middle of that gap and keeps the estimator's own error
# far away from the decision.
CHANCE_MARGIN = 2.0


@dataclass
class ChanceEstimate:
    """Chance-match expectation for one fit at one tolerance."""

    expected: float         # matches a wrong model gets for free, all frames
    tol_px: float           # tolerance the expectation was computed at
    n_frames: int           # frames that contributed to `expected`
    median_residual_px: float   # residual a chance match would show

    def excess(self, n_matches: int) -> float:
        return excess_over_chance(n_matches, self.expected)

    def ratio(self, n_matches: int) -> float:
        return chance_ratio(n_matches, self.expected)

    def is_above_chance(self, n_matches: int,
                        margin: float = CHANCE_MARGIN) -> bool:
        return is_above_chance(n_matches, self.expected, margin)

    def describe(self, n_matches: int) -> str:
        return (f"{n_matches} matches vs {self.expected:.0f} expected by chance "
                f"at tol={self.tol_px:.1f}px over {self.n_frames} frame(s) "
                f"({self.ratio(n_matches):.2f}x chance)")


def chance_median_residual(tol_px: float) -> float:
    """Median residual of a match made by chance at `tol_px`.

    A chance partner is uniform over the disc of radius `tol_px`, so its radius
    has median `tol/sqrt(2)`. A fit whose RMS sits at this value is describing
    the tolerance, not the sky.
    """
    return float(tol_px) / sqrt(2.0)


def expected_frame_matches(n_det: int, n_cat: int, search_radius_px: float,
                           tol_px: float) -> float:
    """Chance matches in one frame — see the module docstring for the model."""
    if n_det <= 0 or n_cat <= 0 or search_radius_px <= 0 or tol_px <= 0:
        return 0.0
    lam = n_cat * (float(tol_px) / float(search_radius_px)) ** 2
    expected = n_det * (1.0 - exp(-lam)) if lam < 700.0 else float(n_det)
    return float(min(expected, min(n_det, n_cat)))


def frame_pool(frame: dict, model, max_vmag: Optional[float] = None
               ) -> Tuple[int, int, float]:
    """(n_det, n_cat, search_radius_px) for one frame.

    Mirrors `_brightness_match`'s candidate pool: the brightest
    min(5*N_det, 400) above-horizon entries, kept when `model` projects them
    into the region the detections were found in. `search_radius_px` is 0.0
    when the frame carries no sky circle and no resolution — the caller then
    has no geometry to reason about and should fail open.

    This is an approximation, not an exact mirror: `_brightness_match` keeps
    every one of those entries that projects anywhere on the frame (`xy is
    not None`), while this function keeps only the ones landing inside the
    sky circle (or image bounds), so `n_cat` — and the chance expectation
    built from it — is biased low, which is the fail-open direction.
    """
    det = frame.get('detected') or []
    n_det = len(det)
    horizon = frame.get('above_horizon') or []
    if max_vmag is not None:
        horizon = [(s, a, z) for s, a, z in horizon
                   if s.get('vmag', 9.0) <= max_vmag]
    pool = horizon[:min(n_det * CATALOG_PER_DETECTION, CATALOG_CAP)]
    if n_det == 0 or not pool:
        return n_det, 0, 0.0

    alts = np.array([a for _s, a, _z in pool], dtype=float)
    azs = np.array([z for _s, _a, z in pool], dtype=float)
    px, py, vis = model.altaz_array_to_pixels(alts, azs)

    sky_r = frame.get('sky_r')
    cx, cy = frame.get('sky_cx'), frame.get('sky_cy')
    if sky_r and cx is not None and cy is not None:
        inside = vis & (np.hypot(px - float(cx), py - float(cy)) <= float(sky_r))
        radius = float(sky_r)
    else:
        w = float(frame.get('image_width') or 0)
        h = float(frame.get('image_height') or 0)
        if w <= 0 or h <= 0:
            return n_det, int(np.count_nonzero(vis)), 0.0
        inside = vis & (px >= 0) & (px < w) & (py >= 0) & (py < h)
        # Equal-area disc: the formula is written against a circular search
        # region, so a rectangular frame enters through its area.
        radius = sqrt(w * h / np.pi)
    return n_det, int(np.count_nonzero(inside)), radius


def expected_chance_matches(frames: List[dict], model, tol_px: float,
                            max_vmag: Optional[float] = None,
                            min_per_image: int = 0) -> float:
    """Total chance matches `model` earns across `frames` at `tol_px`.

    `min_per_image` mirrors `_build_all_matches`, which drops a frame whose
    match count falls below it: frames not expected to reach the floor are left
    out of the sum, as they are left out of the real total.
    """
    return estimate_chance(frames, model, tol_px, max_vmag, min_per_image).expected


def estimate_chance(frames: List[dict], model, tol_px: float,
                    max_vmag: Optional[float] = None,
                    min_per_image: int = 0) -> ChanceEstimate:
    """`expected_chance_matches` with the supporting numbers attached."""
    total = 0.0
    used = 0
    for f in frames or []:
        n_det, n_cat, radius = frame_pool(f, model, max_vmag)
        if radius <= 0:
            continue
        e = expected_frame_matches(n_det, n_cat, radius, tol_px)
        if e < min_per_image:
            continue
        total += e
        used += 1
    return ChanceEstimate(expected=total, tol_px=float(tol_px), n_frames=used,
                          median_residual_px=chance_median_residual(tol_px))


def excess_over_chance(n_matches: int, expected: float) -> float:
    """Matches beyond what chance supplies. Negative means below chance."""
    return float(n_matches) - float(expected)


def chance_ratio(n_matches: int, expected: float) -> float:
    """`n_matches` as a multiple of chance. `inf` when chance expects none."""
    if expected <= 0:
        return float('inf') if n_matches > 0 else 0.0
    return float(n_matches) / float(expected)


def is_above_chance(n_matches: int, expected: float,
                    margin: float = CHANCE_MARGIN) -> bool:
    """Is `n_matches` materially more than chance would supply?

    Fails open when there is no expectation to compare against (`expected <= 0`,
    e.g. frames with no sky circle or resolution): the estimate is unavailable,
    which is not evidence of a bad fit.
    """
    if expected <= 0:
        return True
    return float(n_matches) >= margin * float(expected)


def check_above_chance(n_matches: int, frames: List[dict], model, tol_px: float,
                       max_vmag: Optional[float] = None,
                       min_per_image: int = 0,
                       margin: float = CHANCE_MARGIN
                       ) -> Tuple[bool, str, ChanceEstimate]:
    """(ok, message, estimate) for a completed fit — the gate callers use."""
    est = estimate_chance(frames, model, tol_px, max_vmag, min_per_image)
    return est.is_above_chance(n_matches, margin), est.describe(n_matches), est
