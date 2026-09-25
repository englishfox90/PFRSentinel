"""
Where in the frame may a label go? The visibility plane for one render.

Three sources, in order of trust for the frame at hand:

  1. the per-night sky-mask vote (``label_stability.SkyMaskHistory``) while
     it is fresh — real detections from the last fifteen frames;
  2. the persisted equipment map (``obstruction_map``) once the vote is
     stale — what hundreds of earlier frames said about this rig;
  3. the calibration model's own sky disc when neither exists — the first
     frames of a first session. Labels may then sit on equipment for a few
     minutes; the map fixes that within the hour.

When both a fresh vote and a map exist a pixel must be sky in both: the
vote can only remove sky the map allows, and the map can only remove sky the
vote saw. Every source is further bounded by the model's sky disc, so a
pixel nobody has judged (a frame corner, a sparse vote) can never place a
label outside the calibrated sky circle. The raw-grayscale fallback this
replaces passed lit equipment on a moonlit frame and nothing at all on a
dark stretch (issue #93, H9).

This module also owns what the vote and the map are fed: the detection
evidence of a frame (discs around detected stars, at one and at two radii)
and the Moon's glare disc, which blanks detections without being equipment.

Everything here is computed on the vote's reduced grid (≤ 512 px longest
edge, ``label_stability.vote_grid``) and upsampled once at the end, so the
per-frame cost on the capture path is a handful of small planes plus the
one full-resolution plane the label tests read (plan §8).
"""
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Sequence, Tuple

import numpy as np
from PIL import Image, ImageDraw

from services.logger import app_logger as log

# Pre-imported here (and therefore by overlay_renderer at module import) so
# the image-processor thread never triggers scipy's first import — that
# segfaults in PyInstaller builds.
try:
    from scipy.spatial.distance import cdist as _cdist
except Exception as _e:
    _cdist = None
    log.error(f"scipy.spatial import failed in sky_region: {type(_e).__name__}: {_e}")

from .calibration_validate import SKY_TRIM_FRACTION
from .fisheye import FisheyeModel
from .label_stability import LabelStabilizer, upsample, vote_grid
from .obstruction_map import ObstructionMap
from .detection_filters import DetectionFilters
from .star_centroid import detect_stars, estimate_sky_circle

# Detections a frame needs before its mask can say where the sky is NOT: the
# discs are drawn around detections, so with few of them most of the disc
# is uncovered and would read as obstruction. Ten was the renderer's floor
# for using a mask at all; it stays the floor for a full vote.
FULL_MASK_MIN_DETECTIONS = 10

# Below the full floor, three to nine detections are still real stars — a
# moonlit or hazy frame on a fixed rig — and vote sky inside their discs
# without saying anything elsewhere. Under three there is no second-nearest
# neighbour to size a disc by, and a pair of hot pixels would pass; that is
# a miss.
PARTIAL_MIN_DETECTIONS = 3

# Local brightness, as a fraction of the frame's sky level, at which a
# detection's claim on the sky around it is smallest (equipment edge) and
# largest (open sky). 20/110 and 60/110: the former absolute thresholds at the
# sky level of the frame they were tuned on.
_DIM_FRACTION = 0.18
_SKY_FRACTION = 0.55

# Negative evidence for the equipment map reaches this many disc radii from
# a detection: a pixel further than that from every star on a frame that saw
# forty or more of them was watched and found dark (plan, package 1).
NEGATIVE_REACH_RADII = 2.0

# The Moon's glare disc when no saturated blob can be measured: on the
# reporter's 20 s frames the halo spans 0.12–0.15 of the sky radius (§0.3).
MOON_GLARE_FRACTION = 0.15
# A saturated blob is looked for within this fraction of the sky radius of
# the predicted Moon pixel — the model's error plus the topocentric Moon's
# own drift between capture and render stay well inside it.
MOON_SEARCH_FRACTION = 0.10
# 8-bit level that counts as saturated on the stretched output frame. 250,
# not 255: JPEG ringing and the stretch's own rounding nibble the top.
MOON_SATURATION_LEVEL = 250
# Fewer saturated pixels than this near the predicted Moon is a bright star
# or a light, not the Moon's core; the default fraction is used instead.
MOON_MIN_SATURATED_PX = 20
# The halo extends about this far beyond the saturated core on the
# reporter's frames: the label beside the core was washed out, the one a
# core-diameter away was not.
MOON_HALO_OVER_CORE = 2.0


@dataclass
class SkyEvidence:
    """One frame's detection evidence, on the grid for ``full_shape``."""
    mask: Optional[np.ndarray]        # uint8 0/255 discs, None under PARTIAL floor
    reach: Optional[np.ndarray]       # bool discs at NEGATIVE_REACH_RADII
    n_detections: int
    full_shape: Tuple[int, int]       # (height, width) the grid stands for


