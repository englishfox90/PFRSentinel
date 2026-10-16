"""
Frame-content evidence for the observable-sky gate.

The ML roof classifier is the roof source, but its verdict alone cannot say
whether a frame shows a sky worth annotating: a misread Open on a lit roof,
or an overcast night, still drew constellation labels (issue #93). This
module measures what the frame itself contains — how many star-like sources
the all-sky detector's thresholds find inside the sky circle, and how long
the exposure was — and stores it on the frame's metadata for
``observing_window`` to corroborate the roof verdict with.

Runs on the capture path for every frame, so it is built to a budget
(plan §8: ≤ 30 ms per 3552 px frame, single core): the frame is box-reduced
to a small plane, the sky circle is measured once and cached, and the count
skips the sub-pixel centroiding calibration needs. No threads, no timers,
nothing full-resolution kept beyond the call.
"""
import math
import re
import threading
from typing import Optional

import numpy as np

from .logger import app_logger

# Where the evidence rides on a frame's metadata. Underscore-prefixed like
# _ML_RESULTS: internal, never an overlay token.
EVIDENCE_KEY = '_SKY_EVIDENCE'

# Longest edge of the plane the star scan runs on. Every frame is resampled
# to exactly this size — down by area averaging, up by cubic interpolation —
# so the count means the same thing whatever the sensor, the user's Resize
# setting or the source (a 3552 px capture, a 60 % resize of it, a 750 px
# library JPEG): the count falls with the plane size (300 synthetic stars
# give 71 at 888 px but 17 at 710 px), so an integer reduction factor made
# a clear night sit at the floor on one rig and clear it on another.
# Measured on synthetic 3552 px all-sky frames, single core (scratch
# measurement, 2026-09-25): reading the full RGB frame once for the
# reduction is ~12 ms whatever the factor, the rest of the scan ~7 ms at
# 900 px, against ~50 ms for detect_stars on the same plane and 1.0-1.6 s
# at full resolution. The plan allows up to 1500 px, but the scan alone
# costs ~190 ms there. Only bloomed bright stars survive the reduction,
# and the floor (config_defaults: min_star_detections) is calibrated on this
# plane from the reference rig's 256 px community frames and 750 px library
# frames brought to it: closed roof 20-80 sources, clear night 206-445.
EVIDENCE_PLANE_EDGE_PX = 900
EVIDENCE_MAX_EDGE_PX = EVIDENCE_PLANE_EDGE_PX   # re-exported for existing callers

# The sky circle is a property of the lens mount, not the frame, and its
# measurement (72 radial scans + a circle fit) costs 10-20 ms on the reduced
# plane — the whole budget. Measured on the first frame of a size and every
# CIRCLE_REFRESH_FRAMES after (at the reporter's 10-30 s cadence, once per
# half hour or so), so a re-mounted camera is picked up within the session.
CIRCLE_REFRESH_FRAMES = 100

# The sky background for the scan is estimated at 1/BACKGROUND_REDUCE of the
# plane and interpolated back. detect_stars blurs at full plane size with a
# kernel of sky_r / 6 (59 taps at 888 px, ~14 ms single core); the same
# Gaussian at quarter scale is 15 taps on a sixteenth of the pixels (~1 ms).
# Sky glow varies over hundreds of pixels, so the interpolation changes the
# count by nothing measurable — see test_sky_evidence's agreement test.
BACKGROUND_REDUCE = 4

# Every SIGMA_STRIDE-th pixel of the residual feeds the noise estimate. The
# median of the full 888 px plane is ~8 ms; a 1/16 sample of ~30 000 sky
# pixels puts the MAD within ~1 % of the full figure, and the threshold is
# an integer multiple of it anyway.
SIGMA_STRIDE = 4

# detect_stars's thresholds, mirrored so the count means the same thing as
# the calibration feed's detections: 4 sigma over the local background,
# blob area 4-1000 px at the 750 px reference (scaled linearly with the sky
# radius), sources within 20 px of the circle edge ignored as horizon noise.
_THRESHOLD_SIGMA = 4.0
_MIN_AREA_REF = 4
_MAX_AREA_REF = 500
_EDGE_BORDER_PX = 20
_REFERENCE_SKY_RADIUS_PX = 375.0

_EXPOSURE_RE = re.compile(
    r'\s*([0-9]*\.?[0-9]+(?:[eE][-+]?[0-9]+)?)\s*'
    r'(µs|us|ms|s|sec|secs|seconds?|m|min|mins|minutes?)?\b',
    re.IGNORECASE,
)
_UNIT_SECONDS = {
    None: 1.0, 's': 1.0, 'sec': 1.0, 'secs': 1.0, 'second': 1.0, 'seconds': 1.0,
    'ms': 1e-3, 'µs': 1e-6, 'us': 1e-6,
    'm': 60.0, 'min': 60.0, 'mins': 60.0, 'minute': 60.0, 'minutes': 60.0,
}


