"""
The target NINA is imaging, drawn on the all-sky overlay (issue #137).

The imaging camera's field of view as the outline of the sensor rectangle
projected through the fisheye model, with a small cross at its centre, and the
target's name. With no box to draw (no field of view reported, too small, or
reaching below the horizon) a reticle, a ring with four ticks, marks the
target instead. The target arrives from the NINA plugin (``POST /nina/target``,
``services.nina_target_store``) as J2000 RA/Dec; the renderer reads it from
``config['_nina_target']``, which ``render_allsky_for_preview`` injects (direct
callers pass it themselves, like ``_obs_utc``).

Unlike every catalogue layer, the target is never a top-N candidate and
ignores the visibility plane: the operator wants to see where the scope is
pointing even when that is behind the pier. Its name is placed on the label
grid before any other layer, so every later label works around it.

Rotation convention: ``rotation_deg`` is the sky position angle, in degrees
east of north, of the camera's "up" (the top edge / height axis). At 0 the
width runs east-west and the height north-south. Only the angle mod 180
changes the outline. The plugin sends NINA's ``PositionAngle`` unchanged; if
a live comparison shows the box mirrored, the one fix is the sign of theta
in ``fov_corners_radec``.

On a small preview the field of view is a few pixels across (an equidistant
fisheye at 750 px gives ~4 px per degree), so the outline is only drawn once
its bounding box reaches ``MIN_FOV_PX``; below that the reticle stands in.
"""
import math
from dataclasses import dataclass, replace
from datetime import datetime
from typing import List, Optional, Tuple

from PIL import Image, ImageDraw

from .coords import precess_from_j2000, radec_to_altaz
from .fisheye import FisheyeModel
from .label_collision import LabelGrid, default_gap, estimate_text_size
from .moon_label import text_halo
from .render_objects import _load_font, _parse_color

TARGET_UID = 'nina:target'
MIN_FOV_PX = 4.0
EDGE_SAMPLES = 6  # points per edge: the fisheye bends a long edge

_DEFAULT_COLOR = '#FF66AA'
_TICK_INNER = 1.4  # tick span, in ring radii
_TICK_OUTER = 2.2
_CROSS_OF_BOX = 0.35  # centre-cross half-length cap, in box shorter sides


@dataclass(frozen=True)
class TargetPlacement:
    name: str
    x: float
    y: float
    marker_r: float
    label_px: int
    fov_polygon: Optional[Tuple[Tuple[float, float], ...]]
    label_pos: Optional[Tuple[float, float]] = None


def target_label_px(img_size: Tuple[int, int], layer_cfg: dict) -> int:
    """Target label height in pixels for an image of ``img_size``."""
    return int(round(float(layer_cfg.get('label_size', 13)) * max(img_size) / 750.0))


def _marker_r(img_size: Tuple[int, int], layer_cfg: dict) -> float:
    return max(3.0, float(layer_cfg.get('marker_size', 10)) * max(img_size) / 750.0)


def fov_corners_radec(
    ra_deg: float, dec_deg: float,
    fov_w_deg: float, fov_h_deg: float, rotation_deg: float,
    samples_per_edge: int = EDGE_SAMPLES,
) -> List[Tuple[float, float]]:
    """RA/Dec of points round the sensor rectangle centred on (ra, dec):
    ``4 * samples_per_edge`` points, corners included once, in order.

    Gnomonic: a flat sensor behind a telescope sees the sky through the
    tangent plane, so a point (u, v) degrees off-centre along the width and
    height axes is ``p + tan(u)*right + tan(v)*up``, normalised."""
    a, d = math.radians(ra_deg), math.radians(dec_deg)
    th = math.radians(rotation_deg)
    p = (math.cos(d) * math.cos(a), math.cos(d) * math.sin(a), math.sin(d))
    e = (-math.sin(a), math.cos(a), 0.0)
    n = (-math.sin(d) * math.cos(a), -math.sin(d) * math.sin(a), math.cos(d))
    up = tuple(math.cos(th) * n[i] + math.sin(th) * e[i] for i in range(3))
    right = tuple(math.sin(th) * n[i] - math.cos(th) * e[i] for i in range(3))

    hw, hh = fov_w_deg / 2.0, fov_h_deg / 2.0
    corners = ((-hw, hh), (hw, hh), (hw, -hh), (-hw, -hh))
    k = max(1, int(samples_per_edge))
    out = []
    for i in range(4):
        (u0, v0), (u1, v1) = corners[i], corners[(i + 1) % 4]
        for j in range(k):
            t = j / k
            tu = math.tan(math.radians(u0 + (u1 - u0) * t))
            tv = math.tan(math.radians(v0 + (v1 - v0) * t))
            c = [p[m] + tu * right[m] + tv * up[m] for m in range(3)]
            norm = math.sqrt(sum(x * x for x in c))
            c = [x / norm for x in c]
            out.append((math.degrees(math.atan2(c[1], c[0])) % 360.0,
                        math.degrees(math.asin(max(-1.0, min(1.0, c[2]))))))
    return out


def _project(model: FisheyeModel, ra: float, dec: float, lat: float, lon: float,
             dt: datetime) -> Optional[Tuple[float, float]]:
    alt, az = radec_to_altaz(ra, dec, lat, lon, dt, refraction=True)
    if float(alt) < 0.0:
        return None
    xy = model.altaz_to_pixel(float(alt), float(az))
    if xy is None:
        return None
    return float(xy[0]), float(xy[1])


