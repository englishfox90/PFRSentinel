"""
Celestial pole from the rotating star field — no pole star required.

The Polaris path (pole_finder) needs a visible, uncontaminated pole star:
on the reporter's hosting-site rig (issue #93) the pier covers Polaris and
a light on it was reported as the pole on every clear night; in the
southern hemisphere there is no bright pole star at all. What every rig
has is the field itself: between two frames Δt apart every real star
satisfies v₂ = R(axis, ω·Δt)·v₁ once detections are mapped to unit vectors
through the lens's radial function, and the axis IS the pole. Done in
angle space the fit is not biased by fisheye curvature the way the
pixel-space rigid fit the pole-anchor plan measured (100+ px) was.

Two stages:

1. Coarse axis by Hough vote (axis_vote): every detection pair casts its
   two exact axis solutions into a 2° histogram over axis direction; the
   true axis collects one vote per star per frame pair while chance
   pairings scatter over the sphere. Run at a few seed scales, seeds
   pooled by absolute votes.

2. Refine over (axis, plate scale, optical centre, cubic shape) by iterated closest
   point in PIXEL space: each frame-1 detection is rotated and re-projected
   through the same radial function and matched to the nearest frame-2
   detection within a tightening tolerance (40 → 10 px at reference
   scale), then a robust least-squares solve over the matches. Pixel
   residuals are what sigma_px must be honest in. The sky circle is a SEED
   only — on an obstructed rig it is not a measurement (plan §0.3) — hence
   a1 is free within A1_BOUNDS and the centre within CENTRE_BOUND_FRACTION
   of the seed; both come out as a free plate-scale / centre measurement.

sigma_px is a jackknife over time blocks of frames: the pole is re-solved
with each block's matches left out and the spread of those solutions,
floored at the covariance the final solve implies and at SIGMA_FLOOR_PX,
is reported. It is a radial 1σ-like scale; tests hold it to covering the
true error in ≥ 90 % of seeded runs on every preset.

Hemisphere follows the sign of the site latitude: the axis found is the
pole this site can see, and the array-coordinate rotation sense maps to
east_left the same way it does in pole_finder. The southern path is
validated on the synthetic fixture only (tests/allsky_synth.SOUTHERN) —
no southern buffer dump existed when this was written (plan §5, decision
6). Runs on the refine worker thread, once per refinement, on the frame
ring (frames ≥ 15 min apart) or, failing that, the rolling buffer.
"""
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

import numpy as np
from scipy.optimize import least_squares
from scipy.spatial import cKDTree

from services.logger import app_logger as log

from .calibration_validate import a1_from_sky_radius, median_frame_resolution, tol_scale
from .axis_vote import (  # re-exported: the vote is stage 1 of this fit
    AXIS_BIN_DEG, CHORD_SLACK, MIN_POLAR_DISTANCE_DEG, REFINE_SEEDS, AxisBins,
    distinct_seeds, hough_axis)
from .pole_estimate import SIDEREAL_DEG_PER_MIN, PoleEstimate

