"""
Christmas painters for the status sprite.

Each painter mirrors the meaning of the stock animation it replaces, so the
state is still readable at a glance: exposure is a present opening, calibration
brings the star down onto the tree, the stretch is a row of candy canes,
processing turns a snowflake, sending is the sleigh leaving. Pure QPainter, no
assets.

Signature: ``painter(p, w, h, frame, elapsed)`` — ``frame`` ticks at 25 fps,
``elapsed`` is wall-clock seconds in the state (the stretch needs it: the GUI
timer starves while the stretch itself is running).
"""
import math

from PySide6.QtCore import Qt, QPointF, QRectF
from PySide6.QtGui import QBrush, QColor, QPainterPath, QPen

from ..theme.tokens import Colors
from .status_sprite_astro import SENDING_PASS_SECONDS, draw_glow, draw_sparkle

BERRY = "#D8313F"
BERRY_DEEP = "#A8202D"
PINE = "#2F8F4E"
PINE_DEEP = "#1F6B38"
GOLD = "#F2C55C"
SNOW = "#F4F7FA"
BARK = "#8A5A33"
FUR = "#B07A45"


def _color(value, alpha=1.0) -> QColor:
    c = QColor(value)
    c.setAlphaF(max(0.0, min(1.0, alpha)))
    return c


def _ease(x: float) -> float:
    x = max(0.0, min(1.0, x))
    return x * x * (3 - 2 * x)


def draw_tree(p, cx, top, s):
    """Three-tier fir with a trunk; ``top`` is the tip."""
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(_color(BARK)))
    p.drawRect(QRectF(cx - s * 0.035, top + s * 0.48, s * 0.07, s * 0.07))
    for i in range(3):
        apex = top + i * s * 0.13
        half = s * (0.14 + 0.07 * i)
        tier = QPainterPath()
        tier.moveTo(cx, apex)
        tier.lineTo(cx + half, apex + s * 0.23)
        tier.lineTo(cx - half, apex + s * 0.23)
        tier.closeSubpath()
        p.setBrush(QBrush(_color(PINE if i % 2 == 0 else PINE_DEEP)))
        p.drawPath(tier)


def draw_sleigh(p, x, y, u, frame):
    """Sleigh and one red-nosed reindeer, ``u`` = sleigh height; (x, y) is the sleigh."""
    p.save()
    p.translate(x, y)

    runner = QPen(_color(GOLD), max(1.0, u * 0.07))
    runner.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(runner)
    p.setBrush(Qt.BrushStyle.NoBrush)
    blade = QPainterPath()
    blade.moveTo(-0.46 * u, 0.32 * u)
    blade.lineTo(0.30 * u, 0.32 * u)
    blade.quadTo(0.52 * u, 0.32 * u, 0.50 * u, 0.12 * u)
    p.drawPath(blade)
    for sx in (-0.26, 0.16):
        p.drawLine(QPointF(sx * u, 0.32 * u), QPointF(sx * u, 0.18 * u))

    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(_color(PINE)))
    p.drawEllipse(QRectF(-0.34 * u, -0.34 * u, 0.34 * u, 0.36 * u))
    body = QPainterPath()
    body.moveTo(-0.50 * u, -0.24 * u)
    body.lineTo(-0.38 * u, -0.24 * u)
    body.lineTo(-0.30 * u, -0.02 * u)
    body.lineTo(0.30 * u, -0.02 * u)
    body.lineTo(0.42 * u, -0.16 * u)
    body.lineTo(0.48 * u, -0.10 * u)
    body.lineTo(0.38 * u, 0.20 * u)
    body.lineTo(-0.42 * u, 0.20 * u)
    body.closeSubpath()
    p.setBrush(QBrush(_color(BERRY)))
    p.drawPath(body)

    rx = 1.02 * u
    p.setPen(QPen(_color(GOLD, 0.8), max(0.6, u * 0.03)))
    p.drawLine(QPointF(0.46 * u, -0.10 * u), QPointF(rx - 0.18 * u, 0.0))

    legs = QPen(_color(FUR), max(1.0, u * 0.06))
    legs.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(legs)
    gallop = math.sin(frame * 0.7) * 0.10 * u
    for lx, swing in ((-0.14, gallop), (-0.08, -gallop), (0.10, -gallop), (0.16, gallop)):
        p.drawLine(QPointF(rx + lx * u, 0.08 * u), QPointF(rx + lx * u + swing, 0.30 * u))
    p.drawLine(QPointF(rx + 0.26 * u, -0.22 * u), QPointF(rx + 0.22 * u, -0.34 * u))
    p.drawLine(QPointF(rx + 0.26 * u, -0.22 * u), QPointF(rx + 0.32 * u, -0.34 * u))

    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(_color(FUR)))
    p.drawEllipse(QRectF(rx - 0.21 * u, -0.08 * u, 0.42 * u, 0.20 * u))
    p.drawEllipse(QRectF(rx + 0.16 * u, -0.23 * u, 0.20 * u, 0.16 * u))
    draw_glow(p, rx + 0.36 * u, -0.14 * u, 0.16 * u, BERRY, 0.7)
    p.setBrush(QBrush(_color(BERRY)))
    p.drawEllipse(QRectF(rx + 0.31 * u, -0.19 * u, 0.10 * u, 0.10 * u))
    p.restore()


