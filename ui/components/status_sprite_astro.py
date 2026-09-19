"""
Standard (night-sky) painters for the status sprite.

One animation per pipeline state, each a small picture of what the app is
doing: an aperture breathing during the exposure, a star spiralling onto the
reticle during calibration, a histogram spreading during the stretch, a galaxy
turning while processing, a meteor leaving when the frame is sent.

Signature: ``painter(p, w, h, frame, elapsed)`` — ``frame`` ticks at 25 fps,
``elapsed`` is wall-clock seconds in the state (the stretch needs it: the GUI
timer starves while the stretch itself is running). Colours are read from the
tokens at paint time, so accent changes show up immediately.
"""
import math

from PySide6.QtCore import Qt, QPointF, QRectF
from PySide6.QtGui import QBrush, QColor, QLinearGradient, QPainterPath, QPen, QRadialGradient

from ..theme.tokens import Colors

MOON = "#FFD166"
MOON_LIT = "#FFE9A8"


def _color(value, alpha=1.0) -> QColor:
    c = QColor(value)
    c.setAlphaF(max(0.0, min(1.0, alpha)))
    return c


def _ease(x: float) -> float:
    x = max(0.0, min(1.0, x))
    return x * x * (3 - 2 * x)


def draw_glow(p, x, y, r, color, alpha):
    grad = QRadialGradient(QPointF(x, y), r)
    grad.setColorAt(0.0, _color(color, alpha))
    grad.setColorAt(1.0, _color(color, 0.0))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(grad))
    p.drawEllipse(QRectF(x - r, y - r, r * 2, r * 2))


def draw_sparkle(p, x, y, r, color, alpha=1.0):
    """Four-point star with concave sides — reads as a star even at 3 px."""
    pinch = r * 0.12
    path = QPainterPath()
    path.moveTo(x, y - r)
    path.quadTo(x + pinch, y - pinch, x + r, y)
    path.quadTo(x + pinch, y + pinch, x, y + r)
    path.quadTo(x - pinch, y + pinch, x - r, y)
    path.quadTo(x - pinch, y - pinch, x, y - r)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(_color(color, alpha)))
    p.drawPath(path)


def draw_idle(p, w, h, frame, elapsed):
    """Crescent moon in a soft halo, three stars twinkling out of phase."""
    s = min(w, h)
    cx, cy = w / 2.0, h / 2.0
    t = frame * 0.012
    breath = (math.sin(t) + 1) / 2

    draw_glow(p, cx - s * 0.04, cy, s * 0.50, MOON, 0.10 + 0.10 * breath)

    r = s * 0.30
    outer = QPainterPath()
    outer.addEllipse(QRectF(cx - r, cy - r, r * 2, r * 2))
    bite = QPainterPath()
    bite.addEllipse(QRectF(cx - r + r * 0.57, cy - r - r * 0.14, r * 2, r * 2))
    grad = QLinearGradient(QPointF(cx - r, cy - r), QPointF(cx, cy + r))
    grad.setColorAt(0.0, _color(MOON_LIT, 0.85 + 0.15 * breath))
    grad.setColorAt(1.0, _color(MOON, 0.75 + 0.20 * breath))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(grad))
    p.drawPath(outer.subtracted(bite))

    for sx, sy, size, phase in (
        (0.17, 0.22, 0.085, 0.0),
        (0.83, 0.18, 0.060, 1.6),
        (0.78, 0.76, 0.105, 2.9),
    ):
        tw = (math.sin(t * 2.2 + phase) + 1) / 2
        x = (w - s) / 2 + sx * s
        draw_sparkle(p, x, sy * s, s * size * (0.65 + 0.35 * tw), Colors.text_primary, 0.30 + 0.60 * tw)


def waiting_dots(p, w, h, frame, dot_cy):
    """Three stars brightening in turn, each lifting slightly as it peaks."""
    s = min(w, h)
    cx = w / 2.0
    t = frame * 0.055
    for i, x in enumerate((cx - s * 0.22, cx, cx + s * 0.22)):
        pulse = _ease((math.sin(t - i * math.pi / 2.0) + 1) / 2)
        y = dot_cy - pulse * s * 0.04
        draw_glow(p, x, y, s * 0.16, Colors.accent_default, 0.35 * pulse)
        draw_sparkle(p, x, y, s * (0.055 + 0.055 * pulse), Colors.accent_text, 0.30 + 0.70 * pulse)