# Input requirements. 8 frames over 45 min give ≥ 20 frame pairs 15 min
# apart and 11° of rotation on the longest — enough for the axis to be a
# vote peak rather than a ridge. The median detection floor is the same
# "cloudy: nothing to judge by" guard chance_matches uses: under 40
# stripped detections a frame is haze or a lit roof.
MIN_FRAMES = 8
MIN_SPAN_MINUTES = 45.0
MIN_MEDIAN_DETECTIONS = 40
# Frames closer than this rotate 3.8°: a 200 px-from-pole star moves 13 px,
# still a clear pairing at the 40 px opening tolerance but a poor lever
# arm for the axis. Pairs are subsampled evenly by gap so long and short
# baselines both contribute.
MIN_PAIR_GAP_MINUTES = 15.0
MAX_PAIRS = 48
# The vote uses only pairs up to this far apart (22° of rotation): the
# chord/arc relation it relies on is computed through the SEED lens, and
# over a 6-hour ring (88°) a 20 % scale error smears the votes until the
# true axis holds only 3× the median bin. Over ≤ 90 min it holds 40×+.
# The refine, which re-fits the lens, still takes every pair.
HOUGH_MAX_GAP_MINUTES = 90.0
HOUGH_MIN_PAIRS = 8
# Seed scales the vote is run at (× the seed a1). At the wrong scale chord
# angles are wrong by the same factor and the true axis's votes smear over
# neighbouring bins: a 0.65× seed on the reference preset lost the peak
# entirely (3× the median) where the right scale holds 40×+. Seeds from
# every scale are pooled and ranked by ABSOLUTE votes, each carrying its
# scale into the refine: ranking by peak/median instead let a too-small
# scale win (it crowds the vectors round the boresight, so its median bin
# empties) and sent every refine seed 30° off (5 of 20 reference runs).
HOUGH_SCALE_STEPS = (1.0, 1.25, 1.55, 0.8)
# The winning bin must hold this many times the median bin's votes for the
# refine to be attempted at all. This only rejects a flat vote: a field of
# random detections measured 4.8–7.5× (the maximum of ~4000 Poisson bins),
# the true axis 13–260×. The real gate is MIN_SUPPORT_FRACTION after the
# refine — a static-only field votes 50× (its configuration is invariant,
# so every frame pair agrees) and then explains 3 % of the detections.
HOUGH_MIN_PEAK_RATIO = 3.0
# Refinement bounds around the seed. The plan's 0.7–1.5× ceiling is
# raised to the a1 gate's own hard ceiling (calibration_validate
# .validate_a1_scale, 1.8): the sky-circle seed on an obstructed aperture
# has measured 0.62× the true scale (plan §0.5, every escape candidate on
# the reference rig), and a 1.5× ceiling then caps the fit at 0.93× truth,
# 30 px off at the pole. The vote scans scale first (HOUGH_SCALE_STEPS)
# so the refine starts near the right one rather than at the bound.
A1_BOUNDS = (0.7, 1.8)
CENTRE_BOUND_FRACTION = 0.1
# Cubic shape term the refine may add to the seed's radial function, as a
# fraction of a1 (r = a1·θ·(1 + k3·θ²) + …). A linear seed cannot follow
# the reference lens (a3/a1 = −0.037, a5/a1 = −0.046): fitted a1-only, its
# pole came out 28 px off with a 5 px sigma on every seed — a confident
# wrong answer. With k3 free the same runs land within 6 px. The bounds
# cover a lens 15 % shorter at the edge than linear (the reference at 70°
# from the axis) to 5 % longer (a mild stereographic).
K3_BOUNDS = (-0.15, 0.05)
# ICP tolerance schedule at reference resolution (× tol_scale). 40 px
# admits a 20 %-wrong seed's mismatch at 200 px from the pole; 10 px is the
# joint fit's own final tolerance. Two floors keep it honest off the
# reference scale: the opening tolerance must cover the vote bin (a 2°
# axis error is a1·0.035 px at the pole — 10 px at 750 px, where 40 px ×
# tol_scale is only 7.6), and the final one must stay above centroid noise
# (JPEG centroids scatter ~1 px; 10 px × 0.19 = 1.9 px on the reference
# rig's 750 px library, where the joint fit itself stops at 3.4–3.8 px).
TOL_SCHEDULE_REF_PX = (40.0, 25.0, 15.0, 10.0)
TOL_FLOOR_PX = 3.0
# Robust-loss scale: residuals beyond this many pixels (× tol_scale) are
# down-weighted, so a chance match inside the tolerance cannot pull the axis.
LOSS_SCALE_REF_PX = 5.0
# The refined solution must explain this fraction of the frame-1
# detections within the final tolerance. True fits on the synthetic presets
# explain 0.55–0.9; on the reference rig's real 2026-09-18 library night
# (750 px finished JPEGs, ~64 % of the pool equipment texture and rendered
# overlay labels even after cleaning, plan §0.6) the TRUE axis explains
# only 0.13–0.17, because the junk is most of the pool. The negative
# controls — a random field, a field of unstripped static lights — explain
# 0.00–0.05. 0.12 admits the real night with a 2.4× margin over the worst
# control; the plan's pole-error allowance (package 5) is what absorbs the
# lens-model error a 13 %-support fit carries.
MIN_SUPPORT_FRACTION = 0.12
# A seed whose refine explains this much is the answer; the remaining
# vote seeds are not refined (they cost ~0.5 s each and only matter when
# the first was a side lobe).
EARLY_EXIT_SUPPORT = 0.5

