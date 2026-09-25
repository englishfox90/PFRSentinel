"""
Cold-start orientation search for the multi-image joint fit: roll by vote,
scale by scan, axis from the measured pole when there is one.

A single obstructed frame can't determine the full 8-parameter pose (the
matched stars cluster in the unblocked sky region), so per-frame methods
like triangle hashing settle on a wrong axis_alt/roll. The TRUE pose is the
one that matches stars in *every* frame's clear window as the sky rotates;
a false pose only fits one frame. The search scores each pose hypothesis
over frames hours apart and hands the best few to the joint fit, which
keeps the one that passes the gates.

How a cell is scored (issue #93, package 5b; cross-checked against an
independent all-sky solver, plan §6):

- A cell is (mirror, optical-axis direction, plate scale a1). Roll is never
  gridded: with the axis fixed, a catalogue star and a detection at the
  same radius from the optical centre (± RADIUS_TOL_FRACTION of the frame
  width) pair up, and each pair implies one roll — the difference of their
  polar angles. A 360-bin histogram over the frames, read with a 3-bin
  window, picks it.
- The score is the peak's excess over chance in sigmas,
  (peak − pairs·3/360) / √(pairs·3/360): a small scale that crowds the sky
  into a small circle piles up random pairings, but random pairings fill
  every bin alike. Raw match count, the previous score, let exactly such a
  cell win on issue #33's rig.
- Scale is scanned in ×SCALE_STEP steps: around the rotation fit's plate
  scale when the pole came from the rotating field (a measurement), else
  around the sky-circle seed — which on an obstructed aperture is not a
  measurement (plan §0.3, §0.5: 0.62–0.78× the truth on both real rigs).
- A trusted pole seeds the scale (when it came from the rotating field)
  and filters the cells: a cell whose own model puts the celestial pole
  farther from the measured pixel than the admission tolerance plus the
  cell's granularity is dropped before ranking. The plan's shortcut of
  deriving the axis from the pole pixel is not taken: the pixel fixes the
  axis only up to a rotation about the pole direction, and walking that
  circle through the seed lens was measured too fragile — on the
  reporter preset the family's nearest cell sat 3.6° from the truth and
  scored 6.6σ against 35σ at the true cell, because the seed's cubic
  term, the scale step and the pole's own error each move the circle.
  Filtering the full vote keeps its robustness; the pole then acts again,
  exactly, in the joint fit (5c) and at the gate (5d).
- The frames are the long-baseline ring's first, middle and last (hours
  apart when the ring is full), so a wrong pose cannot line up with stars
  that have moved; the rolling buffer's first, middle and last otherwise.

The optical centre is taken from the sky circle and corrected by
`centre_offset_vote` before each candidate's verification fit: every
bright catalogue star votes for its (dx, dy) to each detection within
reach, and the peak of that 2-D histogram is the centre error the joint
fit's ±100 px bound could not otherwise cross (the field rig's centre was
165 px off, pole-anchor plan).

No threads; the per-cell work is O(pairs) numpy on small temporaries
(each catalogue star's partners are one run of the radius-sorted
detections). The unseeded grid runs only on cold start and basin escape.
"""
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

import numpy as np

from services.logger import app_logger as log

from .calibration import CalibrationError
from .calibration_validate import (
    A3_SEED_DEFAULT,
    a1_from_sky_radius,
    median_sky_r,
    tol_scale,
)
from .fisheye import FisheyeModel
from .pole_tolerance import pole_tolerance_px

