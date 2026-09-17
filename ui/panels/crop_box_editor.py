"""
Interactive crop-box editor: the latest frame with a draggable, resizable box.

View only. The box lives in reference-frame pixels (the full frame the
thumbnail was made from); this widget converts to and from widget pixels for
painting and mouse handling, and clamps through services.output_crop so the
box it reports is always a valid crop. Emits ``box_changed`` while dragging
(for the numeric fields) and ``box_committed`` on release (for saving).
"""
from PySide6.QtWidgets import QWidget, QSizePolicy
from PySide6.QtCore import Qt, Signal, QRectF, QPointF
from PySide6.QtGui import QPainter, QPixmap, QImage, QColor, QPen, QBrush, QFont

from services.output_crop import normalise_box

from ..theme.tokens import Colors, Typography, Layout

_HANDLE_PX = 9          # drawn handle size
_HIT_PX = 12            # half-size of the handle hit area
_MARGIN_PX = 12         # letterbox margin inside the widget

# Handle ids → (x factor, y factor) of the box the handle sits on.
_CORNERS = {'nw': (0, 0), 'ne': (1, 0), 'sw': (0, 1), 'se': (1, 1)}
_EDGES = {'n': (0.5, 0), 's': (0.5, 1), 'w': (0, 0.5), 'e': (1, 0.5)}
_CURSORS = {
    'nw': Qt.SizeFDiagCursor, 'se': Qt.SizeFDiagCursor,
    'ne': Qt.SizeBDiagCursor, 'sw': Qt.SizeBDiagCursor,
    'n': Qt.SizeVerCursor, 's': Qt.SizeVerCursor,
    'w': Qt.SizeHorCursor, 'e': Qt.SizeHorCursor,
    'move': Qt.SizeAllCursor,
}


