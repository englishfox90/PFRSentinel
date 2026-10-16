"""
Pool hygiene for the calibration buffer: static lights and obstructed sky.

A hosting-site rig (issue #93) has lights on every mount around the ring,
and every one of them is a bright, sharp, perfectly repeatable "star" in
each frame. They reach three consumers and hurt each one:

  - pole_finder: a light near a hidden pole is accepted as Polaris (H1);
  - the joint fit: a light matches some catalogue star at any orientation;
  - chance_matches: lights count towards n_det, so the chance expectation
    every fit is judged against is inflated by things that are not sky.

The lights are found the same way pole_finder finds stationary candidates,
with the time-coherence test of track_coherence deciding: a track present
at one pixel in ≥ STATIC_MIN_FRAMES frames spanning ≥ STATIC_MIN_SPAN_MINUTES
whose motion time does not explain is a light. The coherence test, not a
raw ±2.5 px rule, is what keeps Polaris out of the list: its 2–3 px of real
motion over 45 min on the reporter's rig would sit inside any pixel
tolerance that also tolerates a saturated blob's jitter.

Nothing is persisted; the list is recomputed per run from the frames it is
applied to (cheap: one KD-tree per frame). strip_* return new frame dicts
with shallow-copied metadata and filtered 'detected' lists — the buffer's
own dicts are shared with the GUI thread and are never mutated.

The equipment map is obstruction_map.ObstructionMap (plan package 1),
reached only through sky_mask_for(width, height) -> bool ndarray (True =
sky) or None, so any object with that method serves. The map is learned on
the output frame and stamped with its size and crop; the calibration
buffer is fed the pre-crop frame, so with an output crop configured the
map answers None for the buffer's size and the frames pass through
unchanged — it is never rescaled. A None map likewise changes nothing.
"""
from typing import List, Optional, Sequence, Tuple

import numpy as np
from scipy.spatial import cKDTree

from services.logger import app_logger as log

from .track_coherence import COHERENCE_MIN_RATIO, is_static, track_coherence

# Hits within this radius of a seed belong to one track. Centroid jitter is
# resolution-independent (pole_finder.STATIC_NOISE_FLOOR_PX), so the
# tolerance is absolute: 3σ of the worst jitter measured on a saturated
# light (1.2 px → 3.6 px) with room for a sub-pixel exposure-to-exposure
# centre shift; and Polaris, which must NOT fragment into pieces short
# enough to look static, crosses 6 px only after ~2 h at reference scale,
# by which point any piece's coherence is unmistakable.
STATIC_CLUSTER_TOL_PX = 6.0

# A track needs this many hits, over at least this span, present in at
# least this fraction of the frames between its first and last hit.
# 45 min is where Polaris's progression clears the jitter floor on the
# smallest plate scale in the field (plan §0.1). The other two exist
# because of chance: on a 750 px pool of 150 detections a frame, a 6 px
# disc collects 0.06 chance hits per frame, 3–4 over a 59-frame night —
# scattered hits that fit no line, so with "≥ 3 hits" the coherence test
# called 1379 seeds lights and stripped 85 % of the pool, real stars
# included (reference-rig library replay, 2026-09-18); with 4 hits at
# half presence a synthetic pool of the same density still yielded 107.
# Six hits in ≥ 70 % of the frames they span is a Poisson(0.5) tail of
# ~1e-5 per seed; a real light is in every frame it is not clouded in.
STATIC_MIN_FRAMES = 6
STATIC_MIN_SPAN_MINUTES = 45.0
STATIC_MIN_PRESENCE = 0.7

# Scatter floor for the static verdict: a light rendered so steadily that
# its centroid barely moves must still be called static, not "coherent"
# on a 0.2 px progression.
STATIC_SCATTER_FLOOR_PX = 0.5


def find_static_lights(frames: Sequence[dict], sky_r: Optional[float] = None,
                       tol_px: float = STATIC_CLUSTER_TOL_PX
                       ) -> List[Tuple[float, float]]:
    """Pixels at which a light sits still across the frames.

    `sky_r` is accepted for signature parity with the other pool functions
    and reserved; the tolerance is absolute (module doc).
    """
    usable = sorted((f for f in frames if f.get('detected') and f.get('dt') is not None),
                    key=lambda f: f['dt'])
    if len(usable) < STATIC_MIN_FRAMES:
        return []
    t0 = usable[0]['dt']
    t_min = np.array([(f['dt'] - t0).total_seconds() / 60.0 for f in usable])
    if t_min[-1] - t_min[0] < STATIC_MIN_SPAN_MINUTES:
        return []

    pts = [np.asarray([(d[0], d[1]) for d in f['detected']], dtype=np.float32)
           for f in usable]
    trees = [cKDTree(p) for p in pts]
    seeds = _dedupe_seeds(np.vstack(pts), tol_px)

    # hits[k, i]: index in frame i of the detection within tol of seed k.
    n_seed, n_frame = len(seeds), len(pts)
    hit_x = np.full((n_seed, n_frame), np.nan, dtype=np.float32)
    hit_y = np.full((n_seed, n_frame), np.nan, dtype=np.float32)
    for i, tree in enumerate(trees):
        d, j = tree.query(seeds, k=1, distance_upper_bound=tol_px)
        ok = np.isfinite(d)
        hit_x[ok, i] = pts[i][j[ok], 0]
        hit_y[ok, i] = pts[i][j[ok], 1]

    lights: List[Tuple[float, float]] = []
    for k in range(n_seed):
        ok = ~np.isnan(hit_x[k])
        if int(ok.sum()) < STATIC_MIN_FRAMES:
            continue
        t = t_min[ok]
        if t[-1] - t[0] < STATIC_MIN_SPAN_MINUTES:
            continue
        in_span = int(((t_min >= t[0]) & (t_min <= t[-1])).sum())
        if len(t) < STATIC_MIN_PRESENCE * in_span:
            continue
        track = track_coherence(t, hit_x[k, ok], hit_y[k, ok])
        if is_static(track, COHERENCE_MIN_RATIO, STATIC_SCATTER_FLOOR_PX):
            lights.append((float(hit_x[k, ok].mean()), float(hit_y[k, ok].mean())))
    return _merge_within(lights, tol_px)