# Frames the vote runs on: first, middle and last of the pool. Three is the
# independent solver's recipe (plan §6) and enough — the vote peak on the
# synthetic presets is 20–60σ with three frames, and cost is linear in it.
SEARCH_FRAMES = 3
# A ring shorter than this is no better than the rolling buffer (35 min).
MIN_RING_SPAN_MINUTES = 40.0
# Brightest catalogue stars per frame that take part. 150 covers every
# star the coarse pose can place within the radius tolerance; dimmer ones
# only add chance pairs.
BOOT_MAX_CAT = 150
# Radius pairing tolerance as a fraction of the frame width (plan §6:
# ≈ 0.017·w — 60 px at 3552, 13 px at 750). It must absorb the coarse
# cell's own error: half an AXIS_STEP_DEG of axis error moves a star's
# radius by ~a1·0.026 = 33 px at 1277 px/rad, half a scale step 2.5 % of
# its radius = 37 px at the edge.
RADIUS_TOL_FRACTION = 0.017
ROLL_BINS = 360
ROLL_WINDOW_BINS = 3
# Axis grid. 3° in axis altitude, azimuth step widened by 1/cos(alt) so
# cells stay ~3° apart on the sphere and collapse to one at the zenith.
# The altitude floor is the joint fit's own lower bound (joint_fit: 60°):
# a seed below it is infeasible for least_squares, and no all-sky camera
# points its axis more than 30° from the zenith. The plan's 0–90° would
# spend 85 % of the grid on cells the fit cannot take.
AXIS_STEP_DEG = 3.0
AXIS_ALT_MIN_DEG = 60.0
# Scale scan (plan §6: ×1.05 steps). Around a measured plate scale
# (pole_from_rotation, 0.98× truth on the reference night, plan §0.6) the
# plan's 0.7–1.5× is generous. Around a sky-circle seed the scan covers
# the a1 gate's own admissible band (calibration_validate.validate_a1_scale:
# 0.7–1.8× the circle-implied a1) — the reference rig's true scale is 1.6×
# its circle seed (plan §0.5), outside 1.5, and a candidate outside the
# band is rejected by the gate anyway.
SCALE_STEP = 1.05
SCALE_RANGE_MEASURED = (0.7, 1.5)
SCALE_RANGE_CIRCLE = (0.7, 1.8)
# The cubic seed A3_SEED_DEFAULT was chosen at the reference plate scale
# (a3/a1 = −0.023 at a1 ≈ 1277). Under a resolution change a3 scales with
# a1 (calibration_validate.model_in_frame), so the seed must too: unscaled,
# −30 is 15 % of the radial term at the edge of a 750 px frame against
# the guided model's −2.3 there, and the joint fit's ridge prior then
# holds a3 near that wrong seed.
A3_SEED_A1_REF_PX = 1277.0
# Pole filter (seeded path): a cell survives when its model projects the
# pole within the admission tolerance (pole_tolerance) plus the cell's own
# granularity — half an axis step at the plate scale, half a roll window
# and half a scale step at the pole's radius — since the cell is a coarse
# hypothesis the joint fit still moves by that much.
# Candidates handed to the joint fit, after collapsing cells that are the
# same basin (within DISTINCT_* of a better one). With the pole fixing two
# of the three orientation angles the seeded search is far better
# conditioned, so it needs fewer. The true basin ranked first on every
# synthetic preset in both modes, and on the reference night the first
# candidate in both modes is the one the joint fit turned into the
# admitted model (test_orientation_search, plan §0.7); the extra
# candidates are for rigs those do not represent. Each candidate costs
# one joint fit (~8 s on the reference night's 59 frames, more at full
# resolution), which is why the plan's ≈ 40 is not taken.
BOOT_TOP_K = 8
BOOT_TOP_K_SEEDED = 6
# Two cells are one basin when their orientations, judged as rotations of
# the sky into the camera frame (orientation_separation_deg — axis and
# roll together, since near the zenith the two trade off and cells 30°
# apart in axis azimuth can be the same rotation), are within this and
# their scales within DISTINCT_SCALE_FRACTION. Six degrees is two axis
# steps: the joint fit converges from either.
DISTINCT_ORIENTATION_DEG = 6.0
DISTINCT_SCALE_FRACTION = 0.10
# Centre-offset vote: catalogue stars to mag 3 (≈ 50 above the horizon,
# every one a sure detection), each pairing with detections within
# CENTRE_VOTE_REACH_FRACTION of the sky radius of where the seed puts it,
# in bins of CENTRE_VOTE_BIN_REF_PX (reference resolution). A true offset
# collects one vote per star per frame; chance votes from ~200 detections
# scatter over ~5000 bins, so a 3×3 window holding CENTRE_VOTE_MIN_PEAK
# is not chance.
CENTRE_VOTE_MAX_VMAG = 3.0
CENTRE_VOTE_REACH_FRACTION = 0.10
CENTRE_VOTE_BIN_REF_PX = 4.0
CENTRE_VOTE_MIN_PEAK = 4


