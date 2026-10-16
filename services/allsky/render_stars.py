"""
Bright star name renderer for all-sky overlays.

Labels only — no marker dots (the star itself is the marker). Respects the
global top_n budget: bright stars compete for slots against Messier, NGC,
and planets, so raising top_n or lowering max_magnitude is how you get more
names on the image.
"""
import numpy as np
from PIL import Image, ImageDraw
from datetime import datetime
from typing import Dict, List, Optional, Set, Tuple

from .fisheye import FisheyeModel
from .catalogs import get_bright_stars
from .coords import radec_to_altaz
from .label_collision import LabelGrid, estimate_text_size, reserve_targets
from .render_objects import (
    LABEL_MIN_ALT_DEG, _is_sky_visible, _load_font, _parse_color, faded, label_wanted,
)


def star_uid(star: dict) -> str:
    """Stable identifier for a BSC5 star (HR number preferred)."""
    hr = star.get('hr', '')
    if hr:
        return f'star:HR{hr}'
    return f"star:{star.get('name') or star.get('bayer') or '?'}"


def star_display_name(star: dict, use_bayer_fallback: bool) -> str:
    """Proper name if present; else '<Bayer> <Const>' when fallback enabled."""
    name = (star.get('name') or '').strip()
    if name:
        return name
    if not use_bayer_fallback:
        return ''
    bayer = (star.get('bayer') or '').strip()
    const = (star.get('const') or '').strip()
    if bayer and const:
        return f"{bayer} {const}"
    return bayer


def star_label_px(img_size: Tuple[int, int], config: dict) -> int:
    """Bright-star label height in pixels for an image of ``img_size``."""
    return int(round(config.get('label_size', 11) * max(img_size) / 750.0))


def bright_star_targets(
    model: FisheyeModel,
    config: dict,
    lat_deg: float,
    lon_deg: float,
    dt: datetime,
    gray: np.ndarray,
    allowed_ids: Optional[Set[str]] = None,
    persisted: Optional[Dict[str, float]] = None,
) -> List[Tuple[str, float, float, str]]:
    """(display name, x, y, uid) of every bright star that gets a label.
    ``persisted`` as in render_objects: those UIDs, no visibility test."""
    if not config.get('enabled', False):
        return []
    max_mag   = float(config.get('max_magnitude', 2.5))
    use_bayer = bool(config.get('bayer_fallback', False))

    visible = []
    for star in get_bright_stars(max_mag=max_mag):
        display = star_display_name(star, use_bayer)
        if not display:
            continue
        if not label_wanted(star_uid(star), allowed_ids, persisted):
            continue

        alt, az = radec_to_altaz(
            star['ra_deg'], star['dec_deg'], lat_deg, lon_deg, dt, refraction=True
        )
        alt, az = float(alt), float(az)
        if alt < LABEL_MIN_ALT_DEG:
            continue

        xy = model.altaz_to_pixel(alt, az)
        if xy is None:
            continue

        x, y = int(xy[0]), int(xy[1])
        if persisted is None and not _is_sky_visible(gray, x, y):
            continue
        visible.append((display, float(x), float(y), star_uid(star)))
    return visible


def render_bright_stars(
    img: Image.Image,
    model: FisheyeModel,
    config: dict,
    lat_deg: float,
    lon_deg: float,
    dt: datetime,
    label_grid: LabelGrid,
    allowed_ids: Optional[Set[str]] = None,
    sky_gray: Optional[np.ndarray] = None,
    targets: Optional[List[Tuple[str, float, float, str]]] = None,
    persisted: Optional[Dict[str, float]] = None,
) -> Image.Image:
    """Draw bright star name labels. ``targets`` from ``bright_star_targets``
    lets the caller reserve the stars before other layers place anything;
    ``persisted`` (uid -> alpha) fades each label."""
    if not config.get('enabled', False):
        return img

    color_str  = config.get('color', '#FFEEAA')
    opacity    = int(config.get('opacity', 220))
    label_size = star_label_px(img.size, config)

    if targets is None:
        gray = sky_gray if sky_gray is not None else np.array(img.convert('L'))
        targets = bright_star_targets(model, config, lat_deg, lon_deg, dt, gray,
                                      allowed_ids, persisted)
    if not targets:
        return img
    reserve_targets(label_grid, targets, label_size)

    label_color = _parse_color(color_str, opacity)
    font = _load_font(label_size)
    overlay = Image.new('RGBA', img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    for display, x, y, uid in targets:
        tw, th = estimate_text_size(display, label_size)
        pos = label_grid.try_place(x, y, tw, th, key=uid)
        if pos is not None:
            alpha = persisted.get(uid, 1.0) if persisted is not None else 1.0
            draw.text(pos, display, fill=faded(label_color, alpha), font=font)

    return Image.alpha_composite(img, overlay)