class CropBoxEditor(QWidget):
    box_changed = Signal(int, int, int, int)
    box_committed = Signal(int, int, int, int)
    visibility_changed = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pixmap = None
        self._ref_w = 0
        self._ref_h = 0
        self._box = (0, 0, 0, 0)
        self._keep_square = True
        self._active = True
        self._drag = None          # (mode, anchor_ref_x, anchor_ref_y, grab_dx, grab_dy)
        self._hover = None
        self.setMinimumHeight(300)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setMouseTracking(True)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(
            f"CropBoxEditor {{ background-color: {Colors.bg_input}; "
            f"border-radius: {Layout.radius_md}px; }}"
        )

    # ---- state -----------------------------------------------------------

    def set_frame(self, pil_thumb, ref_w: int, ref_h: int):
        """Show a new thumbnail. The box is kept (it is in reference pixels)."""
        if pil_thumb is None:
            self._pixmap = None
        else:
            if pil_thumb.mode != 'RGB':
                pil_thumb = pil_thumb.convert('RGB')
            data = pil_thumb.tobytes('raw', 'RGB')
            qimg = QImage(data, pil_thumb.width, pil_thumb.height,
                          pil_thumb.width * 3, QImage.Format_RGB888)
            self._pixmap = QPixmap.fromImage(qimg.copy())
        self.set_reference(ref_w, ref_h)

    def set_reference(self, ref_w: int, ref_h: int):
        """Set the reference frame size (usable without a thumbnail)."""
        old_w, old_h = self._ref_w, self._ref_h
        self._ref_w, self._ref_h = int(ref_w), int(ref_h)
        if (old_w, old_h) != (self._ref_w, self._ref_h) and self.has_reference() and self._box[2] > 0:
            x, y, w, h = self._box
            if old_w > 0 and old_h > 0:
                # Same region of the sky on a differently sized frame — mirrors
                # what services.output_crop.resolve_crop_box does at run time.
                sx, sy = self._ref_w / old_w, self._ref_h / old_h
                x, y, w, h = x * sx, y * sy, w * sx, h * sy
            self._box = normalise_box(x, y, w, h, self._ref_w, self._ref_h, self._keep_square)
        self.update()

    def has_reference(self) -> bool:
        return self._ref_w > 0 and self._ref_h > 0

    def reference(self):
        return self._ref_w, self._ref_h

    def box(self):
        return self._box

    def set_box(self, x, y, w, h):
        if self.has_reference():
            self._box = normalise_box(x, y, w, h, self._ref_w, self._ref_h, self._keep_square)
        else:
            self._box = (int(x), int(y), int(w), int(h))
        self.update()

    def set_keep_square(self, keep: bool):
        self._keep_square = bool(keep)
        if self.has_reference() and self._box[2] > 0:
            self._box = normalise_box(*self._box, self._ref_w, self._ref_h, self._keep_square)
        self.update()

    def set_active(self, active: bool):
        """Dim the box when the crop is switched off (still editable)."""
        self._active = bool(active)
        self.update()

    # ---- geometry ----------------------------------------------------------

    def _image_rect(self) -> QRectF:
        """Where the reference frame is drawn, letterboxed inside the widget."""
        if not self.has_reference():
            return QRectF()
        avail_w = max(1, self.width() - 2 * _MARGIN_PX)
        avail_h = max(1, self.height() - 2 * _MARGIN_PX)
        scale = min(avail_w / self._ref_w, avail_h / self._ref_h)
        draw_w, draw_h = self._ref_w * scale, self._ref_h * scale
        return QRectF((self.width() - draw_w) / 2, (self.height() - draw_h) / 2, draw_w, draw_h)

    def _scale(self) -> float:
        rect = self._image_rect()
        return rect.width() / self._ref_w if self._ref_w else 1.0

    def _box_rect(self) -> QRectF:
        rect = self._image_rect()
        s = self._scale()
        x, y, w, h = self._box
        return QRectF(rect.x() + x * s, rect.y() + y * s, w * s, h * s)

    def _to_ref(self, pos: QPointF):
        rect = self._image_rect()
        s = self._scale() or 1.0
        return (pos.x() - rect.x()) / s, (pos.y() - rect.y()) / s

    def _handles(self):
        """Handle id → widget-space centre point for the current box."""
        r = self._box_rect()
        ids = dict(_CORNERS)
        if not self._keep_square:
            ids.update(_EDGES)
        return {
            hid: QPointF(r.x() + fx * r.width(), r.y() + fy * r.height())
            for hid, (fx, fy) in ids.items()
        }

    def _hit(self, pos: QPointF):
        if not self.has_reference() or self._box[2] <= 0:
            return None
        for hid, centre in self._handles().items():
            if abs(pos.x() - centre.x()) <= _HIT_PX and abs(pos.y() - centre.y()) <= _HIT_PX:
                return hid
        if self._box_rect().contains(pos):
            return 'move'
        return None

    # ---- mouse -------------------------------------------------------------

    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton:
            return super().mousePressEvent(event)
        hid = self._hit(event.position())
        if hid is None:
            return super().mousePressEvent(event)
        x, y, w, h = self._box
        rx, ry = self._to_ref(event.position())
        if hid == 'move':
            self._drag = ('move', 0, 0, rx - x, ry - y)
        else:
            fx, fy = (_CORNERS.get(hid) or _EDGES[hid])
            # The anchor is the box edge/corner opposite the grabbed handle.
            ax = x if fx == 1 else (x + w if fx == 0 else None)
            ay = y if fy == 1 else (y + h if fy == 0 else None)
            self._drag = (hid, ax, ay, 0, 0)
        self.setCursor(_CURSORS[hid])
        event.accept()

    def mouseMoveEvent(self, event):
        if self._drag is None:
            hid = self._hit(event.position())
            if hid != self._hover:
                self._hover = hid
                self.setCursor(_CURSORS[hid] if hid else Qt.ArrowCursor)
            return super().mouseMoveEvent(event)
        mode, ax, ay, gdx, gdy = self._drag
        rx, ry = self._to_ref(event.position())
        x, y, w, h = self._box
        if mode == 'move':
            new = (rx - gdx, ry - gdy, w, h)
        else:
            nx0, nx1 = (min(ax, rx), max(ax, rx)) if ax is not None else (x, x + w)
            ny0, ny1 = (min(ay, ry), max(ay, ry)) if ay is not None else (y, y + h)
            nw, nh = nx1 - nx0, ny1 - ny0
            if self._keep_square:
                edge = min(nw, nh)
                nx0 = ax - edge if (ax is not None and rx < ax) else nx0
                ny0 = ay - edge if (ay is not None and ry < ay) else ny0
                nw = nh = edge
            new = (nx0, ny0, nw, nh)
        self._box = normalise_box(*new, self._ref_w, self._ref_h, self._keep_square)
        self.update()
        self.box_changed.emit(*self._box)
        event.accept()

    def mouseReleaseEvent(self, event):
        if self._drag is not None and event.button() == Qt.LeftButton:
            self._drag = None
            self.setCursor(Qt.ArrowCursor)
            self.box_committed.emit(*self._box)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def showEvent(self, event):
        super().showEvent(event)
        self.visibility_changed.emit(True)

    def hideEvent(self, event):
        super().hideEvent(event)
        self.visibility_changed.emit(False)

    # ---- painting ----------------------------------------------------------

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        if not self.has_reference():
            self._paint_placeholder(painter, "Start capture to see a frame here")
            painter.end()
            return

        rect = self._image_rect()
        if self._pixmap is not None:
            painter.drawPixmap(rect.toRect(), self._pixmap)
        else:
            painter.fillRect(rect, QColor(Colors.gray_4))
            painter.setPen(QPen(QColor(Colors.border_default), 1, Qt.DashLine))
            painter.drawRect(rect)
            self._paint_placeholder(painter, f"Sensor {self._ref_w} × {self._ref_h} — waiting for a frame")

        if self._box[2] <= 0:
            painter.end()
            return

        box = self._box_rect()
        if self._active:
            shade = QColor(0, 0, 0, 150)
            painter.setPen(Qt.NoPen)
            painter.setBrush(shade)
            # Four strips around the box: everything outside it is what gets cut.
            painter.drawRect(QRectF(rect.x(), rect.y(), rect.width(), box.y() - rect.y()))
            painter.drawRect(QRectF(rect.x(), box.bottom(), rect.width(), rect.bottom() - box.bottom()))
            painter.drawRect(QRectF(rect.x(), box.y(), box.x() - rect.x(), box.height()))
            painter.drawRect(QRectF(box.right(), box.y(), rect.right() - box.right(), box.height()))

        edge_colour = QColor(Colors.accent_text if self._active else Colors.text_muted)
        pen = QPen(edge_colour, 2 if self._active else 1)
        if not self._active:
            pen.setStyle(Qt.DashLine)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        painter.drawRect(box)

        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(edge_colour))
        half = _HANDLE_PX / 2
        for centre in self._handles().values():
            painter.drawRect(QRectF(centre.x() - half, centre.y() - half, _HANDLE_PX, _HANDLE_PX))

        x, y, w, h = self._box
        label = f"{w} × {h}  at  ({x}, {y})"
        font = QFont()
        font.setPointSize(Typography.size_caption if hasattr(Typography, 'size_caption') else 9)
        painter.setFont(font)
        metrics = painter.fontMetrics()
        tw, th = metrics.horizontalAdvance(label) + 12, metrics.height() + 6
        lx = min(max(box.x(), rect.x()), rect.right() - tw)
        ly = box.bottom() + 4 if box.bottom() + 4 + th <= rect.bottom() else box.y() - th - 4
        painter.setBrush(QColor(0, 0, 0, 170))
        painter.setPen(Qt.NoPen)
        painter.drawRoundedRect(QRectF(lx, ly, tw, th), 4, 4)
        painter.setPen(QColor(Colors.text_primary))
        painter.drawText(QRectF(lx, ly, tw, th), Qt.AlignCenter, label)
        painter.end()

    def _paint_placeholder(self, painter: QPainter, text: str):
        painter.setPen(QColor(Colors.text_muted))
        painter.drawText(self.rect(), Qt.AlignCenter, text)
