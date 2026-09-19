"""
Status Sprite Widget
Procedural QPainter animations for each processing state — no external assets.
Text is omitted; hover the widget to see the state label as a tooltip.

This module owns the state, the timer and the waiting speech bubble. The
pictures themselves live in painter sets: `status_sprite_astro` is the standard
night-sky set, and a special theme may name another (`'sprite'` in its pack)
that replaces or overlays individual states.
"""
import random
import time

from PySide6.QtWidgets import QWidget, QSizePolicy
from PySide6.QtCore import Qt, QTimer, QRectF, QSize
from PySide6.QtGui import QPainter, QColor, QPen, QBrush, QPainterPath, QFont

from ..theme.tokens import Colors
from ..theme.special_themes import active_special_theme
from . import status_sprite_astro, status_sprite_halloween

_THEMED_SETS = {
    'halloween': status_sprite_halloween,
}


class StatusSpriteWidget(QWidget):
    """
    Animated status sprite — 44 × 44 px square, pure animation, no text.
    The state name is surfaced as a QToolTip on hover.

    States: idle, waiting, capturing, stretching, processing, sending
    Call set_state(state_str | None) to switch / stop.
    """

    # Fun astrophotography words shown periodically in the waiting speech bubble
    WAITING_WORDS = [
        "Photons!", "Dark skies...", "Seeing: poor", "Collimated!", "No clouds!",
        "PHD2 locked", "Focus!", "Drift aligned", "Sky clear!", "Plate solved",
        "Flip time!", "Dew heater on", "Cold night!", "Tracking...", "Bias frames",
        "Flat frames", "Dark frames", "SNR rising", "Stars sharp!", "Galaxy time!",
        "Nebula mode", "Slewing...", "Polar aligned", "Cosmic rays!", "Orion rising",
        "Milky Way!", "Andromeda!", "Horsehead!", "Crab Nebula", "Ring Nebula",
        "Pleiades up!", "Globular!", "Double star!", "Red dwarf!", "Black holes!",
        "Quasar!", "Exoplanet?", "Perihelion!", "Zenith!", "RA drift: 0",
        "Dec: stable", "Seeing: 2\"", "Lucky imaging", "Airy disk!", "Sub-arcsec!",
        "Clear skies!", "Nebula time!", "Scope cooling", "No dew yet!", "On target!",
    ]

    STATE_TOOLTIPS = {
        'idle':         'Idle — session not running',
        'waiting':      'Waiting for new image',
        'capturing':    'Capturing exposure',
        'calibrating':  'Calibrating camera',
        'stretching':   'Applying histogram stretch',
        'processing':   'Processing image',
        'sending':      'Sending to outputs',
    }

    MIN_SIZE = 44   # minimum widget side length (px)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._state = None
        self._frame = 0

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.setInterval(40)   # 25 fps

        self._waiting_cycle = -1      # tracks which 750-frame cycle we're in
        self._waiting_word = ""       # current word shown in speech bubble
        self._waiting_word_pool = []  # shuffled queue — drains before reshuffling
        self._waiting_pool_theme = None  # pack the queue was built for
        self._state_start = 0.0       # wall-clock time when current state began

        self.setMinimumSize(self.MIN_SIZE, self.MIN_SIZE)
        sp = self.sizePolicy()
        sp.setHorizontalPolicy(QSizePolicy.Policy.Expanding)
        sp.setVerticalPolicy(QSizePolicy.Policy.Preferred)
        self.setSizePolicy(sp)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, False)

    def sizeHint(self):
        return QSize(self.MIN_SIZE, self.MIN_SIZE)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_state(self, state):
        """Set animation state.  Pass None to stop."""
        new_state = state.lower() if state else None
        if new_state != self._state:
            # Only reset frame counter on genuine state transitions
            self._frame = 0
            self._state_start = time.monotonic()
            self._waiting_cycle = -1  # force fresh word pick on next waiting paint
        self._state = new_state
        self.setToolTip(self.STATE_TOOLTIPS.get(self._state, '') if self._state else '')
        if self._state is not None:
            self._timer.start()
        else:
            self._timer.stop()
        self.update()

    # ------------------------------------------------------------------
    # Qt overrides
    # ------------------------------------------------------------------

    def _tick(self):
        self._frame = (self._frame + 1) % 3600
        self.update()

    def paintEvent(self, event):
        if self._state is None:
            return

        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        pack = active_special_theme()
        themed = _THEMED_SETS.get(pack.get('sprite')) if pack else None
        w, h = self.width(), self.height()
        elapsed = time.monotonic() - self._state_start

        if self._state == 'waiting':
            self._draw_waiting(p, pack, themed)
        else:
            draw = themed.PAINTERS.get(self._state) if themed else None
            draw = draw or status_sprite_astro.PAINTERS.get(self._state)
            if draw:
                draw(p, w, h, self._frame, elapsed)
            overlay = themed.OVERLAYS.get(self._state) if themed else None
            if overlay:
                overlay(p, w, h, self._frame, elapsed)
        p.end()

        # The stretch runs on wall-clock time and re-arms its own repaint: the
        # main QTimer tick is starved by GIL contention while the stretch itself
        # is running, which is exactly when this state is on screen.
        if self._state == 'stretching':
            QTimer.singleShot(30, self.update)

    # ------------------------------------------------------------------
    # Waiting: pulsing dots + periodic speech bubble
    # ------------------------------------------------------------------

    def _next_waiting_word(self, pack) -> str:
        theme_key = pack.get('sprite') if pack else None
        if theme_key != self._waiting_pool_theme:
            self._waiting_pool_theme = theme_key
            self._waiting_word_pool = []
        if not self._waiting_word_pool:
            pool = list((pack or {}).get('waiting_words') or self.WAITING_WORDS)
            random.shuffle(pool)
            self._waiting_word_pool = pool
        return self._waiting_word_pool.pop()

    def _draw_waiting(self, p, pack, themed):
        """Three dots pulsing in sequence, with a periodic speech bubble."""
        w, h = self.width(), self.height()
        cx, cy = w / 2.0, h / 2.0

        # Speech bubble: visible for first 200 frames of every 750-frame cycle (~8s on, ~22s off)
        SHOW_FRAMES = 200
        CYCLE = 750
        frame_in_cycle = self._frame % CYCLE
        bubble_visible = frame_in_cycle < SHOW_FRAMES

        # Shift dots into lower third when bubble is showing
        dot_cy = cy + (h * 0.22 if bubble_visible else 0.0)

        dots = getattr(themed, 'waiting_dots', None) or status_sprite_astro.waiting_dots
        dots(p, w, h, self._frame, dot_cy)

        if not bubble_visible:
            return

        # Fade in (0-15) / hold (15-185) / fade out (185-200)
        if frame_in_cycle < 15:
            fade = frame_in_cycle / 15.0
        elif frame_in_cycle > 185:
            fade = (SHOW_FRAMES - frame_in_cycle) / 15.0
        else:
            fade = 1.0

        current_cycle = self._frame // CYCLE
        if current_cycle != self._waiting_cycle:
            self._waiting_cycle = current_cycle
            self._waiting_word = self._next_waiting_word(pack)
        word = self._waiting_word

        # Bubble geometry: sits above the dots, sized to the word, tail pointing
        # down to the center dot. The font only shrinks if the widget is too
        # narrow for the word — at the old fixed 44 px it always was, and most
        # words were drawn with their ends clipped off.
        margin = 3
        bubble_h = h * 0.46
        font = QFont()
        font.setPixelSize(max(8, int(bubble_h * 0.58)))
        p.setFont(font)
        max_text_w = w - margin * 2 - 12
        text_w = p.fontMetrics().horizontalAdvance(word)
        if text_w > max_text_w:
            font.setPixelSize(max(6, int(font.pixelSize() * max_text_w / text_w)))
            p.setFont(font)
            text_w = p.fontMetrics().horizontalAdvance(word)
        bubble_w = min(w - margin * 2, max(30.0, text_w + 14.0))
        bx, by = cx - bubble_w / 2.0, 1.0
        br = 5.0
        tail_half = 5.0
        tail_tip_y = dot_cy - 2.0   # just above center dot

        # Bubble fill
        bg = QColor(Colors.bg_card)
        bg.setAlphaF(0.93 * fade)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(bg))
        p.drawRoundedRect(QRectF(bx, by, bubble_w, bubble_h), br, br)

        # Tail triangle (pointing down from bottom-center of bubble)
        tail_root_y = by + bubble_h
        tail_path = QPainterPath()
        tail_path.moveTo(cx - tail_half, tail_root_y)
        tail_path.lineTo(cx + tail_half, tail_root_y)
        tail_path.lineTo(cx, tail_tip_y)
        tail_path.closeSubpath()
        p.setBrush(QBrush(bg))
        p.drawPath(tail_path)

        # Bubble border
        border_c = QColor(Colors.accent_default)
        border_c.setAlphaF(0.45 * fade)
        p.setPen(QPen(border_c, 1.0))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(QRectF(bx, by, bubble_w, bubble_h), br, br)

        text_c = QColor(Colors.accent_text)
        text_c.setAlphaF(fade)
        p.setPen(text_c)
        p.drawText(
            QRectF(bx, by, bubble_w, bubble_h),
            Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextDontClip,
            word,
        )
