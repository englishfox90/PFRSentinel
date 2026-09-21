"""
Zoomable, pannable image canvas for picking stars.

Fills whatever space its layout gives it (the guided-calibration dialog used a
fixed 760 px preview — about a fifth of a 3552 px frame, on any monitor).
Scroll zooms about the cursor, dragging pans, double-click returns to the
whole frame, and a click that did not drag reports its position in ORIGINAL
image pixels, so callers never see the view transform.

Display only: markers, hints and the pending pick are handed in by the owner.
"""
from dataclasses import dataclass
from typing import List, Optional, Tuple

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QSizePolicy, QWidget

_ZOOM_STEP = 1.25
# Furthest zoom: this many screen pixels per image pixel. Past ~6 a star is a
# handful of blurry blocks and the snap does the precise work anyway.
_MAX_SCREEN_PX_PER_IMAGE_PX = 6.0
_DRAG_THRESHOLD_PX = 5

# Hover loupe: at whole-frame zoom a 3552 px frame shows ~4 raw pixels per
# screen pixel, too coarse to tell close stars apart (Mizar vs Alioth). Shown
# only while the view is coarser than native — zoomed in, it would just cover
# the stars being picked.
_LOUPE_SIZE = 200
_LOUPE_NATIVE = 150
_LOUPE_OFFSET = 24

_COLOURS = {
    'ok':       QColor(60, 220, 60),
    'suspect':  QColor(255, 80, 80),
    'excluded': QColor(255, 160, 60),
    'renamed':  QColor(255, 160, 60),
}
_PENDING = QColor(255, 210, 60)
_HINT = QColor(90, 200, 255)
_HINT_UNSUPPORTED = QColor(90, 200, 255, 110)


@dataclass
class CanvasMarker:
    x: float
    y: float
    label: str
    state: str = 'ok'          # 'ok' | 'suspect' | 'excluded' | 'renamed'


@dataclass
class CanvasHint:
    x: float
    y: float
    label: str
    supported: bool = True
    emphasised: bool = False   # review mode: drawn bolder, as the result