@dataclass
class OrientationCandidate:
    """One scored cell; `score` is the roll vote's excess over chance in σ."""
    score: float
    east_left: bool
    axis_alt: float
    axis_az: float
    roll_deg: float
    a1: float
    peak: int
    expected: float


# ---------------------------------------------------------------------------
# Frame selection and scale seeds
# ---------------------------------------------------------------------------

def search_frames(frames: Sequence[dict], ring: Optional[Sequence[dict]] = None,
                  k: int = SEARCH_FRAMES) -> List[dict]:
    """First, middle and last usable frames of the ring when it spans
    MIN_RING_SPAN_MINUTES, else of the buffer (`frames` order is kept)."""
    def usable(pool):
        return [f for f in pool
                if len(f.get('detected') or ()) >= 4 and f.get('above_horizon')]

    pool = usable(ring or ())
    if len(pool) >= k and _span_minutes(pool) >= MIN_RING_SPAN_MINUTES:
        pass
    else:
        pool = usable(frames)
    if not pool:
        raise CalibrationError("Bootstrap: no frames with enough detections.")
    if len(pool) <= k:
        return pool
    idx = np.linspace(0, len(pool) - 1, k).round().astype(int)
    return [pool[i] for i in dict.fromkeys(idx.tolist())]


def _span_minutes(pool: Sequence[dict]) -> float:
    dts = [f['dt'] for f in pool if f.get('dt') is not None]
    if len(dts) < 2:
        return 0.0
    return (max(dts) - min(dts)).total_seconds() / 60.0


def scale_seeds(a1_seed: float, measured: bool) -> np.ndarray:
    """Plate scales to scan: a geometric series in SCALE_STEP over the range
    that fits how `a1_seed` was obtained."""
    lo, hi = SCALE_RANGE_MEASURED if measured else SCALE_RANGE_CIRCLE
    n = int(np.floor(np.log(hi / lo) / np.log(SCALE_STEP))) + 1
    return a1_seed * lo * SCALE_STEP ** np.arange(n)


def a3_seed_for(a1: float) -> float:
    """A3_SEED_DEFAULT carried to plate scale `a1` (see A3_SEED_A1_REF_PX)."""
    return A3_SEED_DEFAULT * float(a1) / A3_SEED_A1_REF_PX


# ---------------------------------------------------------------------------
# Geometry shared with FisheyeModel (roll left out)
# ---------------------------------------------------------------------------

def camera_frame(alt_deg: np.ndarray, az_deg: np.ndarray,
                 axis_alt: float, axis_az: float) -> Tuple[np.ndarray, np.ndarray]:
    """(theta, phi0): angle from the optical axis and the camera-plane
    azimuth at roll = 0, exactly as FisheyeModel.altaz_array_to_pixels
    computes them; a roll of ρ turns phi0 into phi0 + ρ."""
    alt_r, az_r = np.radians(alt_deg), np.radians(az_deg)
    vx = np.cos(alt_r) * np.sin(az_r)
    vy = np.cos(alt_r) * np.cos(az_r)
    vz = np.sin(alt_r)
    ca, sa = np.cos(-np.radians(axis_az)), np.sin(-np.radians(axis_az))
    vx2 = ca * vx - sa * vy
    vy2 = sa * vx + ca * vy
    tilt = np.radians(90.0 - axis_alt)
    ct, st = np.cos(-tilt), np.sin(-tilt)
    vy3 = ct * vy2 - st * vz
    vz3 = st * vy2 + ct * vz
    theta = np.arctan2(np.hypot(vx2, vy3), vz3)
    return theta, np.arctan2(vx2, vy3)