# Uncertainty. Five blocks: enough solutions for a spread, each still
# holding 80 % of the matches. The floor is the centroid-jitter limit no
# jackknife can see below; the cap marks a fit whose pole is not localised
# (the gate's own tolerance is 140 px at reference — a pole worse than that
# is not a measurement).
JACKKNIFE_BLOCKS = 5
SIGMA_FLOOR_PX = 3.0
# Lens-model error is systematic, so neither the jackknife nor the
# covariance sees it: on the reference preset the pole sat 4–6 px off with
# a jackknife of ~1 px and a match RMS of 3.5 px. The match RMS is the one
# number that grows with that error, so sigma is floored at this multiple
# of it (measured for ≥ 90 % coverage: see tests/test_pole_from_rotation).
SIGMA_RMS_FACTOR = 2.0
SIGMA_MAX_REF_PX = 100.0


@dataclass
class RotationFit:
    """Diagnostics of one rotation solve; `estimate` is what callers use."""
    estimate: Optional[PoleEstimate]
    a1: float                  # refined plate scale, px/rad
    cx: float
    cy: float
    support: float             # matched fraction at the final tolerance
    rms_px: float
    n_matches: int
    peak_ratio: float          # Hough peak / median bin
    reason: str                # why estimate is None, else ''
    sigma_jackknife: float = 0.0
    sigma_cov: float = 0.0


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def pole_from_rotation(
    frames: Sequence[dict],
    lat_deg: float,
    sky_cx: Optional[float] = None,
    sky_cy: Optional[float] = None,
    sky_r: Optional[float] = None,
    seed_model=None,
) -> Optional[PoleEstimate]:
    """The pole the field rotates about, or None (a normal outcome).

    `seed_model` (a FisheyeModel in the frames' resolution) seeds the
    radial function and centre when the caller trusts it; otherwise the
    sky circle does.
    """
    return fit_rotation(frames, lat_deg, sky_cx, sky_cy, sky_r, seed_model).estimate