def _merge_within(lights: List[Tuple[float, float]], tol_px: float
                  ) -> List[Tuple[float, float]]:
    """One entry per light: a 2 px-jitter light seeds twice (its own
    detections straddle the seed tolerance) and both seeds collect the
    same hits."""
    merged: List[List[float]] = []
    for x, y in lights:
        for m in merged:
            if np.hypot(m[0] - x, m[1] - y) <= tol_px:
                n = m[2]
                m[0] = (m[0] * n + x) / (n + 1)
                m[1] = (m[1] * n + y) / (n + 1)
                m[2] = n + 1
                break
        else:
            merged.append([x, y, 1])
    return [(m[0], m[1]) for m in merged]


def strip_static(frames: Sequence[dict], lights: Sequence[Tuple[float, float]],
                 tol_px: float = STATIC_CLUSTER_TOL_PX) -> List[dict]:
    """Frames with every detection within `tol_px` of a light removed."""
    if not lights:
        return list(frames)
    tree = cKDTree(np.asarray(lights, dtype=np.float32).reshape(-1, 2))
    out = []
    for f in frames:
        det = f.get('detected') or []
        if not det:
            out.append(f)
            continue
        xy = np.asarray([(d[0], d[1]) for d in det], dtype=np.float32)
        d, _ = tree.query(xy, k=1, distance_upper_bound=tol_px)
        keep = ~np.isfinite(d)
        out.append({**f, 'detected': [d_ for d_, k in zip(det, keep) if k]})
    return out


def clean_pools(buffer: Sequence[dict], ring: Optional[Sequence[dict]],
                obstruction_map=None
                ) -> Tuple[List[dict], List[dict], List[Tuple[float, float]]]:
    """(buffer', ring', lights): both pools with equipment and static lights
    stripped, the lights found on their union.

    The rolling buffer alone spans too little (30 min at a 30 s cadence)
    for the 45-min static rule; the ring spans hours. Lights are therefore
    found on both together and stripped from each. Logs the count on the
    transition from none to some, never per run.
    """
    ring = list(ring or [])
    seen = set()
    union = []
    for f in list(buffer) + ring:
        if id(f) not in seen:
            seen.add(id(f))
            union.append(f)
    union = strip_obstructed(union, obstruction_map)
    lights = find_static_lights(union)
    if lights and not _CLEAN_STATE['lit']:
        log.info(f"Calibration pool: {len(lights)} static light(s) stripped from "
                 f"the detections at {[(round(x), round(y)) for x, y in lights[:6]]}"
                 f"{'…' if len(lights) > 6 else ''}")
    _CLEAN_STATE['lit'] = bool(lights)
    return (strip_static(strip_obstructed(buffer, obstruction_map), lights),
            strip_static(strip_obstructed(ring, obstruction_map), lights),
            lights)


_CLEAN_STATE = {'lit': False}   # log-once latch for clean_pools


def strip_obstructed(frames: Sequence[dict], obstruction_map) -> List[dict]:
    """Frames with detections dropped where the equipment map says equipment.

    `obstruction_map` exposes sky_mask_for(width, height) -> bool ndarray
    (True = observable sky) or None. None map, None mask, or a frame with
    no resolution stamp → that frame passes through unchanged.
    """
    if obstruction_map is None:
        return list(frames)
    masks = {}
    out = []
    for f in frames:
        w, h = int(f.get('image_width') or 0), int(f.get('image_height') or 0)
        det = f.get('detected') or []
        if not det or w <= 0 or h <= 0:
            out.append(f)
            continue
        if (w, h) not in masks:
            try:
                masks[(w, h)] = obstruction_map.sky_mask_for(w, h)
            except Exception as e:
                log.debug(f"Equipment map unavailable for {w}x{h}: {e}")
                masks[(w, h)] = None
        mask = masks[(w, h)]
        if mask is None or mask.shape != (h, w):
            out.append(f)
            continue
        xy = np.asarray([(d[0], d[1]) for d in det])
        xi = np.clip(np.rint(xy[:, 0]).astype(int), 0, w - 1)
        yi = np.clip(np.rint(xy[:, 1]).astype(int), 0, h - 1)
        keep = mask[yi, xi]
        out.append({**f, 'detected': [d_ for d_, k in zip(det, keep) if k]})
    return out


def _dedupe_seeds(pts: np.ndarray, tol_px: float) -> np.ndarray:
    """One seed per `tol_px` neighbourhood, in input order."""
    if len(pts) == 0:
        return pts.reshape(0, 2)
    tree = cKDTree(pts)
    taken = np.zeros(len(pts), dtype=bool)
    keep = []
    for i, neighbours in enumerate(tree.query_ball_point(pts, tol_px)):
        if taken[i]:
            continue
        keep.append(i)
        taken[neighbours] = True
    return pts[keep]