def draw_capturing(p, w, h, frame, elapsed):
    """Six-blade aperture opening and closing over a lit lens."""
    s = min(w, h)
    cx, cy = w / 2.0, h / 2.0
    t = frame * 0.045
    openness = _ease((math.sin(t * 0.5) + 1) / 2)
    ring_r = s * 0.42
    hole_r = s * (0.08 + 0.22 * openness)
    rot = t * 0.22

    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(_color(Colors.border_focus, 0.85)))
    p.drawEllipse(QRectF(cx - ring_r, cy - ring_r, ring_r * 2, ring_r * 2))

    verts = [
        QPointF(cx + hole_r * math.cos(rot + i * math.pi / 3), cy + hole_r * math.sin(rot + i * math.pi / 3))
        for i in range(6)
    ]

    # Blade edges run along each side of the hexagon and on out to the ring.
    seam = QPen(_color(Colors.bg_surface, 0.70), 1.2)
    seam.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(seam)
    for i in range(6):
        a, b = verts[i], verts[(i + 1) % 6]
        dx, dy = b.x() - a.x(), b.y() - a.y()
        norm = math.hypot(dx, dy) or 1.0
        dx, dy = dx / norm, dy / norm
        px, py = b.x() - cx, b.y() - cy
        dot = px * dx + py * dy
        k = -dot + math.sqrt(max(0.0, dot * dot - (px * px + py * py - ring_r * ring_r)))
        p.drawLine(b, QPointF(b.x() + dx * k, b.y() + dy * k))

    hexagon = QPainterPath()
    hexagon.moveTo(verts[0])
    for v in verts[1:]:
        hexagon.lineTo(v)
    hexagon.closeSubpath()
    lens = QRadialGradient(QPointF(cx - hole_r * 0.25, cy - hole_r * 0.25), hole_r * 1.3)
    lens.setColorAt(0.0, _color(Colors.iris_12, 0.95))
    lens.setColorAt(0.45, _color(Colors.accent_text, 0.55 + 0.35 * openness))
    lens.setColorAt(1.0, _color(Colors.accent_default, 0.35 + 0.35 * openness))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(lens))
    p.drawPath(hexagon)

    p.setPen(QPen(_color(Colors.accent_text, 0.90), 1.5))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawEllipse(QRectF(cx - ring_r, cy - ring_r, ring_r * 2, ring_r * 2))


def draw_calibrating(p, w, h, frame, elapsed):
    """A star spirals in to the reticle and locks with a pulse, then the hunt restarts."""
    s = min(w, h)
    cx, cy = w / 2.0, h / 2.0
    cycle = 110
    prog = (frame % cycle) / cycle
    approach = _ease(min(1.0, prog / 0.78))
    locked = max(0.0, (prog - 0.78) / 0.22)

    ring_r = s * 0.28
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(QPen(_color(Colors.accent_text, 0.40 + 0.50 * (1.0 - locked) * (prog > 0.78)), 1.2))
    p.drawEllipse(QRectF(cx - ring_r, cy - ring_r, ring_r * 2, ring_r * 2))

    tick = QPen(_color(Colors.accent_text, 0.85), 1.4)
    tick.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(tick)
    inner, outer = s * 0.20, s * 0.43
    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        p.drawLine(QPointF(cx + dx * inner, cy + dy * inner), QPointF(cx + dx * outer, cy + dy * outer))

    if prog > 0.78:
        pulse_r = ring_r * (0.3 + 1.3 * locked)
        p.setPen(QPen(_color(Colors.accent_default, 0.80 * (1.0 - locked)), 1.6))
        p.drawEllipse(QRectF(cx - pulse_r, cy - pulse_r, pulse_r * 2, pulse_r * 2))

    orbit = s * 0.34 * (1.0 - approach)
    angle = approach * math.pi * 3.5 - math.pi / 2
    x, y = cx + orbit * math.cos(angle), cy + orbit * math.sin(angle)
    draw_glow(p, x, y, s * 0.20, Colors.accent_default, 0.30 + 0.40 * approach)
    draw_sparkle(p, x, y, s * (0.10 + 0.04 * approach), MOON, 1.0)