def parse_exposure_seconds(text) -> Optional[float]:
    """Exposure in seconds from a metadata value, or None when unreadable.

    Accepts the camera pipeline's ``"30.0s"``, sidecar strings such as
    ``"30s"``, ``"500 ms"``, ``"32us"``, ``"2m"``, bare numbers (seconds,
    as NINA writes them), and int/float values. None never blocks a gate.
    """
    if text is None or isinstance(text, bool):
        return None
    if isinstance(text, (int, float)):
        value = float(text)
        return value if math.isfinite(value) and value >= 0 else None
    m = _EXPOSURE_RE.match(str(text))
    if not m:
        return None
    unit = (m.group(2) or '').lower() or None
    factor = _UNIT_SECONDS.get(unit)
    if factor is None:
        return None
    value = float(m.group(1)) * factor
    return value if math.isfinite(value) else None


def ml_star_signals(metadata: dict) -> dict:
    """The ML readings the gate corroborates with, from either pipeline.

    Camera mode carries the raw results in ``_ML_RESULTS``; Directory Watch
    mode only has the overlay tokens (``STARS_VISIBLE`` = Yes/No/N/A, and no
    static score at all). Returns ``stars_visible`` (True/False/None) and
    ``frame_is_static`` (bool).
    """
    ml = metadata.get('_ML_RESULTS')
    if isinstance(ml, dict):
        return {
            'stars_visible': ml.get('stars_visible'),
            'frame_is_static': bool(ml.get('frame_is_static')),
        }
    token = metadata.get('STARS_VISIBLE')
    visible = {'Yes': True, 'No': False}.get(token) if isinstance(token, str) else None
    return {'stars_visible': visible, 'frame_is_static': False}


def compute_sky_evidence(image, metadata: dict, config: dict, ignore_rects=None) -> dict:
    """Measure the frame and store the result as ``metadata[EVIDENCE_KEY]``.

    ``image`` is the pre-enhancement frame (PIL Image or numpy array) the
    calibration feed sees. The star scan is skipped — ``star_count`` None —
    when a cheaper rule already decides the frame: the sun is up, the
    exposure is under the floor, or ML reports sensor noise only. The gate
    treats None as "no evidence either way", never as "no stars".
    ``sky_circle`` is in the input frame's pixels, or None when no
    illuminated disc could be measured. ``ignore_rects`` — ``(x0, y0, x1,
    y1)`` boxes in frame pixels — are left out of the count; the pipelines
    measure the clean frame before any overlay is drawn and pass none, but
    a finished output frame with burned-in text (a library JPEG replayed in
    a test) needs its text boxes masked.
    """
    # config is a plain dict in camera mode and a services.config.Config in
    # Directory Watch mode; both answer .get().
    allsky_cfg = config.get('allsky_overlay', {}) or {}
    exposure_s = parse_exposure_seconds(metadata.get('EXPOSURE'))
    evidence = {
        'star_count': None,
        'exposure_s': exposure_s,
        'sky_circle': None,
        'frame_size': _frame_size(image),
    }
    metadata[EVIDENCE_KEY] = evidence

    floor = _exposure_floor(allsky_cfg)
    if exposure_s is not None and floor > 0 and exposure_s < floor:
        return evidence
    if ml_star_signals(metadata)['frame_is_static']:
        return evidence
    from .observing_window import sun_is_up
    if sun_is_up(config):
        return evidence

    try:
        count, circle = _scan(image, ignore_rects)
    except Exception as e:
        app_logger.debug(f"Sky evidence: star scan failed: {e}")
        return evidence
    evidence['star_count'] = count
    evidence['sky_circle'] = circle
    return evidence