def pixel_polar(x: np.ndarray, y: np.ndarray, cx: float, cy: float,
                east_left: bool) -> Tuple[np.ndarray, np.ndarray]:
    """(r, psi) of pixels about the centre; psi is the camera-plane azimuth
    FisheyeModel would have produced them from (roll included)."""
    es = -1.0 if east_left else 1.0
    return np.hypot(x - cx, y - cy), np.arctan2(es * (x - cx), -(y - cy))


_SEPARATION_ALT = np.array([80.0, 50.0, 50.0, 30.0])
_SEPARATION_AZ = np.array([0.0, 90.0, 200.0, 300.0])


def orientation_separation_deg(a, b) -> float:
    """Largest angle between where orientations `a` and `b` (anything with
    axis_alt, axis_az and roll in radians) put four sky directions in the
    camera frame — the one number that says whether two poses are the
    same rotation, where comparing axis and roll separately does not."""
    t1, p1 = camera_frame(_SEPARATION_ALT, _SEPARATION_AZ, a.axis_alt, a.axis_az)
    t2, p2 = camera_frame(_SEPARATION_ALT, _SEPARATION_AZ, b.axis_alt, b.axis_az)
    v1 = np.column_stack([np.sin(t1) * np.sin(p1 + a.roll), np.sin(t1) * np.cos(p1 + a.roll),
                          np.cos(t1)])
    v2 = np.column_stack([np.sin(t2) * np.sin(p2 + b.roll), np.sin(t2) * np.cos(p2 + b.roll),
                          np.cos(t2)])
    cos = np.clip(np.sum(v1 * v2, axis=1), -1.0, 1.0)
    return float(np.degrees(np.arccos(cos)).max())


def axis_grid(step_deg: float = AXIS_STEP_DEG,
              alt_min_deg: float = AXIS_ALT_MIN_DEG) -> List[Tuple[float, float]]:
    """(axis_alt, axis_az) cells ~step_deg apart on the sphere."""
    cells = []
    for alt in np.arange(alt_min_deg, 90.0 + 1e-9, step_deg):
        n_az = max(1, int(round(360.0 * np.cos(np.radians(alt)) / step_deg)))
        for k in range(n_az):
            cells.append((float(alt), 360.0 * k / n_az))
    return cells


# ---------------------------------------------------------------------------
# The roll vote
# ---------------------------------------------------------------------------

