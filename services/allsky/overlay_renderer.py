"""
Main all-sky overlay entry point.

render_allsky_overlay(img, config, metadata) → PIL.Image

Orchestrates: grid → constellations → Messier → NGC → planets.
All layers share a single LabelGrid for collision avoidance.
Fails silently if model not calibrated or any layer errors.
"""
from dataclasses import replace
from datetime import datetime, timezone
from typing import Optional

import numpy as np
from PIL import Image, ImageDraw

from services.logger import app_logger as log
from services.output_crop import METADATA_KEY as CROP_METADATA_KEY, CropBox

# Pre-import scipy at module level so background threads never trigger
# a first-time import (causes segfault in PyInstaller builds).
try:
    from scipy.spatial.distance import cdist as _cdist
except Exception as _e:
    _cdist = None
    log.error(f"scipy.spatial import failed in overlay_renderer: {type(_e).__name__}: {_e}")

from .fisheye import FisheyeModel
from .label_collision import LabelGrid
from .label_stability import get_label_stabilizer
from .render_grid import render_grid
from .render_constellations import render_constellations
from .render_objects import render_messier, render_ngc, render_planets, _is_sky_visible
from .render_stars import render_bright_stars, star_uid, star_display_name
from .detection_filters import DetectionFilters
from .star_centroid import detect_stars, estimate_sky_circle

# One-shot guard so the model-scaling INFO log fires once per scale factor
# rather than on every rendered frame (Phase 3.1).
_last_scale_logged: Optional[float] = None

# Local brightness, as a fraction of the frame's sky level, at which a
# detection's claim on the sky around it is smallest (equipment edge) and
# largest (open sky). 20/110 and 60/110: the former absolute thresholds at the
# sky level of the frame they were tuned on.
_DIM_FRACTION = 0.18
_SKY_FRACTION = 0.55


def _detect_sky_mask(img: Image.Image) -> Optional[np.ndarray]:
    """Build a sky visibility mask from actual star detections.

    Each detected star claims a circular region whose radius is based on
    the 2nd-nearest-neighbour distance (robust to isolated edge stars)
    and weighted by local image brightness — bright open sky gets full
    radius, dim equipment edges get small radii so labels don't bleed
    onto obstructed areas.

    Returns a uint8 array (0 = obstructed, 255 = open sky) that is a
    drop-in replacement for the grayscale image in ``_is_sky_visible()``,
    or None when the frame yields no usable mask (< 10 stars detected,
    detection error). The caller decides how to fall back.
    """
    try:
        sky_cx, sky_cy, sky_r = estimate_sky_circle(img)
        detections = detect_stars(
            img, max_stars=200,
            sky_cx=sky_cx, sky_cy=sky_cy, sky_radius=sky_r,
            filters=DetectionFilters(),
        )
    except Exception:
        return None

    if len(detections) < 10:
        return None

    w, h = img.size
    det_xy = np.array([(x, y) for x, y, _ in detections])

    # 2nd nearest neighbour distance (more robust than 1st to outliers)
    if _cdist is None:
        return None
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
    radii = (base_radii * weight).astype(int)

    mask = Image.new('L', (w, h), 0)
    draw = ImageDraw.Draw(mask)
    for (x, y), r in zip(det_xy, radii):
        draw.ellipse([(x - r, y - r), (x + r, y + r)], fill=255)
    return np.array(mask)


