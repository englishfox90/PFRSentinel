"""
A labelled star that is plainly in the frame keeps its label.

The visibility plane (sky_region) decides where labels may go from discs
drawn around *every* detection, voted over fifteen frames. That is a fair
guess at where the open sky is, and a poor one at the edges of it: on the
hosting-site rig of discussion #105 the 2026-09-30 buffer had bright stars
detected at their predicted pixel frame after frame while the vote called
the pixel obstructed, so the label stayed off — or came and went — over a
star anyone could see.

This module adds the direct evidence. Each frame, every star (and planet)
the overlay could label is looked for among the frame's detections at the
pixel the calibration puts it. A sighting raises a per-object confidence
that decays every frame; two sightings in a row make the label visible
regardless of the vote, and it is held for a few frames after the last
one — a fly-wheel, so a frame where the star is lost in noise or a passing
cloud does not blink the label.

Scope, deliberately narrow:

  * it only ever *adds* visibility, at the object's own pixel. Where the
    observing gate suppresses the overlay (closed roof, no stars, daylight)
    nothing is rendered at all, so a sighting cannot draw a label there;
  * it overrides the vote, never the persisted equipment map;
  * a chance coincidence with a detection needs to repeat on consecutive
    frames to count: at the reporter's density (200 detections on a
    1440 px sky circle, 14 px tolerance) one star in ~50 has a detection
    within reach by chance on a given frame, ~1 in 2500 on two in a row;
  * the tolerance follows the model's own residual, so a model that does
    not describe the sky simply produces no sightings and leaves the vote
    in charge.
"""
import threading
from typing import Dict, Iterable, Optional, Tuple

import numpy as np

# Confidence added per sighting and kept per frame. A star seen every frame
# settles at 1 / (1 - DECAY) = 2.5.
SIGHTING_GAIN = 1.0
SIGHTING_DECAY = 0.6
# Two sightings in a row (1.0 + 0.6) are needed to show a label on sighting
# alone; once shown it is held while the confidence stays above KEEP — four
# frames after a steady star was last seen, three after a brief one.
SIGHTING_SHOW = 1.5
SIGHTING_KEEP = 0.3

# Match radius in calibration pixels: twice the model's RMS residual, never
# under the floor (a guided model can report ~1.4 px on its anchors) nor over
# the cap (a loose model must not collect neighbours' detections).
SIGHTING_TOL_RMS = 2.0
SIGHTING_TOL_MIN_PX = 10.0
SIGHTING_TOL_MAX_PX = 25.0

# Radius of the patch marked as sky around a sighted object, in frame pixels.
# The label test (render_objects._is_sky_visible) takes the median of a
# fixed 31 x 31 px window at any frame size; 22 px covers all of it.
PATCH_RADIUS_PX = 22

# Below this, confidences are dropped rather than decayed forever.
_FORGET = 0.05


def sighting_tolerance(rms_px: float, scale: float) -> float:
    """Match radius in frame pixels for a model of ``rms_px`` residual drawn
    at ``scale`` frame pixels per calibration pixel."""
    tol = min(SIGHTING_TOL_MAX_PX, max(SIGHTING_TOL_MIN_PX, SIGHTING_TOL_RMS * float(rms_px or 0.0)))
    return tol * float(scale)


class StarSightings:
    """Per-object sighting confidence, decaying frame by frame."""

    def __init__(self):
        self._score: Dict[str, float] = {}
        self._shown: set = set()
        self._lock = threading.Lock()

    def reset(self) -> None:
        with self._lock:
            self._score.clear()
            self._shown.clear()

    def update(self, targets: Dict[str, Tuple[float, float]],
               detections: Optional[np.ndarray], tol_px: float) -> None:
        """Fold one frame in: ``targets`` maps each labellable object to its
        predicted (x, y), ``detections`` is an (n, 2) array of the frame's
        detected star centres in the same pixels."""
        seen = _seen(targets, detections, tol_px)
        with self._lock:
            score = {uid: s * SIGHTING_DECAY for uid, s in self._score.items()}
            for uid in seen:
                score[uid] = score.get(uid, 0.0) + SIGHTING_GAIN
            self._score = {uid: s for uid, s in score.items() if s >= _FORGET}
            shown = set()
            for uid, s in self._score.items():
                if s >= SIGHTING_SHOW or (uid in self._shown and s >= SIGHTING_KEEP):
                    shown.add(uid)
            self._shown = shown

    def visible(self) -> set:
        """Objects whose label stands on sightings alone."""
        with self._lock:
            return set(self._shown)


