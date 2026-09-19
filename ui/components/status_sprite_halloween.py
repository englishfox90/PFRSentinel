"""
Halloween painters for the status sprite.

Each painter mirrors the meaning of the stock animation it replaces, so the
state is still readable at a glance: exposure is an eye opening, calibration
hunts across a web, the stretch is a row of candles, processing brews, sending
flies off. Pure QPainter, no assets.

Signature: ``painter(p, w, h, frame, elapsed)`` — ``frame`` ticks at 25 fps,
``elapsed`` is wall-clock seconds in the state (the stretch needs it: the GUI
timer starves while the stretch itself is running).
"""
import math

from PySide6.QtCore import Qt, QPointF, QRectF
from PySide6.QtGui import QBrush, QColor, QLinearGradient, QPainterPath, QPen, QTransform

from ..theme.tokens import Colors
from .status_sprite_astro import SENDING_PASS_SECONDS

PUMPKIN = "#FF7A1A"
PUMPKIN_STEM = "#5E8C3A"
SLIME = "#8CE04A"
WAX = "#E8DCC0"
FLAME = "#FFD166"
GHOST = "#EEEEEC"
SCLERA = "#F4EBDD"
INK = "#120F16"


def _color(value, alpha=1.0) -> QColor:
    c = QColor(value)
    c.setAlphaF(max(0.0, min(1.0, alpha)))
    return c


def _wing_path() -> QPainterPath:
    wing = QPainterPath()
    wing.moveTo(0.10, -0.10)
    wing.quadTo(0.50, -0.58, 1.00, -0.25)
    wing.quadTo(0.85, 0.00, 0.78, 0.22)
    wing.quadTo(0.62, -0.02, 0.48, 0.24)
    wing.quadTo(0.32, 0.02, 0.10, 0.26)
    wing.closeSubpath()
    return wing


def draw_bat(p, x, y, half_span, color, flap=1.0):
    """Bat silhouette centred on (x, y). ``flap`` 0.4–1.0 squashes the wings."""
    p.save()
    p.translate(x, y)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(color))
    wing = _wing_path()
    for mirror in (1.0, -1.0):
        p.drawPath(QTransform().scale(half_span * mirror, half_span * flap).map(wing))
    body_w, body_h = half_span * 0.30, half_span * 0.56
    p.drawEllipse(QRectF(-body_w / 2, -body_h / 2 + half_span * 0.04, body_w, body_h))
    for mirror in (1.0, -1.0):
        ear = QPainterPath()
        ear.moveTo(mirror * half_span * 0.02, -half_span * 0.20)
        ear.lineTo(mirror * half_span * 0.13, -half_span * 0.42)
        ear.lineTo(mirror * half_span * 0.15, -half_span * 0.16)
        ear.closeSubpath()
        p.drawPath(ear)
    p.restore()


def draw_ghost(p, x, y, size, alpha=1.0, sway=0.0):
    """Sheet ghost, ``size`` = height. ``sway`` shifts the hem for a drifting look."""
    half = size * 0.40
    top, hem = y - size / 2, y + size / 2
    path = QPainterPath()
    path.moveTo(x - half, hem)
    path.lineTo(x - half, y - size * 0.05)
    path.cubicTo(x - half, top - size * 0.12, x + half, top - size * 0.12, x + half, y - size * 0.05)
    path.lineTo(x + half, hem)
    step = half * 2 / 3
    for i in range(3):
        x_end = x + half - step * (i + 1)
        path.quadTo(x_end + step / 2 + sway, hem - size * 0.20, x_end, hem)
    path.closeSubpath()
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(_color(GHOST, alpha)))
    p.drawPath(path)
    p.setBrush(QBrush(_color(INK, alpha)))
    eye_w, eye_h = size * 0.13, size * 0.18
    for mirror in (-1.0, 1.0):
        p.drawEllipse(QRectF(x + mirror * size * 0.15 - eye_w / 2, y - size * 0.16, eye_w, eye_h))