def _fov_polygon(model, layer_cfg, target, ra, dec, lat, lon, dt):
    if not layer_cfg.get('show_fov', True):
        return None
    fw, fh = target.get('fov_w_deg'), target.get('fov_h_deg')
    if not fw or not fh:
        return None
    pts = []
    for r, dd in fov_corners_radec(ra, dec, float(fw), float(fh),
                                   float(target.get('rotation_deg') or 0.0)):
        xy = _project(model, r, dd, lat, lon, dt)
        if xy is None:
            return None  # a part-drawn box reads as a different shape
        pts.append(xy)
    if max(_bbox_size(pts)) < MIN_FOV_PX:
        return None
    return tuple(pts)


def place_target(
    img_size: Tuple[int, int],
    model: FisheyeModel,
    layer_cfg: dict,
    target: Optional[dict],
    lat: float, lon: float, dt: datetime,
) -> Optional[TargetPlacement]:
    """Where the target and its field of view land on the frame, or None
    when there is no target, the layer is off, or the target is below the
    horizon or off the image."""
    if not target or not layer_cfg.get('enabled', True):
        return None
    ra, dec = precess_from_j2000(float(target['ra_deg']), float(target['dec_deg']), dt)
    ra, dec = float(ra), float(dec)
    xy = _project(model, ra, dec, lat, lon, dt)
    w, h = img_size
    if xy is None or not (0 <= xy[0] < w and 0 <= xy[1] < h):
        return None
    return TargetPlacement(
        name=str(target.get('name') or ''),
        x=xy[0], y=xy[1],
        marker_r=_marker_r(img_size, layer_cfg),
        label_px=target_label_px(img_size, layer_cfg),
        fov_polygon=_fov_polygon(model, layer_cfg, target, ra, dec, lat, lon, dt),
    )


def cross_half_length(placement: TargetPlacement) -> Optional[float]:
    """Half-length of the centre cross drawn inside the field-of-view box, or
    None with no box (the ring and ticks are drawn instead). At full
    resolution a typical field is smaller than the ring and ticks, which
    would swallow it."""
    if not placement.fov_polygon:
        return None
    w, h = _bbox_size(placement.fov_polygon)
    return min(placement.marker_r, _CROSS_OF_BOX * min(w, h))


def marker_reach(placement: TargetPlacement) -> float:
    """Radius round the target that holds everything drawn for it: the
    reticle, and the box's whole bounding rectangle when there is one, so a
    label kept outside it clears the box."""
    cross = cross_half_length(placement)
    if cross is None:
        return placement.marker_r * _TICK_OUTER
    xs = [px - placement.x for px, _ in placement.fov_polygon]
    ys = [py - placement.y for _, py in placement.fov_polygon]
    corner = math.hypot(max(abs(min(xs)), abs(max(xs))), max(abs(min(ys)), abs(max(ys))))
    return max(cross, corner)


def _bbox_size(points) -> Tuple[float, float]:
    xs, ys = [x for x, _ in points], [y for _, y in points]
    return max(xs) - min(xs), max(ys) - min(ys)


def reserve_target(grid: LabelGrid, placement: TargetPlacement,
                   show_label: bool = True) -> TargetPlacement:
    """Keep every later label off the reticle and the box, and give the
    target's name first pick of the slots round it."""
    reach = marker_reach(placement)
    grid.reserve_marker(placement.x, placement.y, reach)
    if not show_label or not placement.name:
        return placement
    tw, th = estimate_text_size(placement.name, placement.label_px)
    pos = grid.try_place(placement.x, placement.y, tw, th,
                         gap=max(default_gap(th), reach + 0.3 * th), key=TARGET_UID)
    return replace(placement, label_pos=pos)


def render_target(img: Image.Image, layer_cfg: dict,
                  placement: Optional[TargetPlacement]) -> Image.Image:
    """Draw the field-of-view box with a centre cross, or the ring and ticks
    when there is no box, and the name."""
    if placement is None or not layer_cfg.get('enabled', True):
        return img
    original_mode = img.mode
    if original_mode != 'RGBA':
        img = img.convert('RGBA')
    opacity = int(layer_cfg.get('opacity', 230))
    color = _parse_color(str(layer_cfg.get('color') or _DEFAULT_COLOR), opacity)
    width = max(1, int(layer_cfg.get('line_width', 2)))

    overlay = Image.new('RGBA', img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    x, y, r = placement.x, placement.y, placement.marker_r
    cross = cross_half_length(placement)
    if cross is None:
        draw.ellipse((x - r, y - r, x + r, y + r), outline=color, width=width)
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            draw.line((x + dx * r * _TICK_INNER, y + dy * r * _TICK_INNER,
                       x + dx * r * _TICK_OUTER, y + dy * r * _TICK_OUTER),
                      fill=color, width=width)
    else:
        pts = list(placement.fov_polygon)
        draw.line(pts + [pts[0]], fill=color, width=width, joint='curve')
        draw.line((x - cross, y, x + cross, y), fill=color, width=width)
        draw.line((x, y - cross, x, y + cross), fill=color, width=width)
    if placement.label_pos is not None and layer_cfg.get('show_label', True):
        draw.text(placement.label_pos, placement.name, fill=color,
                  font=_load_font(placement.label_px),
                  **text_halo(placement.label_px, opacity))

    img = Image.alpha_composite(img, overlay)
    return img if original_mode == 'RGBA' else img.convert(original_mode)
