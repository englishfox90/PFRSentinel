"""
The one star-source scan: noise-adaptive, masked to the sky circle.

Two things use it. ``star_detection.analyze_stars`` runs it on the frame at
its own resolution: that count is THE star count — the ``{STAR_COUNT}``
token, the status strip, the library column, the FWHM star list and the
imaging-conditions feed (``stars.count`` in ``GET /conditions``, the
baseline, the ratio and the trend). ``sky_evidence`` runs the same scan on a
fixed 900 px plane for the observable-sky gate, which has to decide before
star detection whether detection runs at all; that plane count is internal
and never shown as a star count.

Method: a smooth sky background (a Gaussian at quarter scale, interpolated
back) is subtracted; the noise is the MAD of the SIGNED residual inside the
mask; pixels 4 sigma over the background are opened 3x3 and labelled; blobs
inside the area band and clear of the circle's edge are sources.

Per-frame cost on a synthetic 3552x3552 RGB all-sky frame (300 stars drawn,
12 runs, 4-core cloud container, 2026-11-24; ``tests/test_star_source_scan``
logs the same figures on whatever machine runs it):

    before  detect_stars (31 px median background)   p50 193 ms   173 stars
            + estimate_fwhm (float copy of the frame) p50 197 ms
    after   scan_frame (count + positions)            p50  55 ms   273 stars
            analyze_stars (scan + FWHM, same plane)   p50  63 ms
            the same on a linear uint16 frame         p50  70 ms   278 stars

(On the owner's machine the old count measured 128 ms and this scan, before
the scaled background, 45 ms.) The connected-component pass is ~20 ms of
what is left. Counts are validated on synthetic frames only: 271-278 of the
300 drawn stars at either bit depth, where the fixed threshold of 30 found
173. The FWHM pass measures cutouts of the grey plane the scan already
made instead of converting the frame a second time.
"""
from typing import List, Optional, Tuple

import numpy as np

# Background Gaussian estimated at 1/BACKGROUND_REDUCE of the plane. Sky
# glow varies over hundreds of pixels, so interpolating it back changes the
# count by nothing measurable.
BACKGROUND_REDUCE = 4

# Every SIGMA_STRIDE-th pixel of the residual feeds the noise estimate.
SIGMA_STRIDE = 4

# Both figures above are for a plane of this edge (the gate's). A larger
# frame scales them with its size, so the background is always blurred on a
# plane of about 225 px and the noise taken from a few ten thousand pixels:
# at 3552 px the fixed figures cost ~20 ms for the blur and ~10 ms for the
# two medians, the scaled ones under 2 ms together, for the same count.
_REFERENCE_PLANE_EDGE_PX = 900

# 4 sigma over the local background, blob area 4-1000 px at the 750 px
# reference (scaled linearly with the sky radius), sources within 20 px of
# the circle edge ignored as horizon noise.
THRESHOLD_SIGMA = 4.0
MIN_AREA_REF = 4
MAX_AREA_REF = 500
EDGE_BORDER_PX = 20
REFERENCE_SKY_RADIUS_PX = 375.0

# Quantisation floor of the noise estimate, in the plane's own levels: a
# frame quieter than this has no measurable noise and the threshold would
# collapse to zero. Not scaled for uint16: a linear RAW16 frame uses a small
# part of its range, and a floor of 1.5 x 257 sat above its real noise
# (measured: 117 stars against 271 on the same synthetic frame at 8 bits).
_SIGMA_FLOOR = 1.5

Circle = Tuple[float, float, float]