def detect_sky_evidence(img: Image.Image, gray: Optional[np.ndarray] = None) -> SkyEvidence:
    """Build the sky visibility evidence from actual star detections.

    Each detected star claims a circular region whose radius is based on
    the 2nd-nearest-neighbour distance (robust to isolated edge stars)
    and weighted by local image brightness — bright open sky gets full
    radius, dim equipment edges get small radii so labels don't bleed
    onto obstructed areas.
    """
    w, h = img.size
    full_shape = (h, w)
    try:
        sky_cx, sky_cy, sky_r = estimate_sky_circle(img)
        detections = detect_stars(
            img, max_stars=200,
            sky_cx=sky_cx, sky_cy=sky_cy, sky_radius=sky_r,
            filters=DetectionFilters(),
        )
    except Exception:
        return SkyEvidence(None, None, 0, full_shape)

    n = len(detections)
    if n < PARTIAL_MIN_DETECTIONS or _cdist is None:
        return SkyEvidence(None, None, n, full_shape)

    det_xy = np.array([(x, y) for x, y, _ in detections])

    # 2nd nearest neighbour distance (more robust than 1st to outliers)
    dists = _cdist(det_xy, det_xy)
    np.fill_diagonal(dists, 1e9)
    nn2_dist = np.sort(dists, axis=1)[:, 1]
    base_radii = np.clip(nn2_dist * 0.8, 50, 250)

    # Brightness weight: detections in dim areas (near equipment) get 30% of
    # their base radius, detections in open sky get 100%.  This prevents
    # equipment-edge stars from claiming nearby obstructed regions.
    #
    # "Dim" is judged against this frame's own sky level, not a fixed grey
    # value. The thresholds were 20 and 60 on a 0-255 scale, which is right
    # for a frame whose sky sits near 110 and wrong for every other: one
    # auto-exposure step, or dusk fading, moved the whole sky across them and
    # the mask shrank to a third — taking the labels over that sky with it
    # (discussion #76). The fractions below are those same two thresholds
    # expressed against the reference frame's sky level.
    if gray is None:
        gray = np.array(img.convert('L'))
    bw = 25  # brightness sample half-window
    brightness = np.array([
        float(np.median(gray[max(0, int(y) - bw):int(y) + bw + 1,
                              max(0, int(x) - bw):int(x) + bw + 1]))
        for x, y in det_xy
    ])
    # Most detections are in open sky, so their median IS the sky level.
    sky_level = max(float(np.median(brightness)), 1.0)
    relative = brightness / sky_level
    weight = np.clip((relative - _DIM_FRACTION) / (_SKY_FRACTION - _DIM_FRACTION),
                     0.3, 1.0)
    radii = base_radii * weight

    mask = grid_discs(full_shape, det_xy, radii)
    reach = grid_discs(full_shape, det_xy, radii * NEGATIVE_REACH_RADII) > 0
    return SkyEvidence(mask, reach, n, full_shape)


def grid_discs(full_shape, centres: Sequence, radii: Sequence) -> np.ndarray:
    """uint8 0/255 plane on the grid for ``full_shape`` with a filled disc
    per full-resolution (x, y), r. PIL rasterises straight onto the grid: a
    broadcast distance grid on a 3552 px frame is a 100 MB temporary."""
    step, (rows, cols) = vote_grid(full_shape)
    mask = Image.new('L', (cols, rows), 0)
    draw = ImageDraw.Draw(mask)
    for (x, y), r in zip(centres, radii):
        x, y, r = x / step, y / step, r / step
        draw.ellipse([(x - r, y - r), (x + r, y + r)], fill=255)
    return np.array(mask)


# ---------------------------------------------------------------------------
# Model-derived geometry
# ---------------------------------------------------------------------------

def model_sky_radius(model: FisheyeModel) -> float:
    """The model's own sky radius in its pixels: the exact inverse of
    ``calibration_validate.a1_from_sky_radius`` (pole-anchor plan P7), so
    the two stay consistent by construction."""
    return float(model.a1) * (np.pi / 2.0) * (1.0 - SKY_TRIM_FRACTION)


def model_sky_disc(model: FisheyeModel, full_shape) -> np.ndarray:
    """Bool plane on the grid, True inside the model's sky disc. The model
    must already be scaled and translated to this frame (the renderer does
    that)."""
    return grid_discs(full_shape, [(float(model.cx), float(model.cy))],
                      [model_sky_radius(model)]) > 0


