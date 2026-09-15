"""
AltAz grid renderer for all-sky overlays.

Draws:
  - Horizon circle (alt = 0°)
  - Altitude rings at configurable step (e.g. 30°, 60°)
  - Azimuth radial lines (N/E/S/W + optional subdivisions)
  - Cardinal direction labels
"""
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from typing import Tuple, Optional

from .fisheye import FisheyeModel


def _parse_color(hex_str: str, opacity: int) -> Tuple[int, int, int, int]:
    """Convert '#RRGGBB' + opacity int to RGBA tuple."""
    h = hex_str.lstrip('#')
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return r, g, b, max(0, min(255, opacity))


def _load_font(size: int):
    """Load Space Grotesk if available, fall back to Arial / DejaVu / default."""
    import os
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


def render_grid(
    img: Image.Image,
    model: FisheyeModel,
    config: dict,
) -> Image.Image:
    """
    Draw AltAz grid overlay on img.

    Args:
        img: RGBA PIL Image to draw on.
        model: Calibrated FisheyeModel.
        config: Grid config dict (from allsky_overlay.grid).

    Returns:
        Modified PIL Image (RGBA).
    """
    if not config.get('enabled', True):
        return img

    color_str = config.get('color', '#336633')
    opacity = config.get('opacity', 120)
    line_width = max(1, int(config.get('line_width', 1)))
    label_size = int(config.get('label_size', 14))
    alt_step = int(config.get('altitude_step', 30))
    draw_rings = config.get('altitude_rings', True)
    draw_horizon = config.get('horizon', True)
    draw_az_lines = config.get('azimuth_lines', True)
    draw_cardinals = config.get('cardinal_labels', True)

    line_color = _parse_color(color_str, opacity)
    font = _load_font(label_size)

    # Create overlay layer
    overlay = Image.new('RGBA', img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    n_az_pts = 180   # Number of azimuth sample points per circle
    az_pts = np.linspace(0, 360, n_az_pts, endpoint=False)

    # --- Horizon circle (alt = 0°) ---
    if draw_horizon:
        _draw_alt_circle(draw, model, 0.0, az_pts, line_color, line_width)

    # --- Altitude rings ---
    if draw_rings:
        for alt in range(alt_step, 90, alt_step):
            _draw_alt_circle(draw, model, float(alt), az_pts, line_color, line_width)

    # --- Azimuth radial lines ---
    if draw_az_lines:
        for az_deg in range(0, 360, 45):
            _draw_az_line(draw, model, float(az_deg), line_color, line_width)

    # --- Cardinal labels ---
    if draw_cardinals:
        cardinals = [
            (0.0,   'N'),
            (90.0,  'E'),
            (180.0, 'S'),
            (270.0, 'W'),
        ]
        label_color = _parse_color(color_str, min(255, opacity + 60))
        for az, label in cardinals:
            xy = model.altaz_to_pixel(1.5, az)  # Just above horizon
            if xy is not None:
                x, y = xy
                # Centre the label
                try:
                    bbox = font.getbbox(label)
                    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
                except Exception:
                    tw, th = label_size, label_size
                draw.text((x - tw / 2, y - th / 2), label,
                          fill=label_color, font=font)

    return Image.alpha_composite(img, overlay)


def _draw_alt_circle(
    draw: ImageDraw.ImageDraw,
    model: FisheyeModel,
    alt_deg: float,
    az_pts: np.ndarray,
    color: tuple,
    width: int,
) -> None:
    """Draw a constant-altitude circle by projecting many azimuth points."""
    points = []
    for az in az_pts:
        xy = model.altaz_to_pixel(alt_deg, float(az))
        if xy is not None:
            points.append(xy)
        elif points:
            # Break detected — draw accumulated segment then start fresh
            _draw_polyline(draw, points, color, width)
            points = []
    if points:
        _draw_polyline(draw, points, color, width)


def _draw_az_line(
    draw: ImageDraw.ImageDraw,
    model: FisheyeModel,
    az_deg: float,
    color: tuple,
    width: int,
) -> None:
    """Draw a radial azimuth line from horizon to zenith."""
    alt_pts = np.linspace(0.0, 89.0, 45)
    points = []
    for alt in alt_pts:
        xy = model.altaz_to_pixel(float(alt), az_deg)
        if xy is not None:
            points.append(xy)
    if points:
        _draw_polyline(draw, points, color, width)


def _draw_polyline(
    draw: ImageDraw.ImageDraw,
    points: list,
    color: tuple,
    width: int,
) -> None:
    """Draw a polyline, skipping large gaps (> 100px) caused by edge wrap."""
    if len(points) < 2:
        return
    segment = [points[0]]
    for pt in points[1:]:
        prev = segment[-1]
        dist = np.hypot(pt[0] - prev[0], pt[1] - prev[1])
        if dist > 100:
            if len(segment) >= 2:
                draw.line([tuple(map(int, p)) for p in segment],
                          fill=color, width=width)
            segment = [pt]
        else:
            segment.append(pt)
    if len(segment) >= 2:
        draw.line([tuple(map(int, p)) for p in segment],
                  fill=color, width=width)