def reduce_for_evidence(image):
    """The frame as a uint8 grey plane with a long edge of exactly
    EVIDENCE_PLANE_EDGE_PX.

    Returns ``(plane, scale)`` with ``scale`` = plane px per frame px, or
    ``(None, 1.0)`` for input it cannot read. Reduction is by area averaging
    (a PIL box ``reduce`` by the integer part of the factor, then cv2
    INTER_AREA to the exact size) so every source keeps its flux wherever
    it sits between the sampled pixels; NEAREST would make the count
    flicker with sub-pixel star motion. A frame smaller than the plane is
    interpolated up (cubic) so the area thresholds mean the same thing.
    """
    import cv2

    if hasattr(image, 'reduce') and hasattr(image, 'mode'):
        if image.mode[:1] in ('I', 'F'):
            # 16-bit or float data (a FITS opened in Watch mode): convert('L')
            # would clip it at 255, so it takes the array path and is
            # percentile-stretched after the reduction instead.
            return reduce_for_evidence(np.asarray(image))
        if image.mode not in ('L', 'RGB', 'RGBA'):
            image = image.convert('RGB')
        w, h = image.size
        # The integer box reduce reads the full RGB frame once (~12 ms at
        # 3552 px, whatever the factor); the exact resample below then runs
        # on a small plane. Nearest factor, not floor: 3552 px reduces 4x to
        # 888 and is interpolated up 1.4 % rather than reduced 3x to 1184 and
        # averaged down, which read the same frame but cost 8 ms more.
        factor = max(1, int(round(max(w, h) / EVIDENCE_PLANE_EDGE_PX)))
        small = image.reduce(factor) if factor > 1 else image
        plane = np.asarray(small.convert('L'), dtype=np.uint8)
        plane = _resample_to_plane(plane, cv2)
        return plane, plane.shape[1] / w

    if not isinstance(image, np.ndarray) or image.ndim not in (2, 3):
        return None, 1.0
    arr = image
    h, w = arr.shape[:2]
    if arr.ndim == 3 and arr.dtype == np.uint8:
        # Grey first: INTER_AREA on three channels of a 3552 px frame is
        # ~45 ms, the colour conversion plus a one-channel reduction ~10 ms.
        if arr.shape[2] == 4:
            arr = cv2.cvtColor(arr, cv2.COLOR_RGBA2GRAY)
        elif arr.shape[2] == 3:
            arr = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
        else:
            return None, 1.0
    if arr.ndim == 3 and arr.shape[2] not in (3, 4):
        return None, 1.0
    # Same two steps as the PIL path (integer box reduce, then the exact
    # resample) so the two inputs count alike.
    factor = max(1, int(round(max(h, w) / EVIDENCE_PLANE_EDGE_PX)))
    if factor > 1:
        arr = cv2.resize(arr, (max(1, w // factor), max(1, h // factor)),
                         interpolation=cv2.INTER_AREA)
    arr = _resample_to_plane(arr, cv2)
    if arr.dtype != np.uint8:
        from .allsky.star_centroid import percentile_stretch
        arr = percentile_stretch(arr)
    if arr.ndim == 3:
        arr = cv2.cvtColor(arr, cv2.COLOR_RGBA2GRAY if arr.shape[2] == 4 else cv2.COLOR_RGB2GRAY)
    return arr, arr.shape[1] / w


def _resample_to_plane(arr: np.ndarray, cv2) -> np.ndarray:
    h, w = arr.shape[:2]
    longest = max(h, w)
    if longest == EVIDENCE_PLANE_EDGE_PX:
        return arr
    scale = EVIDENCE_PLANE_EDGE_PX / longest
    size = (max(1, round(w * scale)), max(1, round(h * scale)))
    interpolation = cv2.INTER_AREA if scale < 1.0 else cv2.INTER_CUBIC
    return cv2.resize(arr, size, interpolation=interpolation)


def count_star_like_sources(plane: np.ndarray, circle, ignore_rects=None) -> int:
    """Number of star-like sources inside ``circle`` = (cx, cy, r) on a
    uint8 grey plane, by detect_stars's thresholds without its sub-pixel
    centroiding: local background subtracted, 4 sigma threshold, 3x3 open,
    blob area band, sources within the edge border ignored. ``ignore_rects``
    are ``(x0, y0, x1, y1)`` boxes in plane pixels left out of the count.
    """
    import cv2

    cx, cy, r = circle
    h, w = plane.shape
    linear_scale = max(1.0, r / _REFERENCE_SKY_RADIUS_PX)
    min_area = max(_MIN_AREA_REF, int(_MIN_AREA_REF * linear_scale))
    max_area = max(min_area + 1, int(_MAX_AREA_REF * linear_scale * 2))

    # detect_stars: GaussianBlur with ksize = max(31, r // 6) | 1, whose
    # sigma by OpenCV's rule is 0.3 * ((ksize - 1) * 0.5 - 1) + 0.8.
    ksize = max(31, int(r // 6)) | 1
    sigma_px = 0.3 * ((ksize - 1) * 0.5 - 1) + 0.8
    small = cv2.resize(plane, (max(1, w // BACKGROUND_REDUCE), max(1, h // BACKGROUND_REDUCE)),
                       interpolation=cv2.INTER_AREA)
    small = cv2.GaussianBlur(small, (0, 0), sigma_px / BACKGROUND_REDUCE)
    background = cv2.resize(small, (w, h), interpolation=cv2.INTER_LINEAR)
    residual = cv2.subtract(plane, background)   # uint8: clipped at 0 for the threshold

    mask = np.zeros((h, w), dtype=np.uint8)
    cv2.circle(mask, (int(round(cx)), int(round(cy))), int(round(r)), 255, -1)
    for x0, y0, x1, y1 in ignore_rects or ():
        mask[max(0, int(y0)):max(0, int(y1)), max(0, int(x0)):max(0, int(x1))] = 0

    # Noise from the SIGNED residual. detect_stars takes its MAD from the
    # clipped one, and on a noise-only frame that is a half-rectified
    # Gaussian whose median and MAD are both ~0: the threshold collapses to
    # the floor and every grain becomes a source. Measured on the reference
    # rig's closed-roof frames (256 px community FITS, 2026-07-18, 13 s at
    # gain 300; 750 px library JPEGs, 2026-09-17): 120-140 and ~950
    # "stars" on a dark closed roof with the clipped estimate, against
    # 15-50 on open clear nights. The signed MAD sees the amplified noise
    # and lifts the threshold above it.
    sub = mask[::SIGMA_STRIDE, ::SIGMA_STRIDE] > 0
    sample = (plane[::SIGMA_STRIDE, ::SIGMA_STRIDE][sub].astype(np.float32)
              - background[::SIGMA_STRIDE, ::SIGMA_STRIDE][sub].astype(np.float32))
    if sample.size == 0:
        return 0
    sigma = max(1.5, 1.4826 * float(np.median(np.abs(sample - np.median(sample)))))
    threshold = max(1, int(_THRESHOLD_SIGMA * sigma))

    _, binary = cv2.threshold(residual, threshold, 255, cv2.THRESH_BINARY)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    binary = cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel)
    binary = cv2.bitwise_and(binary, mask)

    n_labels, _labels, stats, centroids = cv2.connectedComponentsWithStats(binary, connectivity=8)
    if n_labels <= 1:
        return 0
    area = stats[1:, cv2.CC_STAT_AREA]
    dist = np.hypot(centroids[1:, 0] - cx, centroids[1:, 1] - cy)
    keep = (area >= min_area) & (area <= max_area) & (dist <= r - _EDGE_BORDER_PX)
    return int(np.count_nonzero(keep))


class _SkyCircleCache:
    """Measured sky circle per frame size, refreshed every CIRCLE_REFRESH_FRAMES."""

    def __init__(self):
        self._lock = threading.Lock()
        self._entries = {}   # frame size -> [circle or None, frames_since_measured]

    def circle(self, plane: np.ndarray, key):
        """``key`` identifies the source frame size: every plane is the same
        size, and two cameras' frames must not share a circle."""
        from .allsky.star_centroid import measure_sky_circle

        with self._lock:
            entry = self._entries.get(key)
            if entry is not None and entry[1] < CIRCLE_REFRESH_FRAMES:
                entry[1] += 1
                return entry[0]
        measured = measure_sky_circle(plane)
        with self._lock:
            self._entries[key] = [measured, 1]
        return measured

    def reset(self) -> None:
        with self._lock:
            self._entries.clear()


_circle_cache = _SkyCircleCache()


def reset_sky_evidence_cache() -> None:
    """Forget the cached sky circles (tests, and a camera change)."""
    _circle_cache.reset()


def _scan(image, ignore_rects=None):
    """Star count on the reduced plane. Returns ``(count, circle)`` with the
    circle in the input frame's pixels, or None for the circle when no
    illuminated disc could be measured — the count then uses the
    frame-centred fallback, without estimate_sky_circle's per-call WARNING,
    which on the capture path would fire every frame."""
    from .allsky.star_centroid import fallback_sky_circle

    plane, scale = reduce_for_evidence(image)
    if plane is None:
        return None, None
    circle = _circle_cache.circle(plane, _frame_size(image))
    cx, cy, r = circle if circle is not None else fallback_sky_circle(plane.shape[1], plane.shape[0])
    rects = [tuple(v * scale for v in rect) for rect in (ignore_rects or ())]
    count = count_star_like_sources(plane, (cx, cy, r), rects)
    circle_full = None
    if circle is not None:
        circle_full = (float(cx / scale), float(cy / scale), float(r / scale))
    return count, circle_full


def _exposure_floor(allsky_cfg: dict) -> float:
    try:
        return max(0.0, float(allsky_cfg.get('min_exposure_s', 0) or 0))
    except (TypeError, ValueError):
        return 0.0


def _frame_size(image):
    if hasattr(image, 'size') and hasattr(image, 'mode'):
        return tuple(image.size)
    if isinstance(image, np.ndarray) and image.ndim >= 2:
        return (int(image.shape[1]), int(image.shape[0]))
    return None
