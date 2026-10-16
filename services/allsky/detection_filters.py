"""
Per-detection rejection rules for star_centroid.detect_stars.

detect_stars keeps every connected component that passes an area window;
on a hosting-site rig (issue #93, plan §0.3) that admits three things that
are not stars and that feed the pole finder and the joint fit:

  streaks  — satellite / aircraft trails, silhouette edges lit by a nearby
             light, cables: elongated components;
  glints   — bright specks on the rim of a dark silhouette (a dew-heater
             LED reflected off a dew shield, the lit edge of a pier): a
             point source whose neighbourhood contains equipment-dark
             pixels, which open sky never does;
  text     — burned-in overlay boxes when the library's finished JPEGs
             are replayed (plan §7.1): the caller knows where they are.

The dark-neighbourhood rule complements the equipment map (package 1):
the map removes the interior of equipment, this removes its rim.

Constants were fixed on synthetic stretched frames with drawn silhouettes
(tests/test_detection_filters.py); the reporter's stills were not on hand
when this was written, so the numbers below carry the plan's reading of
them (§0.3) and the synthetic confirmation, not a real-frame measurement.
Pure functions; detect_stars applies them through DetectionFilters.
"""
from dataclasses import dataclass, field
from typing import Optional, Sequence, Tuple

import numpy as np

try:
    import cv2
except ImportError:   # pragma: no cover - star_centroid raises the same way
    cv2 = None

# A component whose bounding box is longer than this many times its width
# is a streak. A star's PSF is round; JPEG ringing and a slight coma stay
# under 2:1, a 6-px trail of a satellite in a 20 s frame is over 3:1.
ASPECT_MAX = 3.2
# ...but only once the long side exceeds this many pixels (× the detector's
# resolution scale): a 3×1 px component is noise-shaped, not a trail.
STREAK_MIN_LONG_PX = 6.0

# Radius (× resolution scale) of the neighbourhood whose darkest pixel is
# inspected. 10 px reaches past a star's own halo to the sky around it and
# from a rim glint into the silhouette behind it.
EDGE_R_PX = 10.0

# Darkest neighbour below this level (0–255, stretched frame) means the
# source sits against equipment or the frame edge. Open sky on a stretched
# frame sits at 45–90 on the plan's reading of the reporter's stills; the
# silhouettes read under 20.
DARK_FLOOR = 35
# ...unless the whole sky is dark: a moonless short exposure can stretch to
# a sky of 20–30, and a fixed floor would then reject every star. The
# floor is therefore capped at this fraction of the sky's own median, so
# "dark" always means darker than the sky, never merely dark.
DARK_SKY_FRACTION = 0.5


@dataclass(frozen=True)
class DetectionFilters:
    """Which rules detect_stars applies, plus the caller's ignore boxes.

    The dark-neighbourhood rule is OFF by default. Measured on the reference
    rig's 2026-09-18 library night (59 finished 750 px JPEGs, guided model
    as truth, tests/test_allsky_pool_hygiene_real.py): with the plan's
    constants it dropped 33 % of all candidates and 35 % of the ones on
    catalogue stars — the same proportion, so it told stars from equipment
    texture no better than a coin — and every (floor 15–35, radius 4–10 px)
    setting of a twelve-cell sweep did the same. At 750 px a dark pixel
    within 10 px is JPEG grain or dark sky, not a silhouette. The rule
    stays available for a caller that has measured it on full-resolution
    frames; until then it removes stars for nothing.
    """
    reject_streaks: bool = True
    reject_dark_neighbourhood: bool = False
    # (x0, y0, x1, y1) boxes, inclusive-exclusive, in image pixels.
    ignore_rects: Sequence[Tuple[int, int, int, int]] = field(default_factory=tuple)


def is_streak(bbox_w: float, bbox_h: float, scale: float = 1.0,
              component_mask: Optional[np.ndarray] = None) -> bool:
    """Elongation rule on a component.

    The bounding-box aspect catches axis-aligned trails; a diagonal trail
    fills a squarish box (a 30 px trail at 27° measured 30 × 17), so when
    the caller passes the component's pixel mask the elongation is taken
    from the mask's principal axes instead.
    """
    long_side = max(bbox_w, bbox_h)
    if long_side <= STREAK_MIN_LONG_PX * scale:
        return False
    short_side = max(min(bbox_w, bbox_h), 1.0)
    if long_side / short_side > ASPECT_MAX:
        return True
    if component_mask is None:
        return False
    ys, xs = np.nonzero(component_mask)
    if len(xs) < 3:
        return False
    cov = np.cov(np.vstack([xs, ys]).astype(np.float64))
    ev = np.linalg.eigvalsh(cov)
    return float(np.sqrt(max(ev[1], 0.0) / max(ev[0], 1.0 / 12.0))) > ASPECT_MAX


def in_ignore_rect(x: float, y: float,
                   rects: Sequence[Tuple[int, int, int, int]]) -> bool:
    for x0, y0, x1, y1 in rects:
        if x0 <= x < x1 and y0 <= y < y1:
            return True
    return False


def dark_neighbourhood_map(gray: np.ndarray, scale: float = 1.0) -> np.ndarray:
    """Darkest pixel within EDGE_R_PX × scale of each pixel (a min filter)."""
    r = max(1, int(round(EDGE_R_PX * scale)))
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1))
    return cv2.erode(gray, kernel)


def dark_threshold(gray: np.ndarray, sky_mask: Optional[np.ndarray] = None) -> float:
    """The level below which a neighbour counts as equipment-dark."""
    sky = gray[sky_mask > 0] if sky_mask is not None else gray
    if sky.size == 0:
        return float(DARK_FLOOR)
    return min(float(DARK_FLOOR), DARK_SKY_FRACTION * float(np.median(sky)))


def has_dark_neighbour(dark_map: np.ndarray, x: float, y: float,
                       threshold: float) -> bool:
    h, w = dark_map.shape
    xi = min(max(int(round(x)), 0), w - 1)
    yi = min(max(int(round(y)), 0), h - 1)
    return float(dark_map[yi, xi]) < threshold