def moon_glare_disc(
    img: Image.Image, model: FisheyeModel, dt: datetime, lat: float, lon: float,
    sky_r: Optional[float] = None,
) -> Optional[np.ndarray]:
    """Bool plane on the grid covering the Moon's glare, or None when the
    Moon is not in the frame. Radius from the saturated blob at the
    predicted pixel when one is found, else MOON_GLARE_FRACTION of the sky
    radius (§0.3). Only the window around the predicted Moon is read from
    ``img``: a full-frame grayscale of a 3552 px frame costs 50 ms."""
    try:
        from .coords import radec_to_altaz
        from .planets import moon_radec_topocentric
        ra, dec = moon_radec_topocentric(dt, lat, lon)
        alt, az = radec_to_altaz(ra, dec, lat, lon, dt, refraction=True)
        if float(alt) < 0.0:
            return None
        xy = model.altaz_to_pixel(float(alt), float(az))
    except Exception as e:
        log.debug(f"allsky: Moon position unavailable for the glare disc: {e}")
        return None
    if xy is None:
        return None
    w, h = img.size
    mx, my = float(xy[0]), float(xy[1])
    if not (0 <= mx < w and 0 <= my < h):
        return None
    if sky_r is None:
        sky_r = model_sky_radius(model)
    radius = _saturated_radius(img, mx, my, sky_r)
    if radius is None:
        radius = MOON_GLARE_FRACTION * sky_r
    return grid_discs((h, w), [(mx, my)], [radius]) > 0


def _saturated_radius(img: Image.Image, mx, my, sky_r) -> Optional[float]:
    """Halo radius from the saturated core near (mx, my), or None."""
    search = MOON_SEARCH_FRACTION * sky_r
    w, h = img.size
    x0, x1 = max(0, int(mx - search)), min(w, int(mx + search) + 1)
    y0, y1 = max(0, int(my - search)), min(h, int(my + search) + 1)
    if x0 >= x1 or y0 >= y1:
        return None
    window = np.asarray(img.crop((x0, y0, x1, y1)).convert('L'))
    yy, xx = np.ogrid[y0:y1, x0:x1]
    near = ((xx - mx) ** 2 + (yy - my) ** 2) <= search * search
    n_sat = int(np.count_nonzero((window >= MOON_SATURATION_LEVEL) & near))
    if n_sat < MOON_MIN_SATURATED_PX:
        return None
    return MOON_HALO_OVER_CORE * float(np.sqrt(n_sat / np.pi))


# ---------------------------------------------------------------------------
# The plane itself
# ---------------------------------------------------------------------------

# Logged when the source of the plane changes, never per frame.
_last_source: Optional[str] = None


def visibility_plane(
    img: Image.Image,
    model: FisheyeModel,
    dt: datetime,
    lat: float,
    lon: float,
    stabilizer: LabelStabilizer,
    obstruction_map: ObstructionMap,
    *,
    advance: bool = True,
    frame_is_observable: bool = False,
    crop: Optional[Tuple[int, ...]] = None,
    save_path: Optional[str] = None,
    evidence: Optional[SkyEvidence] = None,
) -> np.ndarray:
    """Return the 0/255 uint8 full-resolution plane the label tests read.

    ``advance`` is False for a reprocess of a capture already counted
    (``observing_window.SAME_CAPTURE_KEY``): the vote and the map are read,
    not fed. ``frame_is_observable`` is the observable-sky gate's verdict and
    gates the map alone: the vote follows every rendered frame. ``evidence``
    lets a caller that already detected stars (or a timing test) skip the
    detection.
    """
    w, h = img.size
    full_shape = (h, w)
    disc = model_sky_disc(model, full_shape)
    if advance:
        if evidence is None:
            evidence = detect_sky_evidence(img)
        moon = moon_glare_disc(img, model, dt, lat, lon, model_sky_radius(model))
        n = evidence.n_detections
        stabilizer.fold_mask(
            evidence.mask, full_shape, exclude=moon,
            partial=evidence.mask is not None and n < FULL_MASK_MIN_DETECTIONS)
        if frame_is_observable and evidence.mask is not None:
            changed = obstruction_map.update(
                evidence.mask, n_detections=n, frame_is_observable=True,
                full_shape=full_shape, reach_mask=evidence.reach,
                sky_region=disc, exclude=moon, crop=crop,
            )
            if changed and save_path:
                obstruction_map.maybe_save(save_path)

    vote, stale = stabilizer.current_small_vote()
    if vote is not None and vote.shape != disc.shape:
        # A reprocess after a size or crop change: the held vote is for
        # another grid and says nothing about this one.
        vote, stale = None, False
    map_sky = obstruction_map.small_sky_mask(w, h, crop)
    small, source = _select(vote, stale, map_sky, disc)
    _log_source_change(source)
    return upsample(small, full_shape)


def _select(vote, stale, map_sky, disc):
    """The grid plane (0/255) and the name of what decided it. Every branch
    is bounded by the model disc: unjudged pixels are sky to the vote and to
    the map, and must not become labels outside the sky circle."""
    if vote is not None and not stale:
        sky, source = (vote > 0), 'vote'
        if map_sky is not None:
            sky, source = sky & map_sky, 'vote+map'
    elif map_sky is not None:
        sky, source = map_sky, 'map'
    elif vote is not None:
        sky, source = (vote > 0), 'stale vote'
    else:
        sky, source = disc, 'model disc'
    return np.where(sky & disc, 255, 0).astype(np.uint8), source


def _log_source_change(source: str) -> None:
    global _last_source
    if source != _last_source:
        log.info(f"allsky: label visibility now from the {source}")
        _last_source = source