def fit_rotation(
    frames: Sequence[dict],
    lat_deg: float,
    sky_cx: Optional[float] = None,
    sky_cy: Optional[float] = None,
    sky_r: Optional[float] = None,
    seed_model=None,
) -> RotationFit:
    """pole_from_rotation with the solve's diagnostics."""
    none = RotationFit(None, 0.0, 0.0, 0.0, 0.0, 0.0, 0, 0.0, '')
    usable = sorted((f for f in frames if f.get('detected') and f.get('dt') is not None),
                    key=lambda f: f['dt'])
    if len(usable) < MIN_FRAMES:
        none.reason = f"{len(usable)} frames, need {MIN_FRAMES}"
        return none
    span_min = (usable[-1]['dt'] - usable[0]['dt']).total_seconds() / 60.0
    if span_min < MIN_SPAN_MINUTES:
        none.reason = f"{span_min:.0f} min span, need {MIN_SPAN_MINUTES:.0f}"
        return none
    n_det = int(np.median([len(f['detected']) for f in usable]))
    if n_det < MIN_MEDIAN_DETECTIONS:
        none.reason = f"median {n_det} detections/frame, need {MIN_MEDIAN_DETECTIONS}"
        return none

    if sky_cx is None or sky_cy is None or sky_r is None:
        rs = [(f.get('sky_cx'), f.get('sky_cy'), f.get('sky_r'))
              for f in usable if f.get('sky_r')]
        if not rs:
            none.reason = "no sky circle on any frame"
            return none
        sky_cx = float(np.median([r[0] for r in rs]))
        sky_cy = float(np.median([r[1] for r in rs]))
        sky_r = float(np.median([r[2] for r in rs]))
    lens = _seed_lens(sky_cx, sky_cy, sky_r, seed_model)
    scale = tol_scale(sky_r)

    t0 = usable[0]['dt']
    t_min = np.array([(f['dt'] - t0).total_seconds() / 60.0 for f in usable])
    xy = [np.asarray([(d[0], d[1]) for d in f['detected']], dtype=np.float32)
          for f in usable]
    trees = [cKDTree(p) for p in xy]
    pairs = _select_pairs(t_min)
    alphas = _alphas(t_min, pairs)
    vote_pairs = _select_pairs(t_min, max_gap=HOUGH_MAX_GAP_MINUTES)
    if len(vote_pairs) < HOUGH_MIN_PAIRS:
        vote_pairs = pairs[:HOUGH_MIN_PAIRS]
    vote_alphas = _alphas(t_min, vote_pairs)

    seeds, peak_ratio = [], 0.0
    for s in HOUGH_SCALE_STEPS:
        vecs = [lens.scaled(s, 0.0, 0.0).unproject(p) for p in xy]
        cand, ratio = hough_axis(vecs, vote_pairs, vote_alphas)
        peak_ratio = max(peak_ratio, ratio)
        seeds.extend((axis, sign, votes, s) for axis, sign, votes in cand)
    seeds = distinct_seeds(seeds)
    if peak_ratio < HOUGH_MIN_PEAK_RATIO or not seeds:
        none.peak_ratio = peak_ratio
        none.reason = (f"no rotation axis in the vote (peak {peak_ratio:.1f}x the "
                       f"median bin, need {HOUGH_MIN_PEAK_RATIO:.0f}x)")
        return none

    best = None
    for axis, sign, _votes, seed_scale in seeds[:REFINE_SEEDS]:
        fit = _refine(xy, trees, pairs, alphas, sign, axis, lens, sky_r, scale,
                      seed_scale)
        if fit is not None and (best is None or fit.support > best.support):
            best = fit
        if best is not None and best.support >= EARLY_EXIT_SUPPORT:
            break
    if best is None or best.support < MIN_SUPPORT_FRACTION:
        support = 0.0 if best is None else best.support
        return RotationFit(None, lens.a1, lens.cx, lens.cy, support,
                           0.0 if best is None else best.rms, 0, peak_ratio,
                           f"solution explains {support:.0%} of detections, "
                           f"need {MIN_SUPPORT_FRACTION:.0%}")

    pole_x, pole_y = best.lens.project(best.axis[None, :])[0]
    jack = _jackknife_sigma(best, t_min)
    sigma = max(jack, best.cov_sigma, SIGMA_FLOOR_PX, SIGMA_RMS_FACTOR * best.rms)
    sigma_max = SIGMA_MAX_REF_PX * scale
    if sigma > sigma_max:
        return RotationFit(None, best.lens.a1, best.lens.cx, best.lens.cy,
                           best.support, best.rms, best.n_matches, peak_ratio,
                           f"pole not localised (sigma {sigma:.0f} px > {sigma_max:.0f})")

    array_sign = _array_rotation_sign(best)
    east_left = (array_sign < 0) if lat_deg >= 0 else (array_sign > 0)
    img_w, img_h = median_frame_resolution(usable)
    est = PoleEstimate(
        x=float(pole_x), y=float(pole_y), east_left=east_left, sign=array_sign,
        n_frames=len(usable), span_minutes=float(span_min),
        drift_px=0.0, flux=0.0,
        sign_votes=(int(best.n_matches), 0) if array_sign > 0 else (0, int(best.n_matches)),
        window_start=usable[0]['dt'], window_end=usable[-1]['dt'],
        image_width=img_w, image_height=img_h,
        sigma_px=float(sigma), source='rotation',
    )
    log.info(
        f"Rotation pole: ({est.x:.1f}, {est.y:.1f}) ± {sigma:.0f} px from "
        f"{len(usable)} frames over {span_min:.0f} min ({len(pairs)} pairs, "
        f"support {best.support:.0%}, rms {best.rms:.1f} px, a1 {best.lens.a1:.0f} "
        f"vs seed {lens.a1:.0f}, centre ({best.lens.cx:.0f}, {best.lens.cy:.0f}), "
        f"east_left={east_left})"
    )
    return RotationFit(est, best.lens.a1, best.lens.cx, best.lens.cy, best.support,
                       best.rms, best.n_matches, peak_ratio, '', jack, best.cov_sigma)


# ---------------------------------------------------------------------------
# Lens: pixel <-> unit vector through the seed radial function
# ---------------------------------------------------------------------------