def find_star_like_sources(plane: np.ndarray, circle: Optional[Circle],
                           ignore_rects=None, background_reduce: int = BACKGROUND_REDUCE,
                           sigma_stride: int = SIGMA_STRIDE) -> np.ndarray:
    """Star-like sources on a grey plane, as an ``(N, 3)`` float array of
    ``x, y, area`` in plane pixels.

    ``plane`` is uint8 or uint16 (a linear RAW16 frame is scanned as it is —
    the threshold is relative to its own noise, so no stretch is needed).
    ``circle`` = ``(cx, cy, r)`` limits the scan to the sky disc; None scans
    the whole plane (a frame with no measurable disc: a telescope sub in
    Directory Watch, or an all-sky night too dark to show its edge).
    ``ignore_rects`` are ``(x0, y0, x1, y1)`` boxes left out.
    """
    import cv2

    h, w = plane.shape
    if circle is not None:
        cx, cy, r = circle
    else:
        cx, cy, r = w / 2.0, h / 2.0, min(w, h) / 2.0
    linear_scale = max(1.0, r / REFERENCE_SKY_RADIUS_PX)
    min_area = max(MIN_AREA_REF, int(MIN_AREA_REF * linear_scale))
    max_area = max(min_area + 1, int(MAX_AREA_REF * linear_scale * 2))

    # A Gaussian of ksize = max(31, r // 6) | 1, whose sigma by OpenCV's rule
    # is 0.3 * ((ksize - 1) * 0.5 - 1) + 0.8, run at reduced scale.
    ksize = max(31, int(r // 6)) | 1
    sigma_px = 0.3 * ((ksize - 1) * 0.5 - 1) + 0.8
    small = cv2.resize(plane, (max(1, w // background_reduce), max(1, h // background_reduce)),
                       interpolation=cv2.INTER_AREA)
    small = cv2.GaussianBlur(small, (0, 0), sigma_px / background_reduce)
    background = cv2.resize(small, (w, h), interpolation=cv2.INTER_LINEAR)
    residual = cv2.subtract(plane, background)   # unsigned: clipped at 0 for the threshold

    mask = None
    if circle is not None or ignore_rects:
        mask = np.zeros((h, w), dtype=np.uint8)
        if circle is not None:
            cv2.circle(mask, (int(round(cx)), int(round(cy))), int(round(r)), 255, -1)
        else:
            mask[:] = 255
        for x0, y0, x1, y1 in ignore_rects or ():
            mask[max(0, int(y0)):max(0, int(y1)), max(0, int(x0)):max(0, int(x1))] = 0

    # Noise from the SIGNED residual. The MAD of the clipped one is that of
    # a half-rectified Gaussian on a noise-only frame — median and MAD both
    # ~0 — so the threshold collapses to the floor and every grain becomes a
    # source. Measured on the reference rig's closed-roof frames (256 px
    # community FITS, 2026-07-18, 13 s at gain 300; 750 px library JPEGs,
    # 2026-09-17): 120-140 and ~950 "stars" on a dark closed roof with the
    # clipped estimate, against 15-50 on open clear nights. The signed MAD
    # sees the amplified noise and lifts the threshold above it.
    plane_s = plane[::sigma_stride, ::sigma_stride]
    back_s = background[::sigma_stride, ::sigma_stride]
    if mask is not None:
        sub = mask[::sigma_stride, ::sigma_stride] > 0
        plane_s, back_s = plane_s[sub], back_s[sub]
    sample = plane_s.astype(np.float32) - back_s.astype(np.float32)
    if sample.size == 0:
        return np.empty((0, 3), dtype=np.float64)
    sigma = max(_SIGMA_FLOOR,
                1.4826 * float(np.median(np.abs(sample - np.median(sample)))))
    threshold = max(1, int(THRESHOLD_SIGMA * sigma))

    binary = cv2.compare(residual, threshold, cv2.CMP_GT)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    binary = cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel)
    if mask is not None:
        binary = cv2.bitwise_and(binary, mask)

    n_labels, _labels, stats, centroids = cv2.connectedComponentsWithStats(binary, connectivity=8)
    if n_labels <= 1:
        return np.empty((0, 3), dtype=np.float64)
    area = stats[1:, cv2.CC_STAT_AREA]
    keep = (area >= min_area) & (area <= max_area)
    if circle is not None:
        dist = np.hypot(centroids[1:, 0] - cx, centroids[1:, 1] - cy)
        keep &= dist <= r - EDGE_BORDER_PX
    return np.column_stack([centroids[1:][keep], area[keep].astype(np.float64)])


def grey_plane(image: np.ndarray) -> Optional[np.ndarray]:
    """``image`` (grey, RGB or RGBA; uint8 or uint16) as one grey plane of
    the same depth, or None for input the scan cannot read. Other dtypes
    are min-max normalised to uint8."""
    import cv2

    if not isinstance(image, np.ndarray) or image.size == 0 or image.ndim not in (2, 3):
        return None
    if image.ndim == 3:
        if image.shape[2] == 3:
            image = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
        elif image.shape[2] == 4:
            image = cv2.cvtColor(image, cv2.COLOR_RGBA2GRAY)
        elif image.shape[2] == 1:
            image = image[:, :, 0]
        else:
            return None
    if image.dtype not in (np.uint8, np.uint16):
        image = cv2.normalize(image, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
    return image


def circle_in_frame(sky_circle, circle_frame_size, frame_size) -> Optional[Circle]:
    """``sky_circle`` — measured on a frame of ``circle_frame_size`` (w, h) —
    in the pixels of a frame of ``frame_size``.

    The two pipelines measure the evidence circle on a different copy of
    the frame than the one stars are counted on (camera mode: the resized
    stretched copy; Directory Watch: the same size), so the circle is
    scaled. A different aspect ratio means a different framing, not a
    resize: the circle cannot be placed and None (whole frame) is returned.
    """
    try:
        cx, cy, r = (float(v) for v in sky_circle)
        w, h = (float(v) for v in frame_size)
    except (TypeError, ValueError):
        return None
    if not (r > 0 and w > 0 and h > 0):
        return None
    if not circle_frame_size:
        return cx, cy, r
    try:
        cw, ch = (float(v) for v in circle_frame_size)
    except (TypeError, ValueError):
        return None
    if cw <= 0 or ch <= 0:
        return None
    sx, sy = w / cw, h / ch
    if abs(sx - sy) > 0.02 * max(sx, sy):
        return None
    return cx * sx, cy * sy, r * (sx + sy) / 2.0


def scan_frame(image: np.ndarray, sky_circle=None,
               circle_frame_size=None) -> Tuple[List[dict], Optional[np.ndarray]]:
    """Stars on ``image`` at its own resolution.

    Returns ``(stars, grey)``: star dicts with ``x``, ``y`` (int pixels) and
    ``size`` (equivalent diameter), and the grey plane they were found on,
    for the FWHM pass to reuse. ``sky_circle`` is the evidence scan's circle
    for this frame with the size of the frame it was measured on.
    """
    grey = grey_plane(image)
    if grey is None:
        return [], None
    h, w = grey.shape
    circle = None
    if sky_circle is not None:
        circle = circle_in_frame(sky_circle, circle_frame_size, (w, h))
    factor = max(1, int(round(max(w, h) / _REFERENCE_PLANE_EDGE_PX)))
    sources = find_star_like_sources(grey, circle,
                                     background_reduce=BACKGROUND_REDUCE * factor,
                                     sigma_stride=SIGMA_STRIDE * factor)
    stars = [{'x': int(x), 'y': int(y), 'size': float(np.sqrt(area / np.pi) * 2)}
             for x, y, area in sources]
    return stars, grey
