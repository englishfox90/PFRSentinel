"""
Main all-sky overlay entry point.

render_allsky_overlay(img, config, metadata) → PIL.Image

Orchestrates: grid → constellations → planets → bright stars → Messier → NGC
→ NINA target (whose name is placed before any other label).
All layers share a single LabelGrid for collision avoidance.
Fails silently if model not calibrated or any layer errors.
"""
from dataclasses import replace
from datetime import datetime, timezone
from typing import Optional

import numpy as np
from PIL import Image

from services.logger import app_logger as log
from services.nina_target_store import get_nina_target_store
from services.observing_window import SAME_CAPTURE_KEY
from services.output_crop import METADATA_KEY as CROP_METADATA_KEY, CropBox

from .discredit_policy import overlay_withheld
from .fisheye import FisheyeModel
from .label_collision import LabelGrid, reserve_targets
from .label_candidates import eligible_labels, rank_label_candidates
from .label_size import apply_label_size_preset
from .label_stability import forget_drawn_labels, get_label_stabilizer, upsample
from .obstruction_map import get_obstruction_map
from .render_grid import render_grid
from .render_constellations import render_constellations
from .moon_label import measure_moon_glare
from . import target_status_log
from .render_target import (
    layer_config, locate_target, render_target, reserve_target, stale_after_seconds,
)
from .render_objects import (
    planet_label_px, planet_targets, render_messier, render_ngc, render_planets,
    reserve_moon_glare,
)
from .render_stars import bright_star_targets, render_bright_stars, star_label_px
from .star_sightings import apply_sightings, label_targets, sighting_tolerance
from .sky_region import (  # sky_region pre-imports scipy for the worker thread
    FULL_MASK_MIN_DETECTIONS, detect_sky_evidence, visibility_plane,
)

# One-shot guard so the model-scaling INFO log fires once per scale factor
# rather than on every rendered frame (Phase 3.1).
_last_scale_logged: Optional[float] = None


def _detect_sky_mask(img: Image.Image) -> Optional[np.ndarray]:
    """A frame's detection mask when it rests on enough stars for a full
    vote, else None. Kept for existing callers; the renderer itself reads
    ``sky_region.visibility_plane``."""
    evidence = detect_sky_evidence(img)
    if evidence.n_detections < FULL_MASK_MIN_DETECTIONS:
        return None
    return upsample(evidence.mask, evidence.full_shape)