class _VoteFrame:
    """One search frame prepared for the vote: catalogue positions and the
    detections sorted by radius, with their camera-plane azimuths in both
    mirrors. The per-cell work is O(pairs): each catalogue star's partners
    are a contiguous run of the radius-sorted detections."""

    def __init__(self, frame: dict, cx: float, cy: float):
        ah = frame['above_horizon'][:BOOT_MAX_CAT]
        self.alt = np.array([a for _s, a, _z in ah], dtype=float)
        self.az = np.array([z for _s, _a, z in ah], dtype=float)
        det = np.asarray([(d[0], d[1]) for d in frame['detected']], dtype=float)
        r_d = np.hypot(det[:, 0] - cx, det[:, 1] - cy)
        order = np.argsort(r_d)
        self.r_sorted = r_d[order]
        self.psi = {m: pixel_polar(det[order, 0], det[order, 1], cx, cy, m)[1]
                    for m in (True, False)}

    def vote(self, axis_alt: float, axis_az: float, a1s: np.ndarray, east_left: bool,
             tol_px: float, hist: np.ndarray) -> None:
        """Add this frame's pair votes to `hist` (len(a1s) × ROLL_BINS)."""
        theta, phi0 = camera_frame(self.alt, self.az, axis_alt, axis_az)
        r_c = (a1s[:, None] * theta[None, :]
               * (1.0 + A3_SEED_DEFAULT / A3_SEED_A1_REF_PX * theta[None, :] ** 2))
        lo = np.searchsorted(self.r_sorted, r_c - tol_px, side='left').ravel()
        hi = np.searchsorted(self.r_sorted, r_c + tol_px, side='right').ravel()
        counts = hi - lo
        total = int(counts.sum())
        if total == 0:
            return
        cell = np.repeat(np.arange(counts.size), counts)          # (scale, star) row
        starts = np.cumsum(counts) - counts
        det = lo[cell] + (np.arange(total) - np.repeat(starts, counts))
        star = cell % len(theta)
        delta = (self.psi[east_left][det] - phi0[star]) % (2.0 * np.pi)
        bins = np.minimum((delta * (ROLL_BINS / (2.0 * np.pi))).astype(np.int64),
                          ROLL_BINS - 1)
        hist += np.bincount((cell // len(theta)) * ROLL_BINS + bins,
                            minlength=hist.size).reshape(hist.shape)


def _window_sums(hist: np.ndarray) -> np.ndarray:
    """Circular ROLL_WINDOW_BINS sums along the last axis."""
    out = np.zeros_like(hist)
    half = ROLL_WINDOW_BINS // 2
    for k in range(-half, half + 1):
        out += np.roll(hist, k, axis=-1)
    return out


def _score(peak: np.ndarray, pairs: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    expected = pairs * ROLL_WINDOW_BINS / ROLL_BINS
    return (peak - expected) / np.sqrt(np.maximum(1.0, expected)), expected


def _roll_deg(bin_index: np.ndarray) -> np.ndarray:
    """Roll at a bin's centre, wrapped to (−180, 180]."""
    deg = (bin_index + 0.5) * 360.0 / ROLL_BINS
    return ((deg + 180.0) % 360.0) - 180.0


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

def orientation_candidates(
    frames: List[dict],
    east_left_hint: Optional[bool] = None,
    pole=None,
    lat_deg: Optional[float] = None,
    ring: Optional[Sequence[dict]] = None,
    k: Optional[int] = None,
) -> List[FisheyeModel]:
    """Up to `k` coarse seeds for the joint fit, best-first.

    `pole` is the trusted PoleEstimate (or None) in the frames' resolution;
    with `lat_deg` it fixes the axis family and seeds the scale when it
    came from the rotating field. `east_left_hint` restricts the search to
    one mirror. `ring` is the long-baseline frame ring.
    """
    sample = search_frames(frames, ring)
    cx = float(np.median([f.get('sky_cx', 0.0) for f in sample]))
    cy = float(np.median([f.get('sky_cy', 0.0) for f in sample]))
    sky_r = median_sky_r(sample)
    width = float(sample[0].get('image_width') or 0) or (2.0 * sky_r if sky_r else 1200.0)
    tol_px = RADIUS_TOL_FRACTION * width

    seeded = (pole is not None and lat_deg is not None)
    measured = bool(seeded and getattr(pole, 'source', '') == 'rotation'
                    and float(getattr(pole, 'a1_px_per_rad', 0.0) or 0.0) > 0)
    a1_seed = (float(pole.a1_px_per_rad) if measured
               else a1_from_sky_radius(sky_r) if sky_r else 600.0)
    a1s = scale_seeds(a1_seed, measured)
    mirrors = (True, False) if east_left_hint is None else (bool(east_left_hint),)
    vote_frames = [_VoteFrame(f, cx, cy) for f in sample]
    cells = _grid_cells(vote_frames, a1s, mirrors, tol_px)
    if not cells:
        raise CalibrationError("Bootstrap: the orientation vote found no candidate.")
    filtered = False
    if seeded:
        kept = pole_consistent(cells, (cx, cy), pole, float(lat_deg), sky_r)
        if kept:
            cells, filtered = kept, True
        else:
            log.warning(
                f"Orientation search: no cell projects the pole within reach of "
                f"the measured ({pole.x:.0f}, {pole.y:.0f}) — ranking unfiltered; "
                "the joint fit and the pole gate decide")

    if k is None:
        k = BOOT_TOP_K_SEEDED if filtered else BOOT_TOP_K
    chosen = distinct_candidates(cells, k)
    img_w = int(frames[0].get('image_width', 0) or 0)
    img_h = int(frames[0].get('image_height', 0) or 0)
    log.info(f"Orientation search ({'pole-seeded' if seeded else 'unseeded'}, "
             f"{len(sample)} frames, {len(a1s)} scales from a1 {a1_seed:.0f} "
             f"{'measured' if measured else 'sky circle'}, {len(cells)} cells): "
             f"{len(chosen)} candidate(s) of {len(cells)}")
    out = []
    for c in chosen:
        m = FisheyeModel(
            cx=cx, cy=cy, a1=c.a1, a3=a3_seed_for(c.a1), a5=0.0,
            roll=float(np.radians(c.roll_deg)), axis_alt=c.axis_alt,
            axis_az=c.axis_az, east_left=c.east_left,
        )
        m.image_width, m.image_height = img_w, img_h
        out.append(m)
        log.info(f"Bootstrap candidate: score={c.score:.1f}σ ({c.peak} votes vs "
                 f"{c.expected:.0f} chance) east_left={c.east_left} "
                 f"axis_alt={c.axis_alt:.1f} axis_az={c.axis_az:.1f} "
                 f"roll={c.roll_deg:.1f}° a1={c.a1:.0f}")
    return out


def _grid_cells(vote_frames: List[_VoteFrame], a1s: np.ndarray,
                mirrors: Sequence[bool], tol_px: float) -> List[OrientationCandidate]:
    cells = []
    hist = np.zeros((len(a1s), ROLL_BINS), dtype=np.int64)
    for east_left in mirrors:
        for axis_alt, axis_az in axis_grid():
            hist[:] = 0
            for vf in vote_frames:
                vf.vote(axis_alt, axis_az, a1s, east_left, tol_px, hist)
            sums = _window_sums(hist)
            best = np.argmax(sums, axis=1)
            peak = sums[np.arange(len(a1s)), best]
            score, expected = _score(peak, hist.sum(axis=1))
            rolls = _roll_deg(best)
            for s in range(len(a1s)):
                cells.append(OrientationCandidate(
                    float(score[s]), east_left, axis_alt, axis_az, float(rolls[s]),
                    float(a1s[s]), int(peak[s]), float(expected[s])))
    return cells


def pole_consistent(cells: List[OrientationCandidate], centre, pole, lat_deg: float,
                    sky_r: Optional[float]) -> List[OrientationCandidate]:
    """The cells whose own model projects the celestial pole within the
    admission tolerance, plus the cell's granularity, of the measured one."""
    r_p = float(np.hypot(pole.x - centre[0], pole.y - centre[1]))
    gate = pole_tolerance_px(getattr(pole, 'sigma_px', 0.0), tol_scale(sky_r), r_p)
    alt = abs(lat_deg)
    az = 0.0 if lat_deg >= 0 else 180.0
    kept = []
    for c in cells:
        m = FisheyeModel(cx=centre[0], cy=centre[1], a1=c.a1, a3=a3_seed_for(c.a1),
                         roll=float(np.radians(c.roll_deg)), axis_alt=c.axis_alt,
                         axis_az=c.axis_az, east_left=c.east_left)
        xy = m.altaz_to_pixel(alt, az)
        if xy is None:
            continue
        allowance = (c.a1 * np.radians(AXIS_STEP_DEG / 2.0)
                     + r_p * (np.radians(ROLL_WINDOW_BINS / 2.0 * 360.0 / ROLL_BINS)
                              + (SCALE_STEP - 1.0) / 2.0))
        if float(np.hypot(xy[0] - pole.x, xy[1] - pole.y)) <= gate + allowance:
            kept.append(c)
    return kept


def distinct_candidates(cells: List[OrientationCandidate], k: int
                        ) -> List[OrientationCandidate]:
    """Best `k` cells that are not the same basin as a better one."""
    chosen: List[OrientationCandidate] = []
    for c in sorted(cells, key=lambda c: -c.score):
        if len(chosen) >= k:
            break
        if not any(_same_basin(c, d) for d in chosen):
            chosen.append(c)
    return chosen


class _Pose:
    def __init__(self, c: OrientationCandidate):
        self.axis_alt, self.axis_az = c.axis_alt, c.axis_az
        self.roll = float(np.radians(c.roll_deg))


def _same_basin(a: OrientationCandidate, b: OrientationCandidate) -> bool:
    if a.east_left != b.east_left:
        return False
    if abs(np.log(a.a1 / b.a1)) > np.log1p(DISTINCT_SCALE_FRACTION):
        return False
    return orientation_separation_deg(_Pose(a), _Pose(b)) <= DISTINCT_ORIENTATION_DEG


# ---------------------------------------------------------------------------
# Centre offset vote
# ---------------------------------------------------------------------------

def centre_offset_vote(frames: Sequence[dict], model: FisheyeModel,
                       sky_r: Optional[float]) -> Tuple[float, float, int]:
    """(dx, dy, peak): the optical-centre correction the bright stars vote
    for through `model`, and the winning window's vote count. (0, 0, peak)
    when no window reaches CENTRE_VOTE_MIN_PEAK."""
    scale = tol_scale(sky_r)
    reach = CENTRE_VOTE_REACH_FRACTION * (float(sky_r) if sky_r else 700.0 * scale)
    bin_px = max(1.0, CENTRE_VOTE_BIN_REF_PX * scale)
    n_bins = int(np.ceil(2.0 * reach / bin_px))
    hist = np.zeros((n_bins, n_bins), dtype=np.int32)
    votes = []
    for f in frames:
        bright = [(a, z) for s, a, z in f.get('above_horizon') or ()
                  if s.get('vmag', 9.0) <= CENTRE_VOTE_MAX_VMAG]
        det = np.asarray([(d[0], d[1]) for d in f.get('detected') or ()], dtype=float)
        if not bright or len(det) == 0:
            continue
        alts = np.array([a for a, _z in bright]); azs = np.array([z for _a, z in bright])
        px, py, vis = model.altaz_array_to_pixels(alts, azs)
        dx = det[None, :, 0] - px[vis][:, None]
        dy = det[None, :, 1] - py[vis][:, None]
        within = (np.abs(dx) < reach) & (np.abs(dy) < reach)
        if not within.any():
            continue
        vx, vy = dx[within], dy[within]
        votes.append((vx, vy))
        ix = ((vx + reach) / bin_px).astype(int).clip(0, n_bins - 1)
        iy = ((vy + reach) / bin_px).astype(int).clip(0, n_bins - 1)
        np.add.at(hist, (iy, ix), 1)
    if not votes:
        return 0.0, 0.0, 0
    smooth = np.zeros_like(hist)
    for ky in (-1, 0, 1):
        for kx in (-1, 0, 1):
            smooth += np.roll(np.roll(hist, ky, axis=0), kx, axis=1)
    iy, ix = np.unravel_index(int(np.argmax(smooth)), smooth.shape)
    peak = int(smooth[iy, ix])
    if peak < CENTRE_VOTE_MIN_PEAK:
        return 0.0, 0.0, peak
    vx = np.concatenate([v[0] for v in votes]); vy = np.concatenate([v[1] for v in votes])
    cx0 = (ix + 0.5) * bin_px - reach
    cy0 = (iy + 0.5) * bin_px - reach
    near = (np.abs(vx - cx0) <= 1.5 * bin_px) & (np.abs(vy - cy0) <= 1.5 * bin_px)
    return float(np.mean(vx[near])), float(np.mean(vy[near])), peak


# Kept for callers and tests that reach the search through multi_calibrate.
def _coarse_orientation_candidates(frames, k=None, east_left_hint=None, **kw):
    return orientation_candidates(frames, east_left_hint=east_left_hint, k=k, **kw)