def draw_pumpkin(p, x, y, r, alpha=1.0):
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(_color(PUMPKIN_STEM, alpha)))
    p.drawRect(QRectF(x - r * 0.18, y - r * 1.25, r * 0.36, r * 0.5))
    p.setBrush(QBrush(_color(PUMPKIN, alpha)))
    p.drawEllipse(QRectF(x - r * 1.15, y - r * 0.9, r * 2.3, r * 1.8))
    if r >= 3.5:
        p.setPen(QPen(_color("#B8500A", alpha * 0.8), 0.8))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(QRectF(x - r * 0.45, y - r * 0.9, r * 0.9, r * 1.8))


# ----------------------------------------------------------------------
# State painters
# ----------------------------------------------------------------------

def overlay_idle(p, w, h, frame, elapsed):
    """A bat crosses the stock crescent moon every ten seconds."""
    s = min(w, h)
    cycle, flight = 250, 60
    f = frame % cycle
    if f >= flight:
        return
    prog = f / flight
    bx = (w - s) / 2 - s * 0.2 + prog * s * 1.4
    by = h / 2 - s * 0.08 + math.sin(prog * math.pi * 3) * s * 0.12
    flap = 0.70 + 0.30 * math.sin(frame * 0.9)
    draw_bat(p, bx, by, s * 0.20, _color(Colors.accent_text), flap)


def waiting_dots(p, w, h, frame, dot_cy):
    """Three pumpkins swelling in sequence (replaces the pulsing star-dots)."""
    s = min(w, h)
    cx = w / 2.0
    t = frame * 0.055
    for i, x in enumerate((cx - s * 0.24, cx, cx + s * 0.24)):
        pulse = (math.sin(t - i * math.pi / 2.0) + 1) / 2
        draw_pumpkin(p, x, dot_cy, 2.8 + pulse * 1.6, 0.35 + pulse * 0.65)


def draw_capturing(p, w, h, frame, elapsed):
    """A cat's eye opens as the exposure runs; the slit pupil narrows with it."""
    s = min(w, h)
    cx, cy = w / 2.0, h / 2.0
    t = frame * 0.045
    openness = (math.sin(t * 0.5) + 1) / 2
    half_w = s * 0.42
    lid = s * (0.07 + 0.27 * openness)

    almond = QPainterPath()
    almond.moveTo(cx - half_w, cy)
    almond.quadTo(cx, cy - lid * 2, cx + half_w, cy)
    almond.quadTo(cx, cy + lid * 2, cx - half_w, cy)

    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(_color(SCLERA, 0.92)))
    p.drawPath(almond)

    p.save()
    p.setClipPath(almond)
    iris_r = s * 0.19
    ix = cx + math.sin(t * 0.35) * s * 0.07
    p.setBrush(QBrush(_color(Colors.accent_default)))
    p.drawEllipse(QRectF(ix - iris_r, cy - iris_r, iris_r * 2, iris_r * 2))
    slit_w = iris_r * (0.22 + 0.50 * (1.0 - openness))
    p.setBrush(QBrush(_color(INK)))
    p.drawEllipse(QRectF(ix - slit_w / 2, cy - iris_r * 0.92, slit_w, iris_r * 1.84))
    p.setBrush(QBrush(_color("#FFFFFF", 0.85)))
    p.drawEllipse(QRectF(ix - iris_r * 0.55, cy - iris_r * 0.55, iris_r * 0.34, iris_r * 0.34))
    p.restore()

    p.setPen(QPen(_color(Colors.accent_text), 1.5))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawPath(almond)