def _seen(targets: Dict[str, Tuple[float, float]], detections, tol_px: float) -> Iterable[str]:
    if not targets or detections is None or len(detections) == 0:
        return ()
    det = np.asarray(detections, dtype=float).reshape(-1, 2)
    uids = list(targets)
    pts = np.array([targets[u] for u in uids], dtype=float)
    d2 = ((pts[:, None, :] - det[None, :, :]) ** 2).sum(axis=2)
    hit = d2.min(axis=1) <= float(tol_px) ** 2
    return [u for u, h in zip(uids, hit) if h]


def label_targets(model, config: dict, lat: float, lon: float, dt) -> Dict[str, Tuple[float, float]]:
    """Predicted frame pixel of every bright star and planet the overlay could
    label this frame, keyed by the renderers' UIDs. Same eligibility as the
    renderers: the layer enabled, a displayable name, LABEL_MIN_ALT_DEG
    above the horizon."""
    from .catalogs import get_bright_stars
    from .coords import radec_to_altaz
    from .planets import get_all_positions
    from .render_objects import LABEL_MIN_ALT_DEG
    from .render_stars import star_display_name, star_uid

    targets: Dict[str, Tuple[float, float]] = {}

    def _add(uid, ra, dec):
        alt, az = radec_to_altaz(ra, dec, lat, lon, dt, refraction=True)
        if float(alt) < LABEL_MIN_ALT_DEG:
            return
        xy = model.altaz_to_pixel(float(alt), float(az))
        if xy is not None:
            targets[uid] = (float(xy[0]), float(xy[1]))

    stars_cfg = config.get('bright_stars', {})
    if stars_cfg.get('enabled', False):
        use_bayer = bool(stars_cfg.get('bayer_fallback', False))
        for s in get_bright_stars(max_mag=float(stars_cfg.get('max_magnitude', 2.5))):
            if star_display_name(s, use_bayer):
                _add(star_uid(s), s['ra_deg'], s['dec_deg'])
    if config.get('planets', {}).get('enabled', True):
        for name, (ra, dec) in get_all_positions(dt, lat, lon).items():
            # The Moon's glare blanks detections around it rather than giving
            # one at its centre; the Sun is never labelled.
            if name not in ('Sun', 'Moon'):
                _add(f'planet:{name}', ra, dec)
    return targets


def apply_sightings(plane: np.ndarray, sightings: StarSightings,
                    targets: Dict[str, Tuple[float, float]],
                    detections: Optional[np.ndarray], tol_px: float,
                    map_sky: Optional[np.ndarray] = None) -> np.ndarray:
    """Advance ``sightings`` with this frame's detections (None for a
    reprocess of a capture already counted, which reads without advancing)
    and mark the sighted objects as sky on ``plane``.

    ``map_sky`` is the persisted equipment map on the vote grid (True = sky),
    or None while there is none. A sighting overrides the fifteen-frame vote,
    never the map: hundreds of frames outweigh a star's few, and a pier
    light on a star's track would otherwise pass for it for minutes."""
    if detections is not None:
        sightings.update(targets, detections, tol_px)
    visible = sightings.visible()
    if map_sky is not None:
        visible = {u for u in visible if u in targets
                   and _map_allows(map_sky, plane.shape, targets[u])}
    return paint_sightings(plane, targets, visible)


def _map_allows(map_sky: np.ndarray, full_shape, xy) -> bool:
    from .label_stability import vote_grid
    step, (rows, cols) = vote_grid(full_shape)
    r, c = int(xy[1]) // step, int(xy[0]) // step
    if not (0 <= r < rows and 0 <= c < cols) or map_sky.shape != (rows, cols):
        return False
    return bool(map_sky[r, c])


def paint_sightings(plane: np.ndarray, targets: Dict[str, Tuple[float, float]],
                    uids: Iterable[str]) -> np.ndarray:
    """Mark a sky patch around each sighted object on the full-resolution
    0/255 visibility plane. Returns the plane, copied only when something is
    painted (the caller's plane may be a cached, read-only array)."""
    uids = [u for u in uids if u in targets]
    if not uids:
        return plane
    h, w = plane.shape[:2]
    r = PATCH_RADIUS_PX
    out = np.array(plane, copy=True)
    for uid in uids:
        x, y = targets[uid]
        cx, cy = int(round(x)), int(round(y))
        x0, x1 = max(0, cx - r), min(w, cx + r + 1)
        y0, y1 = max(0, cy - r), min(h, cy + r + 1)
        if x0 >= x1 or y0 >= y1:
            continue
        yy, xx = np.ogrid[y0:y1, x0:x1]
        patch = (xx - cx) ** 2 + (yy - cy) ** 2 <= r * r
        out[y0:y1, x0:x1][patch] = 255
    return out