def render_allsky_overlay(
    img: Image.Image,
    config: dict,
    metadata: dict,
) -> Image.Image:
    """
    Add astronomical overlays to an all-sky camera image.

    Reads calibration model path from config['calibration_file'].
    Observer location from config keys '_lat', '_lon', '_elevation'
    (injected by the pipeline callers). '_frame_is_observable' (set by
    render_allsky_for_preview past the observing-window check) lets the
    equipment map learn from the frame; '_obstruction_map_path' is where it
    is saved. Direct callers leave both out and never touch the map.

    Args:
        img: PIL Image (RGB or RGBA) to annotate.
        config: allsky_overlay config dict.
        metadata: Frame metadata dict (may contain timestamp keys).

    Returns:
        Modified PIL Image (same mode as input).
    """
    if not config.get('enabled', False):
        return img

    config = apply_label_size_preset(config)
    original_mode = img.mode

    # --- Load fisheye model ---
    model = _load_model(config)
    if model is None:
        return img  # Silently skip — not calibrated
    calibration_a1 = float(model.a1)

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

    # Where labels may go: the voted detection mask while it is fresh, the
    # persisted equipment map once it is stale, the model's own sky disc
    # before either exists (sky_region). 255 = open sky, 0 = obstructed.
    # A reprocess of a capture already counted reads the vote without
    # advancing it; the map learns only from frames the observing gate passed.
    advance = not metadata.get(SAME_CAPTURE_KEY, False)
    evidence = detect_sky_evidence(img) if advance else None
    obstruction_map = get_obstruction_map()
    gray = visibility_plane(
        img, model, dt, lat, lon, stabilizer, obstruction_map,
        advance=advance,
        frame_is_observable=bool(config.get('_frame_is_observable', False)),
        crop=_crop_stamp(crop), save_path=config.get('_obstruction_map_path'),
        behind_equipment=bool(config.get('labels_behind_equipment', False)),
        evidence=evidence,
    )
    # A star detected at its own pixel is visible whatever the vote says
    # (star_sightings); the equipment map still has the last word.
    scale = float(model.a1) / calibration_a1 if calibration_a1 > 0 else 1.0
    gray = apply_sightings(
        gray, stabilizer.sightings, label_targets(model, config, lat, lon, dt),
        evidence.points if evidence is not None else None,
        sighting_tolerance(model.rms_residual, scale),
        map_sky=obstruction_map.small_sky_mask(w, h, _crop_stamp(crop)),
    )
    # Measured on the frame before any layer draws on it (moon_label).
    planet_config = config.get('planets', {})
    moon_glare = (measure_moon_glare(img, model, dt, lat, lon)
                  if planet_config.get('enabled', True) else None)

    # Placed before any other layer touches the grid, so the target's name
    # gets first pick and every later label works around it; only the Moon's
    # glare, where no name can be read, is kept clear before it.
    target_cfg = layer_config(config.get('nina_target'))
    target_placement = None
    try:
        reserve_moon_glare(grid_cfg, moon_glare)
        target = config.get('_nina_target')
        target_placement, unplaced = locate_target(img.size, model, target_cfg,
                                                   target, lat, lon, dt)
        if target:
            target_status_log.report_placement(
                str(target.get('name') or ''), target_placement, unplaced)
        if target_placement is not None:
            target_placement = reserve_target(
                grid_cfg, target_placement, bool(target_cfg.get('show_label', True)))
    except Exception as e:
        log.warning(f"allsky NINA target placement failed: {e}")

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

    # Rank every labellable object on the ORIGINAL plane (no overlays drawn
    # yet), pick the top-N budget, and let the drawn-label persistence decide
    # what is drawn: every layer then draws exactly `persisted`, each label
    # faded by its alpha (label_persistence).
    ranked = rank_label_candidates(config, model, lat, lon, dt, gray,
                                   moon_seen=moon_glare is not None)
    allowed_ids = _select_budget(ranked, config, advance)
    persisted = stabilizer.drawn_labels(eligible_labels(ranked, allowed_ids), advance,
                                        budget=allowed_ids)

    # Stars and planets are all reserved before any of their labels goes
    # down, and planets are named first: labelled last, a planet found the
    # slot right of it taken by a star's name and its own went to the far
    # side (discussion #105, Saturn).
    stars_config = config.get('bright_stars', {})
    star_targets, planet_targets_ = _reserve_point_objects(
        img.size, model, stars_config, planet_config, lat, lon, dt, gray,
        persisted, grid_cfg, moon_glare)

    try:
        img = render_planets(img, model, planet_config, lat, lon, dt, grid_cfg,
                             sky_gray=gray, targets=planet_targets_,
                             moon_glare=moon_glare, persisted=persisted)
    except Exception as e:
        log.warning(f"allsky planet render failed: {e}")

    try:
        img = render_bright_stars(img, model, stars_config, lat, lon, dt, grid_cfg,
                                  sky_gray=gray, targets=star_targets,
                                  persisted=persisted)
    except Exception as e:
        log.warning(f"allsky bright stars render failed: {e}")

    try:
        messier_config = config.get('messier', {})
        img = render_messier(img, model, messier_config, lat, lon, dt, grid_cfg,
                             sky_gray=gray, persisted=persisted)
    except Exception as e:
        log.warning(f"allsky messier render failed: {e}")

    try:
        ngc_config = config.get('ngc', {})
        img = render_ngc(img, model, ngc_config, lat, lon, dt, grid_cfg,
                         sky_gray=gray, persisted=persisted)
    except Exception as e:
        log.warning(f"allsky NGC render failed: {e}")

    try:
        img = render_target(img, target_cfg, target_placement)
    except Exception as e:
        log.warning(f"allsky NINA target render failed: {e}")

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
    # A model the service has discredited (discredit_policy) is treated as
    # no model: its labels are wrong, and drawing them for hours is what
    # 2026-09-28 looked like. The file itself is untouched.
    # A withheld frame also ends the drawn-label hold: a label must not be
    # carried across the gap and drawn on the next frame from before it.
    if overlay_withheld():
        forget_drawn_labels()
        return output_img
    try:
        from services.observing_window import is_observing_window
        if not is_observing_window(config, metadata, feature="All-sky overlay"):
            forget_drawn_labels()
            return output_img
        weather_cfg = config.get('weather', {})
        cfg = dict(allsky_cfg)
        cfg['_lat'] = float(weather_cfg.get('latitude', 0) or 0)
        cfg['_lon'] = float(weather_cfg.get('longitude', 0) or 0)
        cfg['_elevation'] = float(weather_cfg.get('elevation', 0) or 0)
        cfg['_obs_utc'] = datetime.now(timezone.utc).isoformat()
        cfg['_nina_target'] = _current_nina_target(allsky_cfg)
        # The render only happens past the observing-window check, so this
        # frame is one the equipment map may learn from. Package 2's
        # observable-sky gate will supply the verdict here.
        cfg['_frame_is_observable'] = True
        from services.app_config import get_obstruction_map_path
        cfg['_obstruction_map_path'] = get_obstruction_map_path()
        return render_allsky_overlay(output_img.copy(), cfg, metadata)
    except Exception as e:
        log.debug(f"All-sky preview render skipped: {e}")
        return output_img


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _current_nina_target(allsky_cfg: dict) -> Optional[dict]:
    """The target NINA last pushed, while fresh. Its own guard: a bad
    setting or a failing store loses the target, never the overlay."""
    try:
        stale_after_s = stale_after_seconds(allsky_cfg.get('nina_target'))
        store = get_nina_target_store()
        target = store.current(stale_after_s)
        if target is not None:
            return target.as_dict()
        held = store.snapshot()
        if held.get('target'):
            target_status_log.report_stale(str(held['target'].get('name') or ''),
                                           held.get('age_s'), stale_after_s)
        else:
            target_status_log.forget()
        return None
    except Exception as e:
        log.debug(f"allsky: NINA target unavailable: {e}")
        return None