# ----------------------------------------------------------------------
# State painters
# ----------------------------------------------------------------------

# (x fraction, fall period in frames, phase, radius px). The frame counter
# wraps at 3600, so every period divides it and no flake ever jumps.
_FLAKES = (
    (0.12, 200, 0.10, 1.1), (0.30, 300, 0.55, 0.9), (0.47, 240, 0.80, 1.3),
    (0.63, 360, 0.30, 1.0), (0.80, 200, 0.65, 1.2), (0.93, 300, 0.00, 0.9),
)


def overlay_idle(p, w, h, frame, elapsed):
    """Snow drifts down past the stock crescent moon."""
    p.setPen(Qt.PenStyle.NoPen)
    for fx, period, phase, r in _FLAKES:
        fall = ((frame % period) / period + phase) % 1.0
        x = fx * w + math.sin(fall * math.pi * 4 + phase * 9) * 2.0
        y = -2.0 + fall * (h + 4.0)
        p.setBrush(QBrush(_color(SNOW, 0.35 + 0.45 * math.sin(fall * math.pi))))
        p.drawEllipse(QRectF(x - r, y - r, r * 2, r * 2))


def waiting_dots(p, w, h, frame, dot_cy):
    """Three fairy lights on a wire, lighting in turn (replaces the pulsing star-dots)."""
    s = min(w, h)
    cx = w / 2.0
    t = frame * 0.055
    wire_y = dot_cy - s * 0.09

    p.setPen(QPen(_color(Colors.gray_8), 1.0))
    p.setBrush(Qt.BrushStyle.NoBrush)
    wire = QPainterPath()
    wire.moveTo(cx - s * 0.40, wire_y - s * 0.03)
    wire.quadTo(cx, wire_y + s * 0.04, cx + s * 0.40, wire_y - s * 0.03)
    p.drawPath(wire)

    for i, (x, colour) in enumerate(((cx - s * 0.24, BERRY), (cx, GOLD), (cx + s * 0.24, PINE))):
        pulse = _ease((math.sin(t - i * math.pi / 2.0) + 1) / 2)
        draw_glow(p, x, dot_cy, s * 0.17, colour, 0.50 * pulse)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(_color(Colors.gray_8)))
        p.drawRect(QRectF(x - s * 0.025, dot_cy - s * 0.095, s * 0.05, s * 0.04))
        p.setBrush(QBrush(_color(colour, 0.40 + 0.60 * pulse)))
        p.drawEllipse(QRectF(x - s * 0.05, dot_cy - s * 0.06, s * 0.10, s * 0.13))


