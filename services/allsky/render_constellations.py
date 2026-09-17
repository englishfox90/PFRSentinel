"""
Constellation line and label renderer for all-sky overlays.

Draws IAU/Dien constellation lines and optional abbreviated name labels
using the calibrated fisheye model.
"""
from collections import defaultdict
import numpy as np
from PIL import Image, ImageDraw
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from services.font_loader import load_font

from .fisheye import FisheyeModel
from .catalogs import get_western_constellation_lines, get_western_constellation_labels
from .coords import radec_to_altaz
from .label_collision import LabelGrid, estimate_text_size


def _is_sky_visible(gray: np.ndarray, x: int, y: int,
                    radius: int = 15, threshold: float = 40.0) -> bool:
    """Return True if the image region around (x, y) is open sky (not equipment).

    Uses median brightness — more robust than mean against a few bright outlier
    pixels pulling the average up when the region mixes equipment and sky.
    """
    h, w = gray.shape
    x0, x1 = max(0, x - radius), min(w, x + radius + 1)
    y0, y1 = max(0, y - radius), min(h, y + radius + 1)
    if x0 >= x1 or y0 >= y1:
        return False
    return float(np.median(gray[y0:y1, x0:x1])) > threshold


def _parse_color(hex_str: str, opacity: int) -> Tuple[int, int, int, int]:
    h = hex_str.lstrip('#')
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return r, g, b, max(0, min(255, opacity))


def _load_font(size: int):
    """Load Space Grotesk if available, fall back to Arial / DejaVu / default."""
    return load_font(size, 'display')


def _build_edge_fade_mask(
    img_w: int,
    img_h: int,
    model: FisheyeModel,
    fade_px: int,
) -> 'Image.Image':
    """
    Build a grayscale mask (L mode) that is 255 in the centre of the sky
    circle and fades linearly to 0 over the outer `fade_px` pixels.

    Uses the polynomial fisheye model to compute the true sky-circle radius.
    """
    theta = np.pi / 2.0
    t2 = theta * theta
    sky_r = (model.a1 * theta
             + model.a3 * t2 * theta
             + model.a5 * t2 * t2 * theta)
    sky_r = max(sky_r, 10.0)

    ys, xs = np.mgrid[0:img_h, 0:img_w].astype(np.float32)
    dist = np.hypot(xs - model.cx, ys - model.cy)
    alpha = np.clip((sky_r - dist) / max(fade_px, 1), 0.0, 1.0)
    return Image.fromarray((alpha * 255).astype(np.uint8), 'L')