class _Lens:
    """r = s·(a1θ + a3θ³ + a5θ⁵) about (cx, cy); image-based camera frame
    (x right, y down, z along the boresight — the handedness is absorbed
    by the rotation sign)."""

    __slots__ = ('cx', 'cy', 'a1', 'a3', 'a5')

    def __init__(self, cx, cy, a1, a3=0.0, a5=0.0):
        self.cx, self.cy = float(cx), float(cy)
        self.a1, self.a3, self.a5 = float(a1), float(a3), float(a5)

    def scaled(self, s: float, dcx: float, dcy: float, k3: float = 0.0) -> '_Lens':
        return _Lens(self.cx + dcx, self.cy + dcy, self.a1 * s,
                     (self.a3 + k3 * self.a1) * s, self.a5 * s)

    def _r(self, theta):
        t2 = theta * theta
        return theta * (self.a1 + t2 * (self.a3 + t2 * self.a5))

    def _theta(self, r):
        theta = r / self.a1
        if self.a3 == 0.0 and self.a5 == 0.0:
            return theta
        for _ in range(6):   # Newton on the polynomial; converges in 3–4
            t2 = theta * theta
            f = theta * (self.a1 + t2 * (self.a3 + t2 * self.a5)) - r
            df = self.a1 + t2 * (3.0 * self.a3 + 5.0 * t2 * self.a5)
            theta = theta - f / np.where(np.abs(df) < 1e-6, 1e-6, df)
        return theta

    def unproject(self, xy: np.ndarray) -> np.ndarray:
        dx = xy[:, 0] - self.cx
        dy = xy[:, 1] - self.cy
        r = np.hypot(dx, dy)
        theta = self._theta(r)
        s = np.sin(theta) / np.where(r < 1e-6, 1.0, r)
        v = np.empty((len(xy), 3), dtype=np.float64)
        v[:, 0] = dx * s
        v[:, 1] = dy * s
        v[:, 2] = np.cos(theta)
        return v

    def project(self, v: np.ndarray) -> np.ndarray:
        rho = np.hypot(v[:, 0], v[:, 1])
        theta = np.arctan2(rho, v[:, 2])
        r = self._r(theta)
        k = r / np.where(rho < 1e-9, 1.0, rho)
        out = np.empty((len(v), 2), dtype=np.float64)
        out[:, 0] = self.cx + v[:, 0] * k
        out[:, 1] = self.cy + v[:, 1] * k
        return out


def _seed_lens(sky_cx, sky_cy, sky_r, seed_model) -> _Lens:
    if seed_model is not None and getattr(seed_model, 'a1', 0) > 0:
        return _Lens(seed_model.cx, seed_model.cy, seed_model.a1,
                     getattr(seed_model, 'a3', 0.0), getattr(seed_model, 'a5', 0.0))
    return _Lens(sky_cx, sky_cy, a1_from_sky_radius(sky_r))


def _rotate(v: np.ndarray, axis: np.ndarray, alpha: np.ndarray) -> np.ndarray:
    """Rodrigues rotation of each row of v about `axis` by its own alpha."""
    c = np.cos(alpha)[:, None]
    s = np.sin(alpha)[:, None]
    dot = (v @ axis)[:, None]
    return v * c + np.cross(axis, v) * s + axis[None, :] * dot * (1.0 - c)


# ---------------------------------------------------------------------------
# Frame pairs
# ---------------------------------------------------------------------------

def _select_pairs(t_min: np.ndarray, max_gap: float = float('inf')
                  ) -> List[Tuple[int, int]]:
    n = len(t_min)
    pairs = [(i, j) for i in range(n) for j in range(i + 1, n)
             if MIN_PAIR_GAP_MINUTES <= t_min[j] - t_min[i] <= max_gap]
    if len(pairs) <= MAX_PAIRS:
        return pairs
    pairs.sort(key=lambda p: t_min[p[1]] - t_min[p[0]])
    idx = np.linspace(0, len(pairs) - 1, MAX_PAIRS).round().astype(int)
    return [pairs[i] for i in dict.fromkeys(idx.tolist())]


def _alphas(t_min: np.ndarray, pairs: Sequence[Tuple[int, int]]) -> np.ndarray:
    """Rotation angle (rad) of each pair."""
    if not pairs:
        return np.zeros(0)
    return np.radians(SIDEREAL_DEG_PER_MIN * (t_min[[j for _, j in pairs]]
                                              - t_min[[i for i, _ in pairs]]))


# ---------------------------------------------------------------------------
# Stage 2: ICP refinement in pixel space
# ---------------------------------------------------------------------------

