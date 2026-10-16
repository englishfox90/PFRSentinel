"""
Hough vote for the axis a star field rotates about.

Stage 1 of pole_from_rotation. For a frame pair with rotation angle α, a
pair of unit vectors (v₁, v₂) at chord angle d ≤ α determines the rotation
axis exactly: it lies in the plane bisecting the chord, at polar distance
ρ from both with sin ρ = sin(d/2)/sin(α/2), on one of two sides (the +α
and the −α solution; verified numerically, 2000/2000 random cases). Every
detection pair between two frames casts those two votes into a ~2° bin
histogram over the z ≥ 0 hemisphere of axis directions; the true axis
collects one vote per real star per frame pair while chance pairings
scatter over the sphere. Cost is one 200×200 dot product per frame pair
and no axis × scale × mirror score tensor is ever built (plan §8).

Operates on unit vectors the caller has already produced through its seed
lens (pole_from_rotation._Lens); a wrong seed scale smears the peak, which
is why the caller runs the vote at several scales and pools the seeds by
absolute votes (distinct_seeds).
"""
from typing import List, Sequence, Tuple

import numpy as np

# Detection pairs closer than this to a candidate axis do not vote: their
# chord is ~0 and the construction returns the detection itself as the
# axis, which is how a field of static lights (before stripping) produced
# a 46× peak at every light. Real stars inside 1° of the pole are lost to
# the vote only, not to the refine.
MIN_POLAR_DISTANCE_DEG = 1.0

# Hough bin width. 2° at 1300 px/rad is ~45 px at the pole: coarse enough
# that a 20 % scale error on the seed still piles the votes into one bin,
# fine enough that the refine starts inside its basin.
AXIS_BIN_DEG = 2.0
# Chord/arc slack for the seed's lens error: a pair whose chord exceeds the
# rotation arc by more than this cannot be one star under any lens the
# refine is allowed to reach (A1_BOUNDS).
CHORD_SLACK = 0.2
# Distinct bins refined from the vote (the refine picks by support).
REFINE_SEEDS = 3



class AxisBins:
    """Equal-area-ish bins over the z ≥ 0 hemisphere: rings of AXIS_BIN_DEG
    in polar angle, each split in azimuth so bins stay ~AXIS_BIN_DEG wide."""

    def __init__(self, bin_deg: float = AXIS_BIN_DEG):
        self.step = np.radians(bin_deg)
        self.n_rings = int(np.ceil(np.pi / 2 / self.step))
        mids = (np.arange(self.n_rings) + 0.5) * self.step
        self.n_phi = np.maximum(1, np.round(2 * np.pi * np.sin(mids) / self.step)).astype(int)
        self.offset = np.concatenate([[0], np.cumsum(self.n_phi)[:-1]])
        self.total = int(self.n_phi.sum())

    def index(self, n: np.ndarray) -> np.ndarray:
        theta = np.arccos(np.clip(n[:, 2], -1.0, 1.0))
        ring = np.minimum((theta / self.step).astype(int), self.n_rings - 1)
        phi = np.arctan2(n[:, 1], n[:, 0]) % (2 * np.pi)
        k = np.minimum((phi / (2 * np.pi) * self.n_phi[ring]).astype(int),
                       self.n_phi[ring] - 1)
        return self.offset[ring] + k

    def centre(self, flat: int) -> np.ndarray:
        ring = int(np.searchsorted(self.offset, flat, side='right') - 1)
        k = flat - self.offset[ring]
        theta = (ring + 0.5) * self.step
        phi = (k + 0.5) / self.n_phi[ring] * 2 * np.pi
        return np.array([np.sin(theta) * np.cos(phi), np.sin(theta) * np.sin(phi),
                         np.cos(theta)])


def hough_axis(vecs: List[np.ndarray], pairs: Sequence[Tuple[int, int]],
                alphas: np.ndarray) -> Tuple[List[Tuple[np.ndarray, int, int]], float]:
    """Top axis hypotheses [(axis, sign, votes)] and peak/median vote ratio."""
    bins = AxisBins()
    hist = np.zeros((2, bins.total), dtype=np.int32)   # [sign index][bin]
    for (i, j), alpha in zip(pairs, alphas):
        v1, v2 = vecs[i], vecs[j]
        cos_d = np.clip(v1 @ v2.T, -1.0, 1.0)
        d = np.arccos(cos_d)
        ii, jj = np.nonzero(d <= alpha * (1.0 + CHORD_SLACK))
        if len(ii) == 0:
            continue
        a, b = v1[ii], v2[jj]
        dd = d[ii, jj]
        m = a + b
        m /= np.maximum(np.linalg.norm(m, axis=1), 1e-12)[:, None]
        c = np.cross(a, b)
        c /= np.maximum(np.linalg.norm(c, axis=1), 1e-12)[:, None]
        sin_rho = np.minimum(1.0, np.sin(dd / 2.0) / np.sin(alpha / 2.0))
        far = sin_rho >= np.sin(np.radians(MIN_POLAR_DISTANCE_DEG))
        if not far.any():
            continue
        a, b, dd, m, c, sin_rho = a[far], b[far], dd[far], m[far], c[far], sin_rho[far]
        rho = np.arcsin(sin_rho)
        beta = np.arccos(np.clip(np.cos(rho) / np.cos(dd / 2.0), -1.0, 1.0))
        cb, sb = np.cos(beta)[:, None], np.sin(beta)[:, None]
        # The two axes about which +alpha carries a onto b (verified
        # numerically); their antipodes carry it by -alpha.
        for n_plus in (cb * m + sb * c, -cb * m + sb * c):
            flip = n_plus[:, 2] < 0
            n_plus = np.where(flip[:, None], -n_plus, n_plus)
            sign_idx = flip.astype(int)   # 0: +alpha, 1: -alpha
            np.add.at(hist, (sign_idx, bins.index(n_plus)), 1)

    median = float(np.median(hist))
    peak = int(hist.max())
    ratio = peak / max(median, 1.0)
    order = np.argsort(hist, axis=None)[::-1]
    seeds: List[Tuple[np.ndarray, int, int]] = []
    for flat in order[:REFINE_SEEDS * 8]:
        s_idx, b = divmod(int(flat), bins.total)
        axis = bins.centre(b)
        if any(np.dot(axis, prev) > np.cos(2.5 * bins.step) and sgn == (1 if s_idx == 0 else -1)
               for prev, sgn, _ in seeds):
            continue
        seeds.append((axis, 1 if s_idx == 0 else -1, int(hist[s_idx, b])))
        if len(seeds) >= REFINE_SEEDS:
            break
    return seeds, ratio


def distinct_seeds(seeds):
    """Seeds by descending votes, one per axis neighbourhood and sign."""
    out = []
    for axis, sign, votes, s in sorted(seeds, key=lambda t: -t[2]):
        if any(sgn == sign and float(np.dot(axis, prev)) > np.cos(np.radians(2.5 * AXIS_BIN_DEG))
               for prev, sgn, _v, _s in out):
            continue
        out.append((axis, sign, votes, s))
    return out