def render_constellations(
    img: Image.Image,
    model: FisheyeModel,
    config: dict,
    lat_deg: float,
    lon_deg: float,
    dt: datetime,
    label_grid: LabelGrid = None,
    sky_gray: Optional[np.ndarray] = None,
) -> Image.Image:
    """
    Draw constellation lines and labels onto img.

    Args:
        img: RGBA PIL Image.
        model: Calibrated FisheyeModel.
        config: Constellation config dict (allsky_overlay.constellations).
        lat_deg: Observer latitude.
        lon_deg: Observer longitude.
        dt: UTC observation datetime.
        label_grid: Optional shared LabelGrid for collision avoidance.

    Returns modified RGBA PIL Image.
    """
    if not config.get('enabled', True):
        return img

    draw_lines  = config.get('lines', True)
    draw_labels = config.get('labels', True)
    color_str   = config.get('color', '#4488FF')
    opacity     = config.get('opacity', 180)
    img_scale   = max(img.width, img.height) / 750.0
    line_width  = max(1, int(config.get('line_width', 2)))
    label_size  = int(round(config.get('label_size', 12) * img_scale))
    edge_fade   = int(config.get('edge_fade_px', 250))

    line_color      = _parse_color(color_str, opacity)
    thin_color      = _parse_color(color_str, max(0, int(opacity * 0.45)))
    label_color     = _parse_color(color_str, min(255, opacity + 40))
    font = _load_font(label_size)

    w, h = img.size
    if label_grid is None:
        label_grid = LabelGrid(w, h)

    overlay = Image.new('RGBA', img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    gray = sky_gray if sky_gray is not None else np.array(img.convert('L'))

    # Two-pass rendering:
    # Pass 1 — collect drawable segments per constellation and flag any that
    #           have an endpoint over equipment.  A constellation is only shown
    #           if EVERY projected endpoint is in clear sky (100% rule).
    # Pass 2 — draw lines only for fully-clear constellations.
    con_drawable: Dict[str, list] = defaultdict(list)   # iau → [(p1, p2, thin), ...]
    con_blocked:  set = set()                            # iau with any equipment hit
    visible_constellations: set = set()
    visible_pixels: Dict[str, List[Tuple[float, float]]] = defaultdict(list)

    # --- Pass 1: classify segments ---
    if draw_lines:
        lines = get_western_constellation_lines()
        for ra1, dec1, ra2, dec2, iau, thin in lines:
            alt1, az1 = radec_to_altaz(ra1, dec1, lat_deg, lon_deg, dt, refraction=True)
            alt2, az2 = radec_to_altaz(ra2, dec2, lat_deg, lon_deg, dt, refraction=True)
            alt1, az1 = float(alt1), float(az1)
            alt2, az2 = float(alt2), float(az2)

            if alt1 < -10.0 and alt2 < -10.0:
                continue  # Both below horizon — irrelevant

            p1 = model.altaz_to_pixel(alt1, az1)
            p2 = model.altaz_to_pixel(alt2, az2)

            # If an endpoint projects onto the image, check it for equipment
            for pt in (p1, p2):
                if pt is not None and not _is_sky_visible(gray, int(pt[0]), int(pt[1])):
                    con_blocked.add(iau)

            if p1 is None or p2 is None:
                continue  # Horizon-crossing segment — skip but don't block

            seg_dist = np.hypot(p2[0] - p1[0], p2[1] - p1[1])
            if seg_dist > max(w, h) * 0.6:
                continue

            con_drawable[iau].append((p1, p2, thin))

    # --- Pass 2: draw only fully-clear constellations ---
    if draw_lines:
        for iau, segments in con_drawable.items():
            if iau in con_blocked:
                continue  # At least one endpoint over equipment — skip entirely
            for p1, p2, thin in segments:
                draw.line(
                    [(int(p1[0]), int(p1[1])), (int(p2[0]), int(p2[1]))],
                    fill=thin_color if thin else line_color,
                    width=line_width,
                )
            visible_constellations.add(iau)
            for p1, p2, thin in segments:
                visible_pixels[iau].append((p1[0], p1[1]))
                visible_pixels[iau].append((p2[0], p2[1]))

    # --- Edge fade: lines fade out over the last edge_fade_px of the sky circle ---
    if draw_lines and edge_fade > 0:
        fade_mask = _build_edge_fade_mask(w, h, model, edge_fade)
        r_ch, g_ch, b_ch, a_ch = overlay.split()
        a_arr  = np.array(a_ch, dtype=np.uint16)
        m_arr  = np.array(fade_mask, dtype=np.uint16)
        a_new  = ((a_arr * m_arr) // 255).astype(np.uint8)
        overlay = Image.merge('RGBA', (r_ch, g_ch, b_ch, Image.fromarray(a_new)))

    # --- Constellation labels ---
    if draw_labels:
        # Re-use the same draw context (labels are not faded — they only appear
        # well above the horizon via the alt < 5 check below)
        draw = ImageDraw.Draw(overlay)
        labels = get_western_constellation_labels()
        for lbl in labels:
            iau = lbl.get('abbrev', '')
            text = iau or lbl.get('name', '')
            if not text:
                continue

            if draw_lines:
                # Only label constellations with at least one visible segment.
                if iau not in visible_constellations:
                    continue
                # Place label at centroid of the *visible* endpoints — this
                # guarantees the label lands in clear sky, not over equipment.
                pts = visible_pixels[iau]
                cx = sum(p[0] for p in pts) / len(pts)
                cy = sum(p[1] for p in pts) / len(pts)
            else:
                # Lines disabled: fall back to catalog centroid with sky check.
                alt, az = radec_to_altaz(
                    lbl['ra_deg'], lbl['dec_deg'], lat_deg, lon_deg, dt,
                    refraction=True,
                )
                alt, az = float(alt), float(az)
                if alt < 10.0:
                    continue
                xy = model.altaz_to_pixel(alt, az)
                if xy is None:
                    continue
                cx, cy = float(xy[0]), float(xy[1])
                if not _is_sky_visible(gray, int(cx), int(cy)):
                    continue

            tw, th = estimate_text_size(text, label_size)
            pos = label_grid.try_place(cx, cy, tw, th, key=f'con:{text}')
            if pos is not None:
                draw.text(pos, text, fill=label_color, font=font)

    return Image.alpha_composite(img, overlay)