def draw_calibrating(p, w, h, frame, elapsed):
    """A spider hunts across its web while the rings pulse outward like the stock sonar."""
    s = min(w, h)
    cx, cy = w / 2.0, h / 2.0
    t = frame * 0.05
    spokes = 8
    reach = s * 0.44

    thread = QPen(_color(Colors.accent_text, 0.30), 1.0)
    p.setPen(thread)
    for i in range(spokes):
        a = i * 2 * math.pi / spokes + math.pi / 8
        p.drawLine(QPointF(cx, cy), QPointF(cx + reach * math.cos(a), cy + reach * math.sin(a)))

    p.setBrush(Qt.BrushStyle.NoBrush)
    for ring in range(3):
        radius = reach * (0.34 + ring * 0.30)
        glow = (math.sin(t * 1.4 - ring * 1.1) + 1) / 2
        p.setPen(QPen(_color(Colors.accent_default, 0.25 + 0.60 * glow), 1.2))
        web = QPainterPath()
        for i in range(spokes + 1):
            a = i * 2 * math.pi / spokes + math.pi / 8
            pt = QPointF(cx + radius * math.cos(a), cy + radius * math.sin(a))
            if i == 0:
                web.moveTo(pt)
            else:
                mid = a - math.pi / spokes
                sag = radius * 0.80
                web.quadTo(QPointF(cx + sag * math.cos(mid), cy + sag * math.sin(mid)), pt)
        p.drawPath(web)

    wander = s * 0.16
    sx = cx + math.sin(t * 2.3 + math.cos(t * 0.7)) * wander
    sy = cy + math.cos(t * 1.7 + math.sin(t * 0.4)) * wander
    leg_pen = QPen(_color(Colors.accent_text), 1.0)
    leg_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(leg_pen)
    scuttle = math.sin(frame * 0.8) * 0.18
    for mirror in (-1.0, 1.0):
        for k in range(4):
            a = (-0.9 + k * 0.6) + scuttle * (1 if k % 2 else -1)
            knee = QPointF(sx + mirror * s * 0.09 * math.cos(a), sy + s * 0.09 * math.sin(a) - s * 0.03)
            foot = QPointF(sx + mirror * s * 0.15 * math.cos(a), sy + s * 0.15 * math.sin(a) + s * 0.02)
            p.drawLine(QPointF(sx, sy), knee)
            p.drawLine(knee, foot)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(_color(Colors.accent_default)))
    p.drawEllipse(QRectF(sx - s * 0.065, sy - s * 0.05, s * 0.13, s * 0.13))
    p.drawEllipse(QRectF(sx - s * 0.04, sy - s * 0.10, s * 0.08, s * 0.08))


def draw_stretching(p, w, h, frame, elapsed):
    """The histogram as a row of candles: the stretch wave lifts them, flames flicker."""
    s = min(w, h)
    cx = w / 2.0
    num = 9
    bar_w = s * 0.07
    gap = s * 0.02
    x0 = cx - (num * bar_w + (num - 1) * gap) / 2
    room = h - 8 - s * 0.16

    p.setPen(Qt.PenStyle.NoPen)
    for i in range(num):
        norm = (i - (num - 1) / 2) / ((num - 1) / 2)
        wave = (math.sin(elapsed * 2.2 - i * (2 * math.pi / (num - 1))) + 1) / 2
        low = room * 0.12
        peak = math.exp(-0.5 * (norm * 1.5) ** 2) * room
        bar_h = max(3.0, low + wave * (peak - low))
        x = x0 + i * (bar_w + gap)
        top = h - bar_h - 4

        p.setBrush(QBrush(_color(WAX, 0.35 + wave * 0.55)))
        p.drawRoundedRect(QRectF(x, top, bar_w, bar_h), 1.0, 1.0)

        flicker = math.sin(elapsed * 11.0 + i * 1.7)
        flame_h = s * (0.085 + 0.02 * flicker)
        fx = x + bar_w / 2 + flicker * 0.4
        p.setBrush(QBrush(_color(PUMPKIN, 0.55 + wave * 0.40)))
        p.drawEllipse(QRectF(fx - bar_w * 0.62, top - flame_h - 0.5, bar_w * 1.24, flame_h))
        p.setBrush(QBrush(_color(FLAME, 0.70 + wave * 0.30)))
        p.drawEllipse(QRectF(fx - bar_w * 0.34, top - flame_h * 0.72 - 0.5, bar_w * 0.68, flame_h * 0.68))