def render_allsky_overlay(
    img: Image.Image,
    config: dict,
    metadata: dict,
) -> Image.Image:
    """
    Add astronomical overlays to an all-sky camera image.

    Reads calibration model path from config['calibration_file'].
    Observer location from config keys '_lat', '_lon', '_elevation'
    (injected by the pipeline callers).

    Args:
        img: PIL Image (RGB or RGBA) to annotate.
        config: allsky_overlay config dict.
        metadata: Frame metadata dict (may contain timestamp keys).

    Returns:
        Modified PIL Image (same mode as input).
    """
    if not config.get('enabled', False):
        return img

    original_mode = img.mode

    # --- Load fisheye model ---
    model = _load_model(config)
    if model is None:
        return img  # Silently skip — not calibrated

    # --- Fit the model to the frame we are drawing on ---
    # An OUTPUT_CROP in the metadata means the caller cut a sub-rectangle out of
    # the frame the model was scaled for (issue #12). That has to TRANSLATE the
    # optical centre, not scale it — and a square-to-square crop has the same
    # aspect ratio as the full frame, so the aspect test below cannot tell the
    # two apart on its own. Scale first (resize_percent), then translate.
    crop = CropBox.from_metadata(metadata.get(CROP_METADATA_KEY))
    if crop is not None and img.size != (crop.width, crop.height):
        log.warning(
            f"allsky: OUTPUT_CROP says {crop.width}x{crop.height} but the image is "
            f"{img.width}x{img.height}; skipping overlay rather than misplace it."
        )
        return img
    if crop is not None:
        if model.image_width > 0 and model.image_height > 0:
            model = _scale_model_to(model, crop.frame_width, crop.frame_height)
            if model is None:
                return img
        model = replace(
            model,
            cx=model.cx - crop.x, cy=model.cy - crop.y,
            image_width=crop.width, image_height=crop.height,
        )
    elif model.image_width > 0 and model.image_height > 0:
        model = _scale_model_to(model, *img.size)
        if model is None:
            return img

    # --- Determine observation time ---
    # Prefer the authoritative true-UTC instant injected by the preview path
    # (render_allsky_for_preview), which matches the clock the calibration uses.
    # Only fall back to the metadata {DATETIME} token (naive local time) for
    # direct callers such as tests and the dev debug tool; that legacy path
    # still honours utc_offset_hours.
    dt = _get_obs_utc(config)
    if dt is None:
        dt = _get_datetime(metadata)
        utc_offset = float(config.get('utc_offset_hours', 0) or 0)
        if utc_offset != 0.0:
            from datetime import timedelta
            dt = dt - timedelta(hours=utc_offset)

    # --- Observer location ---
    lat = float(config.get('_lat', 0.0))
    lon = float(config.get('_lon', 0.0))
    if lat == 0.0 and lon == 0.0:
        log.debug("allsky: lat/lon not configured, overlays may be inaccurate")

    # --- Ensure RGBA for compositing ---
    if img.mode != 'RGBA':
        img = img.convert('RGBA')

    w, h = img.size
    stabilizer = get_label_stabilizer()
    grid_cfg = LabelGrid(w, h, slot_memory=stabilizer.slot_memory)

    # Build a sky visibility mask from star detections.  Pixels near
    # detected stars are 255 (open sky); all others are 0 (obstructed).
    # This replaces the old brightness-threshold approach which was
    # fragile across different FITS stretches and scattered light levels.
    # The stabilizer votes the mask over recent frames and holds the last
    # vote through a short run of frames with too few detections; only a
    # sustained failure falls back to the raw grayscale (issues #31, #13).
    gray = stabilizer.smooth_mask(_detect_sky_mask(img))
    if gray is None:
        gray = np.array(img.convert('L'))

    # Layer order: grid first (background), then constellations, then objects
    try:
        grid_config = config.get('grid', {})
        img = render_grid(img, model, grid_config)
    except Exception as e:
        log.warning(f"allsky grid render failed: {e}")

    try:
        con_config = config.get('constellations', {})
        img = render_constellations(img, model, con_config, lat, lon, dt, grid_cfg,
                                    sky_gray=gray)
    except Exception as e:
        log.warning(f"allsky constellation render failed: {e}")

    # Pre-compute global allowed object set (top_n across all types combined)
    # Uses the ORIGINAL gray (no overlays) for accurate equipment detection
    allowed_ids = _compute_allowed_ids(config, model, lat, lon, dt, gray)

    try:
        stars_config = config.get('bright_stars', {})
        img = render_bright_stars(img, model, stars_config, lat, lon, dt, grid_cfg,
                                  allowed_ids, sky_gray=gray)
    except Exception as e:
        log.warning(f"allsky bright stars render failed: {e}")

    try:
        messier_config = config.get('messier', {})
        img = render_messier(img, model, messier_config, lat, lon, dt, grid_cfg,
                             allowed_ids, sky_gray=gray)
    except Exception as e:
        log.warning(f"allsky messier render failed: {e}")

    try:
        ngc_config = config.get('ngc', {})
        img = render_ngc(img, model, ngc_config, lat, lon, dt, grid_cfg,
                         allowed_ids, sky_gray=gray)
    except Exception as e:
        log.warning(f"allsky NGC render failed: {e}")

    try:
        planet_config = config.get('planets', {})
        img = render_planets(img, model, planet_config, lat, lon, dt, grid_cfg,
                             allowed_ids, sky_gray=gray)
    except Exception as e:
        log.warning(f"allsky planet render failed: {e}")

    # Restore original mode
    if original_mode != 'RGBA':
        img = img.convert(original_mode)

    return img


