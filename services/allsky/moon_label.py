"""
Where the Moon actually is on a frame, and how far its glare reaches.

The Moon was labelled like a planet: the text a few pixels from its
predicted centre, near-white, no outline. On a moonlit frame the bloom
around the Moon is tens of pixels across, so the label landed in it and
washed out — on the SFRO rig's 2026-09-28 00:06 frame (Moon at 51 deg) the
"M" of "Moon" was inside the glare and only "oon" could be read.

measure_moon_glare finds the saturated core near the predicted pixel, takes
its centroid as the Moon (the model can be tens of pixels out there; the
blob is where the Moon is) and walks the radial brightness profile out until
it falls most of the way back to the surrounding sky. The renderer anchors
the Moon's label on that centre, sets it beyond that radius and keeps other
labels out of the disc.

A measured core is also direct evidence that the Moon is in view, so the
label no longer depends on the visibility plane at the Moon's pixel — the
vote has nothing to say there, since the glare blanks every detection
around it. With no core (Moon behind the pier, in thick cloud, thin crescent
at a short exposure) nothing changes: the Moon is labelled like any planet,
visibility test included.

Only a window around the predicted Moon is read from the frame.
"""
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import numpy as np
from PIL import Image

from services.logger import app_logger as log

from .fisheye import FisheyeModel
from .sky_region import (
    MOON_MIN_SATURATED_PX, MOON_SATURATION_LEVEL, MOON_SEARCH_FRACTION,
    model_sky_radius,
)

# The glare is never taken as wider than this fraction of the sky radius: a
# hazy moonlit sky can stay bright a long way out, and a label pushed to the
# far side of the frame names nothing.
GLARE_MAX_FRACTION = 0.25

# The glare ends where the radial profile has fallen this fraction of the way
# from saturation back to the surrounding sky. On the rig's moonlit frames
# text over the inner quarter of that drop could not be read.
GLARE_EDGE_FRACTION = 0.25

# The profile is never taken as ending inside the saturated core itself.
GLARE_MIN_OVER_CORE = 1.2

MEAN_SHIFT_PASSES = 6

# Width, in reduced pixels, of the window the glare is measured on.
MEASURE_SPAN_PX = 320


@dataclass(frozen=True)
class MoonGlare:
    """The Moon as seen: centre of the saturated core and the glare radius,
    in frame pixels."""
    x: float
    y: float
    core_r: float
    radius: float


def moon_pixel(model: FisheyeModel, dt: datetime, lat: float,
               lon: float) -> Optional[tuple]:
    """Predicted (x, y) of the topocentric Moon, or None below the horizon."""
    try:
        from .coords import radec_to_altaz
        from .planets import moon_radec_topocentric
        ra, dec = moon_radec_topocentric(dt, lat, lon)
        alt, az = radec_to_altaz(ra, dec, lat, lon, dt, refraction=True)
        if float(alt) < 0.0:
            return None
        return model.altaz_to_pixel(float(alt), float(az))
    except Exception as e:
        log.debug(f"allsky: Moon position unavailable for its label: {e}")
        return None


def measure_moon_glare(img: Image.Image, model: FisheyeModel, dt: datetime,
                       lat: float, lon: float) -> Optional[MoonGlare]:
    """The Moon's glare on ``img`` near its predicted pixel, or None when no
    saturated core is found there (or the Moon is down / off the frame)."""
    xy = moon_pixel(model, dt, lat, lon)
    if xy is None:
        return None
    try:
        return glare_at(img, float(xy[0]), float(xy[1]), model_sky_radius(model))
    except Exception as e:
        log.debug(f"allsky: Moon glare not measured: {e}")
        return None