def draw_processing(p, w, h, frame, elapsed):
    """A cauldron on the boil — bubbles rise and fade where the spinner dots orbited."""
    s = min(w, h)
    cx, cy = w / 2.0, h / 2.0
    rim_y = cy + s * 0.04
    half_w = s * 0.30
    depth = s * 0.34

    p.setPen(Qt.PenStyle.NoPen)
    for i, offset in enumerate((-0.55, 0.30, -0.10, 0.60, -0.35)):
        prog = (frame * 0.022 + i * 0.21) % 1.0
        bx = cx + offset * half_w + math.sin(prog * 7 + i) * s * 0.03
        by = rim_y - prog * s * 0.46
        r = s * (0.030 + 0.045 * (1.0 - abs(prog - 0.4)))
        p.setBrush(QBrush(_color(SLIME, (1.0 - prog) * 0.9)))
        p.drawEllipse(QRectF(bx - r, by - r, r * 2, r * 2))

    pot = QPainterPath()
    pot.moveTo(cx - half_w, rim_y)
    pot.cubicTo(cx - half_w * 1.25, rim_y + depth * 1.25, cx + half_w * 1.25, rim_y + depth * 1.25,
                cx + half_w, rim_y)
    pot.closeSubpath()
    p.setBrush(QBrush(_color(Colors.gray_7)))
    p.drawPath(pot)

    leg_pen = QPen(_color(Colors.gray_7), 2.0)
    leg_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(leg_pen)
    foot_y = rim_y + depth * 1.02
    for mirror in (-1.0, 1.0):
        p.drawLine(QPointF(cx + mirror * half_w * 0.55, foot_y - s * 0.05),
                   QPointF(cx + mirror * half_w * 0.75, foot_y))

    slosh = math.sin(frame * 0.12) * s * 0.008
    p.setPen(QPen(_color(Colors.gray_8), 1.5))
    p.setBrush(QBrush(_color(SLIME, 0.85)))
    p.drawEllipse(QRectF(cx - half_w, rim_y - s * 0.055 + slosh, half_w * 2, s * 0.11))


def draw_sending(p, w, h, frame, elapsed):
    """A ghost swoops left to right, trailing wisps where the shooting star had its tail."""
    s = min(w, h)
    cy = h / 2.0
    # Wall-clock, and on screen from the first paint — see the stock meteor.
    t = (elapsed / SENDING_PASS_SECONDS) % 1.0
    size = s * 0.42
    hx = size * 0.25 + t * (w + size)
    hy = cy + math.sin(t * math.pi * 2) * h * 0.10

    # Tapered wake back along the flight path, like the stock meteor's tail.
    back = t - 0.30
    tx = size * 0.25 + back * (w + size)
    ty = cy + math.sin(back * math.pi * 2) * h * 0.10 + size * 0.15
    wake = QPainterPath()
    wake.moveTo(hx, hy - size * 0.10)
    wake.quadTo((hx + tx) / 2, (hy + ty) / 2 - size * 0.10, tx, ty)
    wake.quadTo((hx + tx) / 2, (hy + ty) / 2 + size * 0.22, hx, hy + size * 0.42)
    wake.closeSubpath()
    fade = QLinearGradient(QPointF(hx, hy), QPointF(tx, ty))
    fade.setColorAt(0.0, _color(GHOST, 0.40))
    fade.setColorAt(1.0, _color(GHOST, 0.0))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(fade))
    p.drawPath(wake)

    draw_ghost(p, hx, hy, size, 0.95, sway=math.sin(frame * 0.5) * size * 0.06)


# Painters that replace the stock animation for a state.
PAINTERS = {
    'capturing': draw_capturing,
    'calibrating': draw_calibrating,
    'stretching': draw_stretching,
    'processing': draw_processing,
    'sending': draw_sending,
}

# Painters drawn on top of the stock animation.
OVERLAYS = {
    'idle': overlay_idle,
}