def render_allsky_for_preview(
    output_img: Image.Image,
    allsky_cfg: dict,
    config: dict,
    metadata: dict,
) -> Image.Image:
    """Return output_img overlaid with all-sky graphics for GUI preview only.

    Guards: enabled flag, calibration_file present, observing window open.
    Returns the original image unchanged when any guard fails — callers must
    not mutate the return value in that case.
    """
    if not allsky_cfg.get('enabled', False):
        return output_img
    if not allsky_cfg.get('calibration_file', ''):
        return output_img
    try:
        from services.observing_window import is_observing_window
        if not is_observing_window(config, metadata, feature="All-sky overlay"):
            return output_img
        weather_cfg = config.get('weather', {})
        cfg = dict(allsky_cfg)
        cfg['_lat'] = float(weather_cfg.get('latitude', 0) or 0)
        cfg['_lon'] = float(weather_cfg.get('longitude', 0) or 0)
        cfg['_elevation'] = float(weather_cfg.get('elevation', 0) or 0)
        cfg['_obs_utc'] = datetime.now(timezone.utc).isoformat()
        return render_allsky_overlay(output_img.copy(), cfg, metadata)
    except Exception as e:
        log.debug(f"All-sky preview render skipped: {e}")
        return output_img


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _scale_model_to(
    model: FisheyeModel, w_target: int, h_target: int,
) -> Optional[FisheyeModel]:
    """Rescale a model's pixel coordinates to a differently sized frame.

    resize_percent < 100 shrinks the image after calibration; the model's pixel
    coordinates (cx, cy, a1, a3, a5) must scale proportionally or every
    projected star lands in the wrong pixel. Returns ``None`` when the aspect
    ratios disagree — the frame was cropped by something that did not declare
    an OUTPUT_CROP, and scaling would silently misalign the whole overlay.
    """
    w_model, h_model = model.image_width, model.image_height
    if w_target == w_model and h_target == h_model:
        return model

    ar_model = w_model / h_model
    ar_target = w_target / h_target
    if abs(ar_model - ar_target) / max(ar_model, ar_target) > 0.02:
        log.warning(
            f"allsky: aspect-ratio mismatch — calibrated at "
            f"{w_model}x{h_model}, rendering at {w_target}x{h_target}. "
            "Image was cropped, not resized; skipping overlay to avoid "
            "misalignment."
        )
        return None

    s = w_target / w_model
    global _last_scale_logged
    if s != _last_scale_logged:
        log.info(
            f"allsky: scaling calibration model by {s:.3f} "
            f"({w_model}x{h_model} → {w_target}x{h_target})"
        )
        _last_scale_logged = s
    return replace(
        model,
        cx=model.cx * s, cy=model.cy * s,
        a1=model.a1 * s, a3=model.a3 * s, a5=model.a5 * s,
        image_width=w_target, image_height=h_target,
    )


