"""The clickable header row of a collapsible card or panel.

One button for the whole row: icon, title, an optional short status on the
right and a chevron that follows the state. Hover and pressed feedback, a
pointing-hand cursor, and a keyboard path (Tab to it, Space or Enter).
``CollapsibleCard`` and Live Monitoring's conditions panel both use it, so
every accordion in the app behaves the same way.
"""
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QAbstractButton, QHBoxLayout, QLabel, QSizePolicy
from qfluentwidgets import SubtitleLabel

from ..theme.icons import mdi
from ..theme.tokens import Colors, Layout, Spacing, Typography

ICON_PX = 20
CHEVRON_PX = 18
_INSET = 4
_KEYBOARD_REASONS = (Qt.TabFocusReason, Qt.BacktabFocusReason, Qt.ShortcutFocusReason)


def _pixmap(icon, size):
    """A FluentIcon (has ``.icon()``) or a ready-made QIcon."""
    if hasattr(icon, 'icon'):
        icon = icon.icon()
    return icon.pixmap(size, size) if hasattr(icon, 'pixmap') else None


class CollapseHeader(QAbstractButton):
    """``clicked`` is the request to toggle; the owner calls ``set_expanded``."""

    def __init__(self, title: str, icon=None, *, expanded=False,
                 expanded_icon='chevron-down', collapsed_icon='chevron-right',
                 expanded_tip="Collapse", collapsed_tip="Expand", parent=None):
        super().__init__(parent)
        self._icons = {True: expanded_icon, False: collapsed_icon}
        self._tips = {True: expanded_tip, False: collapsed_tip}
        self._expanded = None
        self._keyboard_focus = False
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAttribute(Qt.WA_Hover, True)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setAccessibleName(title)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(Spacing.base, Spacing.sm, Spacing.md, Spacing.sm)
        lay.setSpacing(Spacing.sm)
        self.icon_label = QLabel()
        pixmap = _pixmap(icon, ICON_PX) if icon is not None else None
        if pixmap is not None:
            self.icon_label.setPixmap(pixmap)
            lay.addWidget(self.icon_label)
        self.title_label = SubtitleLabel(title)
        self.title_label.setStyleSheet(f"color: {Colors.text_primary};")
        lay.addWidget(self.title_label, 1)
        self.status_label = QLabel()
        self.status_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.status_label.hide()
        lay.addWidget(self.status_label)
        self.chevron = QLabel()
        self.chevron.setFixedSize(CHEVRON_PX, CHEVRON_PX)
        lay.addWidget(self.chevron)
        for label in (self.icon_label, self.title_label, self.status_label, self.chevron):
            label.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.set_expanded(expanded)

    # -- state -------------------------------------------------------------

    def is_expanded(self) -> bool:
        return bool(self._expanded)

    def chevron_name(self) -> str:
        return self._icons[bool(self._expanded)]

    def set_expanded(self, expanded: bool) -> None:
        expanded = bool(expanded)
        if expanded == self._expanded:
            return
        self._expanded = expanded
        self.chevron.setPixmap(
            mdi(self._icons[expanded], Colors.text_secondary).pixmap(CHEVRON_PX, CHEVRON_PX))
        self.setToolTip(self._tips[expanded])

    def set_status(self, text: str = "", color: str = None) -> None:
        """A short state shown right of the title (hidden when empty)."""
        self.status_label.setText(text or "")
        self.status_label.setStyleSheet(
            f"color: {color or Colors.text_secondary}; "
            f"font-size: {Typography.size_caption}px; font-weight: 600;")
        self.status_label.setVisible(bool(text))

    # -- behaviour ---------------------------------------------------------

    def sizeHint(self):
        return self.layout().sizeHint()

    def minimumSizeHint(self):
        return self.layout().minimumSize()

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
        elif self.underMouse() and self.isEnabled():
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
