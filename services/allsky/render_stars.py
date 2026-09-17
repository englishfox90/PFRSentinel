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
from typing import Optional, Set

from .fisheye import FisheyeModel
from .catalogs import get_bright_stars
from .coords import radec_to_altaz
from .label_collision import LabelGrid, default_gap, estimate_text_size
from .render_objects import _parse_color, _load_font, _is_sky_visible


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
) -> Image.Image:
    """Draw bright star name labels."""
    if not config.get('enabled', False):
        return img

    img_scale  = max(img.width, img.height) / 750.0
    color_str  = config.get('color', '#FFEEAA')
    opacity    = int(config.get('opacity', 220))
    label_size = int(round(config.get('label_size', 11) * img_scale))
    max_mag    = float(config.get('max_magnitude', 2.5))
    use_bayer  = bool(config.get('bayer_fallback', False))

    label_color = _parse_color(color_str, opacity)
    font = _load_font(label_size)
    gray = sky_gray if sky_gray is not None else np.array(img.convert('L'))

    overlay = Image.new('RGBA', img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    visible = []
    for star in get_bright_stars(max_mag=max_mag):
        display = star_display_name(star, use_bayer)
        if not display:
            continue
        if allowed_ids is not None and star_uid(star) not in allowed_ids:
            continue

        alt, az = radec_to_altaz(
            star['ra_deg'], star['dec_deg'], lat_deg, lon_deg, dt, refraction=True
        )
        alt, az = float(alt), float(az)
        if alt < 10.0:
            continue

        xy = model.altaz_to_pixel(alt, az)
        if xy is None:
            continue

        x, y = int(xy[0]), int(xy[1])
        if not _is_sky_visible(gray, x, y):
            continue
        visible.append((display, float(x), float(y), star_uid(star)))

    # Reserve every star before placing any label, so an early label cannot
    # land on a star whose own label comes later. The radius stays under the
    # placement gap, so a star never blocks its own label.
    marker_r = default_gap(label_size * 1.2) * 0.75
    for _, x, y, _ in visible:
        label_grid.reserve_marker(x, y, marker_r)

    for display, x, y, uid in visible:
        tw, th = estimate_text_size(display, label_size)
        pos = label_grid.try_place(x, y, tw, th, key=uid)
        if pos is not None:
            draw.text(pos, display, fill=label_color, font=font)

    return Image.alpha_composite(img, overlay)