def draw_capturing(p, w, h, frame, elapsed):
    """A present opens as the exposure runs: the lid lifts and the light comes out."""
    s = min(w, h)
    cx, cy = w / 2.0, h / 2.0
    t = frame * 0.045
    openness = _ease((math.sin(t * 0.5) + 1) / 2)

    box_w, box_h = s * 0.54, s * 0.32
    box_top = cy + s * 0.08
    lid_w, lid_h = s * 0.62, s * 0.11
    lift = openness * s * 0.24

    draw_glow(p, cx, box_top, s * (0.16 + 0.34 * openness), GOLD, 0.15 + 0.65 * openness)
    for i, dx in enumerate((-0.14, 0.02, 0.15)):
        rise = (t * 0.6 + i * 0.37) % 1.0
        draw_sparkle(p, cx + dx * s, box_top - rise * lift * 1.1, s * 0.05,
                     SNOW, openness * math.sin(rise * math.pi))

    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(_color(BERRY)))
    p.drawRoundedRect(QRectF(cx - box_w / 2, box_top, box_w, box_h), 1.5, 1.5)
    p.setBrush(QBrush(_color(GOLD)))
    p.drawRect(QRectF(cx - s * 0.045, box_top, s * 0.09, box_h))

    p.save()
    p.translate(cx, box_top - lift)
    p.rotate(-14.0 * openness)
    p.setBrush(QBrush(_color(BERRY_DEEP)))
    p.drawRoundedRect(QRectF(-lid_w / 2, -lid_h, lid_w, lid_h), 1.5, 1.5)
    p.setBrush(QBrush(_color(GOLD)))
    p.drawRect(QRectF(-s * 0.045, -lid_h, s * 0.09, lid_h))
    for mirror in (-1.0, 1.0):
        p.drawEllipse(QRectF(mirror * s * 0.065 - s * 0.065, -lid_h - s * 0.10, s * 0.13, s * 0.10))
    p.restore()


def draw_calibrating(p, w, h, frame, elapsed):
    """The star spirals down onto the tree and locks with a pulse; the baubles light."""
    s = min(w, h)
    cx, cy = w / 2.0, h / 2.0
    cycle = 110
    prog = (frame % cycle) / cycle
    approach = _ease(min(1.0, prog / 0.78))
    locked = max(0.0, (prog - 0.78) / 0.22)
    lit = prog > 0.78

    top = cy - s * 0.12
    draw_tree(p, cx, top, s)

    p.setPen(Qt.PenStyle.NoPen)
    for i, (bx, by) in enumerate(((-0.06, 0.16), (0.08, 0.27), (-0.12, 0.33), (0.15, 0.42), (-0.02, 0.44))):
        colour = BERRY if i % 2 == 0 else GOLD
        x, y = cx + bx * s, top + by * s
        if lit:
            draw_glow(p, x, y, s * 0.09, colour, 0.6 * (1.0 - locked * 0.5))
        p.setBrush(QBrush(_color(colour, 1.0 if lit else 0.35)))
        p.drawEllipse(QRectF(x - s * 0.03, y - s * 0.03, s * 0.06, s * 0.06))

    tx, ty = cx, top - s * 0.06
    if lit:
        pulse_r = s * (0.08 + 0.34 * locked)
        p.setPen(QPen(_color(GOLD, 0.85 * (1.0 - locked)), 1.6))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(QRectF(tx - pulse_r, ty - pulse_r, pulse_r * 2, pulse_r * 2))

    orbit = s * 0.26 * (1.0 - approach)
    angle = approach * math.pi * 3.5 - math.pi / 2
    x, y = tx + orbit * math.cos(angle), ty + orbit * math.sin(angle)
    draw_glow(p, x, y, s * 0.20, GOLD, 0.30 + 0.40 * approach)
    draw_sparkle(p, x, y, s * (0.10 + 0.04 * approach), GOLD, 1.0)