def _reserve_point_objects(img_size, model, stars_config, planet_config,
                           lat, lon, dt, gray, persisted, grid, moon_glare=None):
    """Bright-star and planet targets of the ``persisted`` labels, each
    reserved on ``grid``, and the Moon's glare kept clear of every label. A
    layer that fails here is left to its own renderer, which recomputes."""
    stars = planets = None
    reserve_moon_glare(grid, moon_glare)
    try:
        stars = bright_star_targets(model, stars_config, lat, lon, dt, gray,
                                    persisted=persisted)
        reserve_targets(grid, stars, star_label_px(img_size, stars_config))
    except Exception as e:
        log.debug(f"allsky: bright-star reservation skipped: {e}")
    try:
        planets = planet_targets(model, planet_config, lat, lon, dt, gray,
                                 moon_glare=moon_glare, persisted=persisted)
        reserve_targets(grid, planets, planet_label_px(img_size, planet_config))
    except Exception as e:
        log.debug(f"allsky: planet reservation skipped: {e}")
    return stars, planets


def _crop_stamp(crop: Optional[CropBox]) -> Optional[tuple]:
    """The OUTPUT_CROP as the equipment map's stamp: a moved or resized crop
    puts the equipment at different pixels, so the map must start over."""
    if crop is None:
        return None
    return (crop.x, crop.y, crop.width, crop.height, crop.frame_width, crop.frame_height)


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


def _select_budget(ranked, config: dict, advance: bool = True) -> Optional[set]:
    """The top-N pick from ``ranked``, sticky across frames (label_stability):
    an object on screen keeps its slot until a newcomer out-ranks it by a
    clear margin. None when top_n is 0 or unset (show everything). A
    reprocess (``advance`` False) reads the pick without counting the frame."""
    top_n = int(config.get('top_n', 0))
    if top_n <= 0:
        return None
    return get_label_stabilizer().select(ranked, top_n, advance)


def _compute_allowed_ids(
    config: dict,
    model: FisheyeModel,
    lat: float,
    lon: float,
    dt: datetime,
    gray: Optional['np.ndarray'] = None,
    moon_seen: bool = False,
) -> Optional[set]:
    """The top-N budget for this frame (label_candidates ranks, the
    stabilizer picks). Returns None if top_n is 0 or not set."""
    if int(config.get('top_n', 0)) <= 0:
        return None
    ranked = rank_label_candidates(config, model, lat, lon, dt, gray, moon_seen)
    return _select_budget(ranked, config)


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