def glare_at(img: Image.Image, mx: float, my: float,
             sky_r: float) -> Optional[MoonGlare]:
    """Measure the glare around a saturated core within the search radius of
    (mx, my). Pure apart from reading ``img``."""
    w, h = img.size
    if not (0 <= mx < w and 0 <= my < h) or sky_r <= 0:
        return None
    search = MOON_SEARCH_FRACTION * sky_r
    max_r = GLARE_MAX_FRACTION * sky_r
    reach = search + max_r
    x0, x1 = max(0, int(mx - reach)), min(w, int(mx + reach) + 1)
    y0, y1 = max(0, int(my - reach)), min(h, int(my + reach) + 1)
    if x0 >= x1 or y0 >= y1:
        return None
    # Box-reduced so the work is ~MEASURE_SPAN_PX across at any frame size: a
    # full-resolution window at 3552 px is over a million pixels. A reduced
    # pixel inside the core is still saturated (every source pixel is).
    step = max(1, int(round(2.0 * reach / MEASURE_SPAN_PX)))
    patch = img.crop((x0, y0, x1, y1)).convert('L')
    if step > 1:
        patch = patch.reduce(step)
    window = np.asarray(patch, dtype=np.float32)
    rows, cols = window.shape
    yy = y0 + (np.arange(rows, dtype=np.float32) + 0.5) * step - 0.5
    xx = x0 + (np.arange(cols, dtype=np.float32) + 0.5) * step - 0.5
    yy, xx = np.broadcast_arrays(yy[:, None], xx[None, :])

    saturated = window >= MOON_SATURATION_LEVEL
    sat = saturated & ((xx - mx) ** 2 + (yy - my) ** 2 <= search * search)
    if int(np.count_nonzero(sat)) * step * step < MOON_MIN_SATURATED_PX:
        return None
    # The search circle is centred on the prediction, so a core it only
    # partly covers pulls the centroid toward the prediction. Re-centre the
    # circle on the centroid until it settles (the rig's 2026-09-28 frame:
    # 18 px of 750 on the first pass, under 1 px after three).
    cx, cy = float(xx[sat].mean()), float(yy[sat].mean())
    for _ in range(MEAN_SHIFT_PASSES):
        sat = saturated & ((xx - cx) ** 2 + (yy - cy) ** 2 <= search * search)
        if not sat.any():
            return None
        nx, ny = float(xx[sat].mean()), float(yy[sat].mean())
        settled = abs(nx - cx) < 0.5 and abs(ny - cy) < 0.5
        cx, cy = nx, ny
        if settled:
            break
    core_r = float(np.sqrt(np.count_nonzero(sat) / np.pi)) * step

    # Profile in source pixels, one bin per reduced pixel of radius.
    r = np.hypot(xx - cx, yy - cy)
    inside = r <= max_r
    bins = (r[inside] / step).astype(np.int32)
    sums = np.bincount(bins, weights=window[inside])
    counts = np.bincount(bins)
    with np.errstate(invalid='ignore', divide='ignore'):
        profile = sums / counts
    # Surrounding sky: the outer fifth of the measured profile.
    outer = profile[int(len(profile) * 0.8):]
    outer = outer[np.isfinite(outer)]
    sky = float(np.median(outer)) if outer.size else float(np.percentile(window, 25))
    edge_level = sky + GLARE_EDGE_FRACTION * (float(MOON_SATURATION_LEVEL) - sky)

    start = int(np.ceil(core_r * GLARE_MIN_OVER_CORE / step))
    radius = max_r
    for i in range(start, len(profile)):
        if counts[i] and profile[i] < edge_level:
            radius = float(i * step)
            break
    radius = min(max_r, max(radius, core_r * GLARE_MIN_OVER_CORE))
    return MoonGlare(cx, cy, core_r, radius)


def label_gap(glare: MoonGlare, label_h: float) -> float:
    """Distance from the Moon's centre to the nearest edge of its label: past
    the glare by a little under half a line. Diagonal slots sit 0.99 of the
    gap from the centre, so the gap clears the reserved disc on every side."""
    return glare.radius / 0.98 + 0.4 * label_h


def text_halo(label_px: int, opacity: int) -> dict:
    """``ImageDraw.text`` keyword arguments for a dark outline that keeps the
    Moon's near-white name readable where the sky is bright."""
    return {
        'stroke_width': max(1, int(round(label_px / 8.0))),
        'stroke_fill': (0, 0, 0, int(max(0, min(255, opacity)) * 0.85)),
    }