@dataclass
class _Fit:
    axis: np.ndarray
    sign: int
    lens: _Lens
    support: float
    rms: float
    n_matches: int
    cov_sigma: float
    # Final matches, for the jackknife: pixel pairs and their frame indices.
    p1: np.ndarray
    p2: np.ndarray
    alpha: np.ndarray
    frame_i: np.ndarray
    frame_j: np.ndarray
    params: np.ndarray
    seed_axis: np.ndarray
    seed_lens: _Lens
    sky_r: float


def _bounds(sky_r: float):
    """least_squares bounds on (axis a, axis b, a1 scale, dcx, dcy, k3)."""
    centre_bound = CENTRE_BOUND_FRACTION * sky_r
    return ([-1.0, -1.0, A1_BOUNDS[0], -centre_bound, -centre_bound, K3_BOUNDS[0]],
            [1.0, 1.0, A1_BOUNDS[1], centre_bound, centre_bound, K3_BOUNDS[1]])


def _tangent_basis(n0: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    helper = np.array([1.0, 0.0, 0.0]) if abs(n0[0]) < 0.9 else np.array([0.0, 1.0, 0.0])
    e1 = np.cross(n0, helper)
    e1 /= np.linalg.norm(e1)
    e2 = np.cross(n0, e1)
    return e1, e2


def _unpack(params, n0, e1, e2, seed_lens):
    a, b, s, dcx, dcy, k3 = params
    axis = n0 + a * e1 + b * e2
    axis = axis / np.linalg.norm(axis)
    return axis, seed_lens.scaled(s, dcx, dcy, k3)


def _predict(params, n0, e1, e2, seed_lens, p1, alpha):
    axis, lens = _unpack(params, n0, e1, e2, seed_lens)
    return lens.project(_rotate(lens.unproject(p1), axis, alpha))


def _assign(params, n0, e1, e2, seed_lens, xy, trees, pairs, alphas, sign, tol):
    """Nearest-neighbour matches within `tol` at the current parameters."""
    p1s, p2s, als, fis, fjs = [], [], [], [], []
    for (i, j), alpha in zip(pairs, alphas):
        pred = _predict(params, n0, e1, e2, seed_lens, xy[i],
                        np.full(len(xy[i]), sign * alpha))
        d, k = trees[j].query(pred, k=1, distance_upper_bound=tol)
        ok = np.isfinite(d)
        if not ok.any():
            continue
        p1s.append(xy[i][ok])
        p2s.append(xy[j][k[ok]])
        als.append(np.full(int(ok.sum()), sign * alpha))
        fis.append(np.full(int(ok.sum()), i, dtype=np.int16))
        fjs.append(np.full(int(ok.sum()), j, dtype=np.int16))
    if not p1s:
        return None
    return (np.vstack(p1s).astype(np.float64), np.vstack(p2s).astype(np.float64),
            np.concatenate(als), np.concatenate(fis), np.concatenate(fjs))


def _solve(params, n0, e1, e2, seed_lens, p1, p2, alpha, bounds, f_scale):
    def residual(p):
        return (_predict(p, n0, e1, e2, seed_lens, p1, alpha) - p2).ravel()
    # lsmr: the dense trust-region solver SVDs the (2M × 6) Jacobian on
    # every iteration — a third of the whole fit's wall time for M ≈ 9000.
    return least_squares(residual, params, bounds=bounds, loss='soft_l1',
                         f_scale=f_scale, max_nfev=40, xtol=1e-6, ftol=1e-6,
                         tr_solver='lsmr')


def _refine(xy, trees, pairs, alphas, sign, axis0, lens, sky_r, scale,
            seed_scale: float = 1.0) -> Optional[_Fit]:
    e1, e2 = _tangent_basis(axis0)
    bounds = _bounds(sky_r)
    params = np.array([0.0, 0.0, float(np.clip(seed_scale, *A1_BOUNDS)), 0.0, 0.0, 0.0])
    n_total = sum(len(xy[i]) for i, _ in pairs)
    matches = None
    result = None
    bin_px = 1.2 * lens.a1 * seed_scale * np.radians(AXIS_BIN_DEG)
    for k, tol_ref in enumerate(TOL_SCHEDULE_REF_PX):
        tol = max(tol_ref * scale, TOL_FLOOR_PX, bin_px if k == 0 else 0.0)
        matches = _assign(params, axis0, e1, e2, lens, xy, trees, pairs, alphas,
                          sign, tol)
        if matches is None or len(matches[0]) < 20:
            return None
        p1, p2, al, _fi, _fj = matches
        result = _solve(params, axis0, e1, e2, lens, p1, p2, al, bounds,
                        max(LOSS_SCALE_REF_PX * scale, TOL_FLOOR_PX / 2.0))
        params = result.x
    p1, p2, al, fi, fj = matches
    res = result.fun.reshape(-1, 2)
    rms = float(np.sqrt(np.mean(res[:, 0] ** 2 + res[:, 1] ** 2)))
    axis, fit_lens = _unpack(params, axis0, e1, e2, lens)
    return _Fit(axis=axis, sign=sign, lens=fit_lens,
                support=len(p1) / max(n_total, 1), rms=rms, n_matches=len(p1),
                cov_sigma=_covariance_sigma(result, params, axis0, e1, e2, lens),
                p1=p1, p2=p2, alpha=al, frame_i=fi, frame_j=fj, params=params,
                seed_axis=axis0, seed_lens=lens, sky_r=sky_r)


def _pole_pixel(params, n0, e1, e2, seed_lens) -> np.ndarray:
    axis, lens = _unpack(params, n0, e1, e2, seed_lens)
    return lens.project(axis[None, :])[0]


def _covariance_sigma(result, params, n0, e1, e2, seed_lens) -> float:
    """Radial 1σ of the pole pixel from the solve's Jacobian."""
    try:
        jac = result.jac
        dof = max(jac.shape[0] - jac.shape[1], 1)
        s2 = 2.0 * result.cost / dof
        cov = np.linalg.pinv(jac.T @ jac) * s2
        grad = np.empty((2, len(params)))
        base = _pole_pixel(params, n0, e1, e2, seed_lens)
        for k in range(len(params)):
            step = np.zeros(len(params))
            step[k] = 1e-4
            grad[:, k] = (_pole_pixel(params + step, n0, e1, e2, seed_lens) - base) / 1e-4
        cov_pole = grad @ cov @ grad.T
        return float(np.sqrt(max(np.trace(cov_pole), 0.0)))
    except Exception:
        return 0.0


def _jackknife_sigma(fit: _Fit, t_min: np.ndarray) -> float:
    """Spread of the pole over leave-one-time-block-out re-solves."""
    e1, e2 = _tangent_basis(fit.seed_axis)
    bounds = _bounds(fit.sky_r)
    n_frames = len(t_min)
    k_blocks = min(JACKKNIFE_BLOCKS, n_frames)
    block_of = np.minimum((np.arange(n_frames) * k_blocks) // n_frames, k_blocks - 1)
    poles = []
    for b in range(k_blocks):
        keep = (block_of[fit.frame_i] != b) & (block_of[fit.frame_j] != b)
        if keep.sum() < max(20, 0.3 * len(keep)):
            continue
        res = _solve(fit.params, fit.seed_axis, e1, e2, fit.seed_lens,
                     fit.p1[keep], fit.p2[keep], fit.alpha[keep], bounds,
                     max(LOSS_SCALE_REF_PX * tol_scale(fit.sky_r), TOL_FLOOR_PX / 2.0))
        poles.append(_pole_pixel(res.x, fit.seed_axis, e1, e2, fit.seed_lens))
    if len(poles) < 3:
        return 0.0
    poles = np.array(poles)
    k = len(poles)
    dev = poles - poles.mean(axis=0)
    return float(np.sqrt((k - 1) / k * np.sum(dev ** 2)))


def _array_rotation_sign(fit: _Fit) -> int:
    """Rotation sense in array coordinates (+1 = pole_finder._rotate's
    positive angle), measured by rotating a point beside the pole pixel."""
    pole = fit.lens.project(fit.axis[None, :])[0]
    q0 = np.array([[pole[0] + 30.0, pole[1]]])
    q1 = fit.lens.project(_rotate(fit.lens.unproject(q0), fit.axis,
                                  np.array([fit.sign * 0.1])))[0]
    cross = (q0[0, 0] - pole[0]) * (q1[1] - pole[1]) - (q0[0, 1] - pole[1]) * (q1[0] - pole[0])
    return 1 if cross > 0 else -1
