"""The conditions panel collapsed: a narrow rail down the side of Live
Monitoring that still shows the verdict (a dot in its colour and the IMAGING
tile's short text, written down the rail) and opens the panel again when
clicked anywhere, or with Space / Enter.
"""
from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import QAbstractButton, QSizePolicy

from ..theme.icons import mdi
from ..theme.tokens import Colors, Layout, Spacing, Typography

RAIL_WIDTH = 44
TITLE = "Imaging conditions"
_ICON_PX = 18
_DOT_PX = 8
_INSET = 4
_KEYBOARD_REASONS = (Qt.TabFocusReason, Qt.BacktabFocusReason, Qt.ShortcutFocusReason)


class ConditionsRail(QAbstractButton):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._status = ""
        self._color = Colors.text_secondary
        self._detail = ""
        self._keyboard_focus = False
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAttribute(Qt.WA_Hover, True)
        self.setFixedWidth(RAIL_WIDTH)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
        self.setAccessibleName(f"Show {TITLE.lower()}")
        self._refresh_tooltip()

    def set_status(self, text: str, color: str = None, detail: str = "") -> None:
        """``text`` is the short verdict ("OK · 8% cloud"); ``detail`` the
        full sentence, for the tooltip."""
        self._status = text or ""
        self._color = color or Colors.text_secondary
        self._detail = detail or ""
        self._refresh_tooltip()
        self.update()

    def status_text(self) -> str:
        return self._status

    def status_color(self) -> str:
        return self._color

    def _refresh_tooltip(self):
        lines = ["Show the imaging conditions panel"]
        if self._detail or self._status:
            lines.append(self._detail or self._status)
        self.setToolTip("\n".join(lines))

    def sizeHint(self):
        return QSize(RAIL_WIDTH, 160)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            self.click()
            return
        super().keyPressEvent(event)

    def focusInEvent(self, event):
        self._keyboard_focus = event.reason() in _KEYBOARD_REASONS
        super().focusInEvent(event)

    def focusOutEvent(self, event):
        self._keyboard_focus = False
        super().focusOutEvent(event)

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = self.rect().adjusted(_INSET, _INSET, -_INSET, -_INSET)
        if self.isDown():
            fill = QColor(255, 255, 255, 10)
        elif self.underMouse():
            fill = QColor(255, 255, 255, 18)
        else:
            fill = None
        if fill is not None:
            painter.setPen(Qt.NoPen)
            painter.setBrush(fill)
            painter.drawRoundedRect(rect, Layout.radius_sm, Layout.radius_sm)
        if self.hasFocus() and self._keyboard_focus:
            painter.setBrush(Qt.NoBrush)
            painter.setPen(QPen(QColor(Colors.border_focus), 1.5))
            painter.drawRoundedRect(rect, Layout.radius_sm, Layout.radius_sm)

        cx = self.width() / 2
        y = Spacing.card_padding
        for name in ('chevron-left', 'telescope'):
            pixmap = mdi(name, Colors.text_secondary).pixmap(_ICON_PX, _ICON_PX)
            painter.drawPixmap(int(cx - _ICON_PX / 2), int(y), _ICON_PX, _ICON_PX, pixmap)
            y += _ICON_PX + Spacing.md
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(self._color))
        painter.drawEllipse(QRectF(cx - _DOT_PX / 2, y, _DOT_PX, _DOT_PX))
        y += _DOT_PX + Spacing.md

        font = QFont(self.font())
        font.setPixelSize(Typography.size_caption)
        font.setWeight(QFont.DemiBold)
        metrics = QFontMetrics(font)
        room = self.height() - y - Spacing.card_padding
        if room < 40:
            return
        status = self._status
        if status:
            status = metrics.elidedText(status, Qt.ElideRight, int(room))
        title = TITLE if metrics.horizontalAdvance(f"{status}   {TITLE}") <= room else ""
        painter.save()
        painter.setFont(font)
        painter.translate(cx, y)
        painter.rotate(90)
        baseline = (metrics.ascent() - metrics.descent()) / 2
        x = 0.0
        if status:
            painter.setPen(QColor(self._color))
            painter.drawText(int(x), int(baseline), status)
            x += metrics.horizontalAdvance(status) + Spacing.md
        if title:
            painter.setPen(QColor(Colors.text_secondary))
            painter.drawText(int(x), int(baseline), title)
        painter.restore()