def draw_stretching(p, w, h, frame, elapsed):
    """The histogram as a row of candy canes: the stretch wave lifts them, stripes climb."""
    s = min(w, h)
    cx = w / 2.0
    num = 9
    bar_w = s * 0.07
    gap = s * 0.02
    x0 = cx - (num * bar_w + (num - 1) * gap) / 2
    room = h - 10
    band = max(2.0, s * 0.07)

    p.setPen(Qt.PenStyle.NoPen)
    for i in range(num):
        norm = (i - (num - 1) / 2) / ((num - 1) / 2)
        wave = (math.sin(elapsed * 2.2 - i * (2 * math.pi / (num - 1))) + 1) / 2
        low = room * 0.12
        peak = math.exp(-0.5 * (norm * 1.5) ** 2) * room
        bar_h = max(3.0, low + wave * (peak - low))
        x = x0 + i * (bar_w + gap)
        top = h - bar_h - 4
        alpha = 0.45 + wave * 0.55

        stick = QPainterPath()
        stick.addRoundedRect(QRectF(x, top, bar_w, bar_h), bar_w / 2, bar_w / 2)
        p.setBrush(QBrush(_color(SNOW, alpha)))
        p.drawPath(stick)

        p.save()
        p.setClipPath(stick)
        p.setBrush(QBrush(_color(BERRY, alpha)))
        y = h - 4 - ((elapsed * 9.0 + i * 1.3) % (band * 2))
        while y > top - band:
            p.drawRect(QRectF(x, y, bar_w, band))
            y -= band * 2
        p.restore()


def draw_processing(p, w, h, frame, elapsed):
    """A six-armed snowflake turning where the galaxy turned."""
    s = min(w, h)
    cx, cy = w / 2.0, h / 2.0
    turn = frame * 0.03
    reach = s * (0.36 + 0.02 * math.sin(frame * 0.10))

    draw_glow(p, cx, cy, s * 0.30, Colors.accent_default, 0.40)

    arm = QPen(_color(SNOW), 1.5)
    arm.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(arm)
    for k in range(6):
        a = turn + k * math.pi / 3
        dx, dy = math.cos(a), math.sin(a)
        p.drawLine(QPointF(cx, cy), QPointF(cx + dx * reach, cy + dy * reach))
        for along, length in ((0.50, 0.30), (0.78, 0.20)):
            bx, by = cx + dx * reach * along, cy + dy * reach * along
            for side in (-1.0, 1.0):
                b = a + side * math.pi / 3.2
                p.drawLine(QPointF(bx, by),
                           QPointF(bx + math.cos(b) * reach * length, by + math.sin(b) * reach * length))

    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(_color(Colors.accent_text)))
    core = s * 0.05
    p.drawEllipse(QRectF(cx - core, cy - core, core * 2, core * 2))


def draw_sending(p, w, h, frame, elapsed):
    """The sleigh leaves left to right, trailing sparkles where the meteor had its tail."""
    s = min(w, h)
    cy = h / 2.0
    # Wall-clock, and on screen from the first paint — see the stock meteor.
    t = (elapsed / SENDING_PASS_SECONDS) % 1.0
    u = s * 0.40

    def pos(at):
        return u * 0.6 + at * (w + u), cy + math.sin(at * math.pi * 2) * h * 0.10

    for k in range(4):
        tx, ty = pos(t - 0.07 - k * 0.07)
        twinkle = (math.sin(frame * 0.9 + k * 1.9) + 1) / 2
        draw_sparkle(p, tx - u * 0.5, ty + u * 0.25, s * (0.07 - k * 0.012),
                     GOLD, (0.85 - k * 0.2) * (0.5 + 0.5 * twinkle))

    hx, hy = pos(t)
    draw_sleigh(p, hx, hy, u, frame)


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
