"""
DSO (Messier/NGC) and planet/Moon label renderer for all-sky overlays.

Objects are shown as text labels only — no circles or shapes are drawn.
Labels are skipped if the projected position falls in a dark area of the
image (equipment or mount blocking the sky at that point).
"""
import os
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from datetime import datetime
from typing import Tuple, List, Dict, Optional, Set

from .fisheye import FisheyeModel
from .catalogs import get_messier_objects, get_ngc_objects
from .planets import get_all_positions
from .coords import radec_to_altaz
from .label_collision import LabelGrid, estimate_text_size


def _parse_color(hex_str: str, opacity: int) -> Tuple[int, int, int, int]:
    h = hex_str.lstrip('#')
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return r, g, b, max(0, min(255, opacity))


def _load_font(size: int):
    """Load Space Grotesk if available, fall back to Arial / DejaVu / default."""
    user_fonts = os.path.join(
        os.environ.get('LOCALAPPDATA', ''), 'Microsoft', 'Windows', 'Fonts'
    )
    for name in ('SpaceGrotesk-Medium.ttf', 'SpaceGrotesk-Regular.ttf',
                 'SpaceGrotesk-VariableFont_wght.ttf'):
        path = os.path.join(user_fonts, name)
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                pass
    for fallback in ('arial.ttf', 'DejaVuSans.ttf'):
        try:
            return ImageFont.truetype(fallback, size)
        except Exception:
            pass
    return ImageFont.load_default()


def _is_sky_visible(
    gray: np.ndarray,
    x: int,
    y: int,
    radius: int = 15,
    threshold: float = 40.0,
) -> bool:
    """
    Return True if the image region around (x, y) looks like open sky.

    Equipment and mount hardware are darker than typical sky background.
    The threshold (default 40) is tuned to handle different FITS stretches
    where scattered light on telescope hardware can reach brightness 20-35.
    Open sky — even on a dark, moonless night — typically has a background
    gradient above 40 after standard percentile stretch.
    """
    h, w = gray.shape
    x0, x1 = max(0, x - radius), min(w, x + radius + 1)
    y0, y1 = max(0, y - radius), min(h, y + radius + 1)
    if x0 >= x1 or y0 >= y1:
        return False
    return float(np.median(gray[y0:y1, x0:x1])) > threshold


# ---------------------------------------------------------------------------
# Planets and Moon
# ---------------------------------------------------------------------------

def render_planets(
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
    """Draw planet and Moon name labels (no marker shapes)."""
    if not config.get('enabled', True):
        return img

    img_scale    = max(img.width, img.height) / 750.0
    label_size   = int(round(config.get('label_size', 14) * img_scale))
    opacity      = int(config.get('opacity', 255))
    single_color = config.get('color', '')
    colors       = config.get('colors', {})

    font = _load_font(label_size)
    gray = sky_gray if sky_gray is not None else np.array(img.convert('L'))

    overlay = Image.new('RGBA', img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    positions = get_all_positions(dt, lat_deg, lon_deg)

    for name, (ra, dec) in positions.items():
        if name == 'Sun':
            continue
        uid = f'planet:{name}'
        if allowed_ids is not None and uid not in allowed_ids:
            continue

        alt, az = radec_to_altaz(ra, dec, lat_deg, lon_deg, dt, refraction=True)
        alt, az = float(alt), float(az)
        if alt < 10.0:
            continue

        xy = model.altaz_to_pixel(alt, az)
        if xy is None:
            continue

        x, y = int(xy[0]), int(xy[1])
        if not _is_sky_visible(gray, x, y):
            continue

        hex_color  = single_color if single_color else colors.get(name, '#FFFFFF')
        text_color = _parse_color(hex_color, opacity)

        tw, th = estimate_text_size(name, label_size)
        pos = label_grid.try_place(float(x), float(y), tw, th, key=uid)
        if pos is not None:
            draw.text(pos, name, fill=text_color, font=font)

    return Image.alpha_composite(img, overlay)


# ---------------------------------------------------------------------------
# Messier objects
# ---------------------------------------------------------------------------

def render_messier(
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
    """Draw Messier object name labels (no marker shapes)."""
    if not config.get('enabled', True):
        return img

    img_scale  = max(img.width, img.height) / 750.0
    color_str  = config.get('color', '#FF8844')
    opacity    = int(config.get('opacity', 200))
    label_size = int(round(config.get('label_size', 12) * img_scale))

    label_color = _parse_color(color_str, opacity)
    font = _load_font(label_size)
    gray = sky_gray if sky_gray is not None else np.array(img.convert('L'))

    overlay = Image.new('RGBA', img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    for obj in get_messier_objects():
        label = obj.get('label', '')
        if not label:
            continue
        uid = f'messier:{label}'
        if allowed_ids is not None and uid not in allowed_ids:
            continue

        alt, az = radec_to_altaz(
            obj['ra_deg'], obj['dec_deg'], lat_deg, lon_deg, dt, refraction=True
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

        common = (obj.get('name') or '').strip()
        display = f"{common} ({label})" if common else label
        tw, th = estimate_text_size(display, label_size)
        pos = label_grid.try_place(float(x), float(y), tw, th, key=uid)
        if pos is not None:
            draw.text(pos, display, fill=label_color, font=font)

    return Image.alpha_composite(img, overlay)


# ---------------------------------------------------------------------------
# NGC objects
# ---------------------------------------------------------------------------

def render_ngc(
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
    """Draw NGC/IC object name labels (no marker shapes)."""
    if not config.get('enabled', False):
        return img

    img_scale  = max(img.width, img.height) / 750.0
    color_str  = config.get('color', '#88FF44')
    opacity    = int(config.get('opacity', 200))
    label_size = int(round(config.get('label_size', 11) * img_scale))
    max_mag    = float(config.get('min_magnitude', 12.0))

    label_color = _parse_color(color_str, opacity)
    font = _load_font(label_size)
    gray = sky_gray if sky_gray is not None else np.array(img.convert('L'))

    overlay = Image.new('RGBA', img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    for obj in get_ngc_objects(max_mag=max_mag):
        if obj.get('messier'):          # already shown by Messier layer
            continue

        oid = obj.get('id', obj.get('name', ''))
        if not oid:
            continue
        uid = f'ngc:{oid}'
        if allowed_ids is not None and uid not in allowed_ids:
            continue

        alt, az = radec_to_altaz(
            obj['ra_deg'], obj['dec_deg'], lat_deg, lon_deg, dt, refraction=True
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

        common = (obj.get('name') or '').strip()
        display = f"{common} ({oid})" if common else oid
        tw, th = estimate_text_size(display, label_size)
        pos = label_grid.try_place(float(x), float(y), tw, th, key=uid)
        if pos is not None:
            draw.text(pos, display, fill=label_color, font=font)

    return Image.alpha_composite(img, overlay)