def _compute_allowed_ids(
    config: dict,
    model: FisheyeModel,
    lat: float,
    lon: float,
    dt: datetime,
    gray: Optional['np.ndarray'] = None,
) -> Optional[set]:
    """
    Rank all visible objects (planets + Messier + NGC) by brightness and
    return the set of UIDs for the top_n brightest ones.

    Objects projected onto dark equipment areas (checked via gray pixel
    brightness) are excluded from ranking so they don't consume the budget.
    The pick is sticky across frames (see label_stability): an object on
    screen keeps its slot until a newcomer out-ranks it by a clear margin.

    UIDs are prefixed by type to avoid collisions:
        'planet:Jupiter', 'messier:M45', 'ngc:NGC 2244'

    Returns None if top_n is 0 or not set (show everything).
    """
    top_n = int(config.get('top_n', 0))
    if top_n <= 0:
        return None

    from .catalogs import get_messier_objects, get_ngc_objects, get_bright_stars
    from .planets import get_all_positions
    from .coords import radec_to_altaz as _ra2aa

    # Approximate typical visual magnitudes for planets (used for ranking only)
    _PLANET_MAG = {
        'Moon': -12.0, 'Venus': -4.0, 'Jupiter': -2.0, 'Mars': 0.5,
        'Mercury': 0.0, 'Saturn': 0.7, 'Uranus': 5.7, 'Neptune': 7.8,
    }

    def _visible(xy) -> bool:
        if xy is None:
            return False
        if gray is None:
            return True
        return _is_sky_visible(gray, int(xy[0]), int(xy[1]))

    candidates = []  # (mag, uid)

    # Bright stars (BSC5 named)
    stars_cfg = config.get('bright_stars', {})
    if stars_cfg.get('enabled', False):
        stars_max_mag = float(stars_cfg.get('max_magnitude', 2.5))
        use_bayer = bool(stars_cfg.get('bayer_fallback', False))
        for s in get_bright_stars(max_mag=stars_max_mag):
            if not star_display_name(s, use_bayer):
                continue
            alt, az = _ra2aa(
                s['ra_deg'], s['dec_deg'], lat, lon, dt, refraction=True
            )
            if float(alt) < 10.0:
                continue
            xy = model.altaz_to_pixel(float(alt), float(az))
            if not _visible(xy):
                continue
            candidates.append((float(s['vmag']), star_uid(s)))

    # Planets
    for name, (ra, dec) in get_all_positions(dt, lat, lon).items():
        if name == 'Sun':
            continue
        alt, az = _ra2aa(ra, dec, lat, lon, dt, refraction=True)
        if float(alt) < -1.0:
            continue
        xy = model.altaz_to_pixel(float(alt), float(az))
        if not _visible(xy):
            continue
        candidates.append((_PLANET_MAG.get(name, 0.0), f'planet:{name}'))

    # Messier
    for obj in get_messier_objects():
        alt, az = _ra2aa(
            obj['ra_deg'], obj['dec_deg'], lat, lon, dt, refraction=True
        )
        if float(alt) < 5.0:
            continue
        xy = model.altaz_to_pixel(float(alt), float(az))
        if not _visible(xy):
            continue
        label = obj.get('label', '')
        if label:
            mag = float(obj.get('vmag') or obj.get('mag') or 10.0)
            candidates.append((mag, f'messier:{label}'))

    # NGC (only when that layer is enabled)
    ngc_cfg = config.get('ngc', {})
    if ngc_cfg.get('enabled', False):
        max_mag = float(ngc_cfg.get('min_magnitude', 12.0))
        for obj in get_ngc_objects(max_mag=max_mag):
            if obj.get('messier'):
                continue
            alt, az = _ra2aa(
                obj['ra_deg'], obj['dec_deg'], lat, lon, dt, refraction=True
            )
            if float(alt) < 5.0:
                continue
            xy = model.altaz_to_pixel(float(alt), float(az))
            if not _visible(xy):
                continue
            oid = obj.get('id', obj.get('name', ''))
            if oid:
                mag = float(obj.get('vmag') or obj.get('mag') or 99.0)
                candidates.append((mag, f'ngc:{oid}'))

    candidates.sort(key=lambda c: c[0])  # brightest first
    return get_label_stabilizer().select([uid for _, uid in candidates], top_n)


_model_cache: dict = {'path': '', 'mtime': 0.0, 'model': None}


def _load_model(config: dict) -> Optional[FisheyeModel]:
    """Load fisheye model from calibration_file path, cached by path+mtime."""
    import os
    path = config.get('calibration_file', '')
    if not path:
        return None
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return None
    if path == _model_cache['path'] and mtime == _model_cache['mtime']:
        return _model_cache['model']
    model = FisheyeModel.try_load(path)
    if model is None or not model.is_valid():
        log.debug(f"allsky: calibration model not valid at {path!r}")
        _model_cache.update(path=path, mtime=mtime, model=None)
        return None
    _model_cache.update(path=path, mtime=mtime, model=model)
    return model


def _get_obs_utc(config: dict) -> Optional[datetime]:
    """Return the authoritative true-UTC observation time, or None.

    Set by the preview path (render_allsky_for_preview) to the same instant
    the calibration uses. When present it is the single source of truth for
    sky orientation and the legacy local-time/utc_offset path is skipped.
    """
    val = config.get('_obs_utc')
    if not val:
        return None
    try:
        dt = datetime.fromisoformat(val)
    except (ValueError, TypeError):
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def _get_datetime(metadata: dict) -> datetime:
    """
    Extract observation UTC datetime from frame metadata.
    Falls back to current UTC time if no timestamp available.
    """
    # Try common metadata keys from capture pipelines
    for key in ('DATETIME', 'capture_time', 'timestamp', 'date_obs'):
        val = metadata.get(key)
        if val is None:
            continue
        if isinstance(val, datetime):
            return val.replace(tzinfo=timezone.utc) if val.tzinfo is None else val
        if isinstance(val, str):
            for fmt in ('%Y-%m-%dT%H:%M:%S', '%Y-%m-%d %H:%M:%S',
                        '%Y%m%d_%H%M%S', '%d/%m/%Y %H:%M:%S'):
                try:
                    return datetime.strptime(val, fmt).replace(tzinfo=timezone.utc)
                except ValueError:
                    continue

    return datetime.now(timezone.utc)