class StarPickCanvas(QWidget):
    """Image view with zoom/pan that reports clicks in image coordinates."""

    clicked = Signal(float, float)      # image x, y
    zoom_changed = Signal(float)        # zoom relative to whole-frame fit

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMinimumSize(320, 320)
        self.setMouseTracking(True)
        self.setCursor(Qt.CrossCursor)
        self.setFocusPolicy(Qt.StrongFocus)

        self._full: Optional[QPixmap] = None
        self._zoom = 1.0                 # 1.0 = whole frame fits the widget
        self._ox = 0.0                   # image coords at the widget's top-left
        self._oy = 0.0
        self._view: Optional[QPixmap] = None
        self._view_smooth = False
        self._markers: List[CanvasMarker] = []
        self._hints: List[CanvasHint] = []
        self._pending: Optional[Tuple[float, float]] = None
        self._snap_r = 0.0
        self._interactive = True

        self._press_pos: Optional[QPointF] = None
        self._press_button = None
        self._press_origin = (0.0, 0.0)
        self._dragging = False
        self._cursor: Optional[QPointF] = None

        # Smooth scaling a 12 MP frame costs tens of ms; do it once the wheel
        # or drag settles and use the fast path while the view is moving.
        self._smooth_timer = QTimer(self)
        self._smooth_timer.setSingleShot(True)
        self._smooth_timer.setInterval(90)
        self._smooth_timer.timeout.connect(self._render_smooth)

    # ------------------------------------------------------------------
    # Owner API
    # ------------------------------------------------------------------

    def set_image(self, pixmap: QPixmap, snap_radius_px: float = 0.0) -> None:
        self._full = pixmap
        self._snap_r = float(snap_radius_px)
        self.reset_view()

    def set_overlays(self, markers: List[CanvasMarker],
                     hints: List[CanvasHint],
                     pending: Optional[Tuple[float, float]]) -> None:
        self._markers = list(markers)
        self._hints = list(hints)
        self._pending = pending
        self.update()

    def set_interactive(self, enabled: bool) -> None:
        """Picking off (zoom and pan stay on) — while solving or reviewing."""
        self._interactive = enabled
        self.setCursor(Qt.CrossCursor if enabled else Qt.OpenHandCursor)

    def zoom(self) -> float:
        return self._zoom

    def zoom_in(self) -> None:
        self._zoom_about(self._widget_centre(), _ZOOM_STEP)

    def zoom_out(self) -> None:
        self._zoom_about(self._widget_centre(), 1.0 / _ZOOM_STEP)

    def reset_view(self) -> None:
        self._zoom = 1.0
        self._clamp_origin()
        self._invalidate(smooth=True)
        self.zoom_changed.emit(self._zoom)

    def centre_on(self, ix: float, iy: float, min_zoom: float = 1.0) -> None:
        """Bring an image point to the middle of the view."""
        if self._full is None:
            return
        self._zoom = min(max(self._zoom, min_zoom), self._max_zoom())
        s = self._scale()
        self._ox = ix - self.width() / (2.0 * s)
        self._oy = iy - self.height() / (2.0 * s)
        self._clamp_origin()
        self._invalidate(smooth=True)
        self.zoom_changed.emit(self._zoom)

    def image_to_widget(self, ix: float, iy: float) -> QPointF:
        s = self._scale()
        return QPointF((ix - self._ox) * s, (iy - self._oy) * s)

    def widget_to_image(self, pos: QPointF) -> Tuple[float, float]:
        s = self._scale()
        return self._ox + pos.x() / s, self._oy + pos.y() / s

    # ------------------------------------------------------------------
    # View transform
    # ------------------------------------------------------------------

    def _fit_scale(self) -> float:
        if self._full is None or self._full.isNull():
            return 1.0
        return min(self.width() / self._full.width(),
                   self.height() / self._full.height())

    def _scale(self) -> float:
        """Logical (layout) pixels per image pixel."""
        return max(1e-6, self._fit_scale() * self._zoom)

    def _device_scale(self) -> float:
        """Physical screen pixels per image pixel — what the eye gets."""
        return self._scale() * self.devicePixelRatioF()

    def _max_zoom(self) -> float:
        fit_device = max(1e-6, self._fit_scale() * self.devicePixelRatioF())
        return max(1.0, _MAX_SCREEN_PX_PER_IMAGE_PX / fit_device)

    def _widget_centre(self) -> QPointF:
        return QPointF(self.width() / 2.0, self.height() / 2.0)

    def _clamp_origin(self) -> None:
        """Keep the frame on screen; centre it on any axis it doesn't fill."""
        if self._full is None:
            return
        s = self._scale()
        for axis, (extent, size) in enumerate((
                (self.width() / s, self._full.width()),
                (self.height() / s, self._full.height()))):
            origin = self._ox if axis == 0 else self._oy
            if extent >= size:
                origin = (size - extent) / 2.0
            else:
                origin = min(max(origin, 0.0), size - extent)
            if axis == 0:
                self._ox = origin
            else:
                self._oy = origin

    def _zoom_about(self, pos: QPointF, factor: float) -> None:
        if self._full is None:
            return
        ix, iy = self.widget_to_image(pos)
        new_zoom = min(max(self._zoom * factor, 1.0), self._max_zoom())
        if abs(new_zoom - self._zoom) < 1e-9:
            return
        self._zoom = new_zoom
        s = self._scale()
        self._ox = ix - pos.x() / s
        self._oy = iy - pos.y() / s
        self._clamp_origin()
        self._invalidate(smooth=False)
        self.zoom_changed.emit(self._zoom)

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------

    def _invalidate(self, smooth: bool) -> None:
        self._view = None
        self._view_smooth = smooth
        if not smooth:
            self._smooth_timer.start()
        self.update()

    def _render_smooth(self) -> None:
        self._view = None
        self._view_smooth = True
        self.update()

    def _render_view(self) -> QPixmap:
        # Rendered at the display's pixel ratio: at 150-200 % scaling a
        # logical-size pixmap throws away up to three quarters of the pixels
        # a 4K monitor has, which is the room issue #79 asked for.
        dpr = self.devicePixelRatioF()
        view = QPixmap(self.size() * dpr)
        view.setDevicePixelRatio(dpr)
        view.fill(QColor(0, 0, 0))
        if self._full is None or self._full.isNull():
            return view
        s = self._scale()
        visible = QRectF(self._ox, self._oy, self.width() / s, self.height() / s)
        src = visible.intersected(QRectF(self._full.rect()))
        if src.isEmpty():
            return view
        src_px = src.toAlignedRect()
        target = QRectF((src_px.x() - self._ox) * s, (src_px.y() - self._oy) * s,
                        src_px.width() * s, src_px.height() * s)
        # QImage.scaled() area-averages a big reduction; drawPixmap's smooth
        # hint is bilinear only and drops faint stars at whole-frame zoom.
        mode = Qt.SmoothTransformation if self._view_smooth else Qt.FastTransformation
        piece = self._full.copy(src_px).scaled(
            max(1, round(target.width() * dpr)),
            max(1, round(target.height() * dpr)),
            Qt.IgnoreAspectRatio, mode)
        piece.setDevicePixelRatio(dpr)
        p = QPainter(view)
        try:
            p.drawPixmap(target.topLeft(), piece)
        finally:
            p.end()
        return view

    def paintEvent(self, _ev):
        if (self._view is None
                or self._view.deviceIndependentSize().toSize() != self.size()):
            self._view = self._render_view()
        p = QPainter(self)
        try:
            p.drawPixmap(0, 0, self._view)
            p.setRenderHint(QPainter.Antialiasing, True)
            self._paint_hints(p)
            self._paint_markers(p)
            self._paint_pending(p)
            self._paint_loupe(p)
        finally:
            p.end()

    def _paint_hints(self, p: QPainter) -> None:
        font = QFont(p.font())
        font.setPointSizeF(max(7.0, font.pointSizeF() - 1.0))
        for h in self._hints:
            pt = self.image_to_widget(h.x, h.y)
            if not self.rect().contains(pt.toPoint()):
                continue
            colour = _HINT if (h.supported or h.emphasised) else _HINT_UNSUPPORTED
            pen = QPen(colour, 2 if h.emphasised else 1)
            if not h.emphasised:
                pen.setStyle(Qt.DashLine)
            p.setPen(pen)
            r = 8 if h.emphasised else 7
            p.drawEllipse(pt, r, r)
            p.setFont(font)
            p.drawText(QPointF(pt.x() + r + 3, pt.y() + 4), h.label)

    def _paint_markers(self, p: QPainter) -> None:
        font = QFont(p.font())
        font.setBold(True)
        p.setFont(font)
        for m in self._markers:
            pt = self.image_to_widget(m.x, m.y)
            p.setPen(QPen(_COLOURS.get(m.state, _COLOURS['ok']), 2))
            p.drawEllipse(pt, 9, 9)
            p.drawText(QPointF(pt.x() + 12, pt.y() - 6), m.label)

    def _paint_pending(self, p: QPainter) -> None:
        if self._pending is None:
            return
        pt = self.image_to_widget(*self._pending)
        p.setPen(QPen(_PENDING, 2))
        p.drawEllipse(pt, 11, 11)
        p.drawLine(QPointF(pt.x() - 16, pt.y()), QPointF(pt.x() - 5, pt.y()))
        p.drawLine(QPointF(pt.x() + 5, pt.y()), QPointF(pt.x() + 16, pt.y()))
        p.drawLine(QPointF(pt.x(), pt.y() - 16), QPointF(pt.x(), pt.y() - 5))
        p.drawLine(QPointF(pt.x(), pt.y() + 5), QPointF(pt.x(), pt.y() + 16))

    def _paint_loupe(self, p: QPainter) -> None:
        if (self._cursor is None or self._dragging or not self._interactive
                or self._full is None or self._device_scale() >= 1.0):
            return
        ix, iy = self.widget_to_image(self._cursor)
        half = _LOUPE_NATIVE / 2.0
        src = QRectF(ix - half, iy - half, _LOUPE_NATIVE, _LOUPE_NATIVE)
        lx = self._cursor.x() + _LOUPE_OFFSET
        ly = self._cursor.y() + _LOUPE_OFFSET
        if lx + _LOUPE_SIZE > self.width():
            lx = self._cursor.x() - _LOUPE_SIZE - _LOUPE_OFFSET
        if ly + _LOUPE_SIZE > self.height():
            ly = self._cursor.y() - _LOUPE_SIZE - _LOUPE_OFFSET
        box = QRectF(max(0.0, lx), max(0.0, ly), _LOUPE_SIZE, _LOUPE_SIZE)
        p.fillRect(box, QColor(0, 0, 0))
        p.setRenderHint(QPainter.SmoothPixmapTransform, True)
        p.drawPixmap(box, self._full, src)
        p.setPen(QPen(QColor(153, 153, 153), 1))
        p.drawRect(box)
        c = box.center()
        p.setPen(QPen(_PENDING, 1))
        for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            p.drawLine(QPointF(c.x() + dx * 4, c.y() + dy * 4),
                       QPointF(c.x() + dx * 14, c.y() + dy * 14))
        if self._snap_r > 0:
            r = self._snap_r * _LOUPE_SIZE / float(_LOUPE_NATIVE)
            if r < _LOUPE_SIZE / 2.0:
                p.setPen(QPen(QColor(60, 220, 60, 160), 1))
                p.drawEllipse(c, r, r)

    # ------------------------------------------------------------------
    # Input
    # ------------------------------------------------------------------

    def wheelEvent(self, ev):
        delta = ev.angleDelta().y()
        if delta:
            self._zoom_about(ev.position(), _ZOOM_STEP ** (delta / 120.0))
        ev.accept()

    def mousePressEvent(self, ev):
        if ev.button() in (Qt.LeftButton, Qt.MiddleButton, Qt.RightButton):
            self._press_pos = ev.position()
            self._press_button = ev.button()
            self._press_origin = (self._ox, self._oy)
            self._dragging = False
        self.setFocus()

    def mouseMoveEvent(self, ev):
        self._cursor = ev.position()
        if self._press_pos is not None:
            delta = ev.position() - self._press_pos
            if not self._dragging and (abs(delta.x()) + abs(delta.y())
                                       > _DRAG_THRESHOLD_PX):
                self._dragging = True
                self.setCursor(Qt.ClosedHandCursor)
            if self._dragging:
                s = self._scale()
                self._ox = self._press_origin[0] - delta.x() / s
                self._oy = self._press_origin[1] - delta.y() / s
                self._clamp_origin()
                self._invalidate(smooth=False)
                return
        self.update()

    def mouseReleaseEvent(self, ev):
        was_drag = self._dragging
        pressed = self._press_pos is not None
        button = self._press_button
        self._press_pos = None
        self._dragging = False
        self.setCursor(Qt.CrossCursor if self._interactive else Qt.OpenHandCursor)
        if (pressed and not was_drag and button == Qt.LeftButton
                and self._interactive and self._full is not None):
            ix, iy = self.widget_to_image(ev.position())
            if (0 <= ix < self._full.width() and 0 <= iy < self._full.height()):
                self.clicked.emit(ix, iy)
        self.update()

    def mouseDoubleClickEvent(self, _ev):
        # The first click of the pair already reported a pick; the owner's
        # pending marker simply moves with the view.
        self.reset_view()

    def keyPressEvent(self, ev):
        if ev.key() in (Qt.Key_Plus, Qt.Key_Equal):
            self.zoom_in()
        elif ev.key() in (Qt.Key_Minus, Qt.Key_Underscore):
            self.zoom_out()
        elif ev.key() == Qt.Key_0:
            self.reset_view()
        else:
            super().keyPressEvent(ev)

    def leaveEvent(self, ev):
        self._cursor = None
        self.update()
        super().leaveEvent(ev)

    def resizeEvent(self, ev):
        self._clamp_origin()
        self._invalidate(smooth=True)
        super().resizeEvent(ev)