def draw_stretching(p, w, h, frame, elapsed):
    """A histogram pinned against the black point spreads into a full tonal range."""
    s = min(w, h)
    cx = w / 2.0
    spread = _ease((math.sin(elapsed * 1.7 - math.pi / 2) + 1) / 2)
    mean = 0.18 + 0.32 * spread
    sigma = 0.060 + 0.150 * spread
    amp = 1.0 - 0.22 * spread

    width = s * 0.86
    x0 = cx - width / 2
    base = h - 7.0
    height = h - 15.0

    steps = 36
    curve = QPainterPath()
    for k in range(steps + 1):
        u = k / steps
        y = base - height * amp * math.exp(-0.5 * ((u - mean) / sigma) ** 2)
        if k == 0:
            curve.moveTo(x0, y)
        else:
            curve.lineTo(x0 + u * width, y)

    area = QPainterPath(curve)
    area.lineTo(x0 + width, base)
    area.lineTo(x0, base)
    area.closeSubpath()
    fill = QLinearGradient(QPointF(0, base - height), QPointF(0, base))
    fill.setColorAt(0.0, _color(Colors.accent_default, 0.60))
    fill.setColorAt(1.0, _color(Colors.accent_default, 0.06))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(fill))
    p.drawPath(area)

    line = QPen(_color(Colors.accent_text), 1.6)
    line.setCapStyle(Qt.PenCapStyle.RoundCap)
    line.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(line)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawPath(curve)

    p.setPen(QPen(_color(Colors.text_muted, 0.80), 1.0))
    p.drawLine(QPointF(x0, base + 0.5), QPointF(x0 + width, base + 0.5))

    # Black / white point markers ride out with the curve.
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(_color(Colors.text_primary, 0.90)))
    for u in (max(0.0, mean - 2 * sigma), min(1.0, mean + 2 * sigma)):
        mx = x0 + u * width
        marker = QPainterPath()
        marker.moveTo(mx, base + 1.5)
        marker.lineTo(mx - 2.4, base + 5.5)
        marker.lineTo(mx + 2.4, base + 5.5)
        marker.closeSubpath()
        p.drawPath(marker)


def draw_processing(p, w, h, frame, elapsed):
    """A two-armed spiral galaxy turning about a bright core."""
    s = min(w, h)
    cx, cy = w / 2.0, h / 2.0
    turn = frame * 0.085

    draw_glow(p, cx, cy, s * 0.24, Colors.accent_default, 0.55)

    p.setPen(Qt.PenStyle.NoPen)
    points = 10
    for arm in range(2):
        for j in range(points):
            frac = j / (points - 1)
            radius = s * (0.07 + 0.35 * frac)
            angle = arm * math.pi + frac * 2.6 - turn
            r = s * (0.058 - 0.036 * frac)
            p.setBrush(QBrush(_color(Colors.accent_text, 1.0 - 0.82 * frac)))
            p.drawEllipse(QRectF(cx + radius * math.cos(angle) - r,
                                 cy + radius * 0.82 * math.sin(angle) - r, r * 2, r * 2))

    p.setBrush(QBrush(_color(Colors.iris_12, 0.95)))
    core = s * 0.075
    p.drawEllipse(QRectF(cx - core, cy - core, core * 2, core * 2))


SENDING_PASS_SECONDS = 0.85


def _meteor_pos(t, w, h):
    return w * 0.10 + t * w * 1.15, h * 0.28 + t * h * 0.40


def draw_sending(p, w, h, frame, elapsed):
    """A meteor crosses the field with a tapering, fading tail.

    Wall-clock and on screen from the first paint: the app holds this state for
    under a second, so a frame-counted pass that starts off-stage is never seen.
    """
    s = min(w, h)
    t = (elapsed / SENDING_PASS_SECONDS) % 1.0

    for sx, sy, phase in ((0.20, 0.72, 0.0), (0.55, 0.16, 2.0), (0.86, 0.60, 4.0)):
        tw = (math.sin(frame * 0.10 + phase) + 1) / 2
        draw_sparkle(p, sx * w, sy * h, s * 0.045, Colors.text_primary, 0.15 + 0.25 * tw)

    hx, hy = _meteor_pos(t, w, h)
    tx, ty = _meteor_pos(t - 0.42, w, h)
    dx, dy = hx - tx, hy - ty
    norm = math.hypot(dx, dy) or 1.0
    nx, ny = -dy / norm * s * 0.045, dx / norm * s * 0.045

    tail = QPainterPath()
    tail.moveTo(hx + nx, hy + ny)
    tail.lineTo(tx, ty)
    tail.lineTo(hx - nx, hy - ny)
    tail.closeSubpath()
    fade = QLinearGradient(QPointF(hx, hy), QPointF(tx, ty))
    fade.setColorAt(0.0, _color(Colors.accent_text, 0.90))
    fade.setColorAt(1.0, _color(Colors.accent_default, 0.0))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(fade))
    p.drawPath(tail)

    draw_glow(p, hx, hy, s * 0.20, MOON, 0.45)
    draw_sparkle(p, hx, hy, s * 0.115, MOON_LIT, 1.0)


PAINTERS = {
    'idle': draw_idle,
    'capturing': draw_capturing,
    'calibrating': draw_calibrating,
    'stretching': draw_stretching,
    'processing': draw_processing,
    'sending': draw_sending,
}
