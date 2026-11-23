"""Status strip's right-hand capture / camera state pill."""
from PySide6.QtWidgets import QFrame, QHBoxLayout, QVBoxLayout, QLabel
from PySide6.QtCore import Qt, QSize, Signal

from ..theme.tokens import Colors
from ..theme.icons import mdi
from ..theme.styles import paint_border_lines
from .status_strip_fit import (
    FitItem, chrome_width, elide_label_text, label_elide_steps, label_text_width,
)

ICON_SIZE = QSize(18, 18)


class StatePill(QFrame):
    """Right-hand capture/camera state indicator (dot or icon + label + value).
    It keeps its text; only as a last resort does the value elide."""

    content_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._state_key = None
        self._text = ""
        self._elide_w = None
        self._build()

    def _build(self):
        self.setAttribute(Qt.WA_StyledBackground, True)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(16, 0, 18, 0)
        lay.setSpacing(10)

        self.indicator = QLabel()
        self.indicator.setFixedSize(ICON_SIZE)
        lay.addWidget(self.indicator, 0, Qt.AlignVCenter)

        col = QVBoxLayout()
        col.setSpacing(1)
        col.setContentsMargins(0, 0, 0, 0)
        self.eyebrow = QLabel("CAMERA")
        self.eyebrow.setStyleSheet(
            f"color: {Colors.text_muted}; font-size: 10px; font-weight: 600; "
            f"letter-spacing: 0.07em; border: none;"
        )
        self.value = QLabel("Idle")
        col.addStretch(1)
        col.addWidget(self.eyebrow)
        col.addWidget(self.value)
        col.addStretch(1)
        lay.addLayout(col)

    def _dot_pixmap(self, color: str):
        from PySide6.QtGui import QPixmap, QPainter, QColor, QBrush
        pm = QPixmap(ICON_SIZE)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(QColor(color)))
        d = 9
        off = (ICON_SIZE.width() - d) // 2
        p.drawEllipse(off, off, d, d)
        p.end()
        return pm

    def set_state(self, mode: str, text: str):
        """mode in {'capturing', 'reconnecting', 'idle', 'disconnected'}."""
        if (mode, text) == self._state_key:
            return
        self._state_key = (mode, text)
        if mode == 'capturing':
            bg, label, vcolor = Colors.success_bg, "CAPTURE", Colors.success_text
            self.indicator.setPixmap(self._dot_pixmap(Colors.success_default))
        elif mode == 'reconnecting':
            bg, label, vcolor = Colors.warning_bg, "CAPTURE", Colors.warning_text
            self.indicator.setPixmap(self._dot_pixmap(Colors.warning_default))
        elif mode == 'disconnected':
            bg, label, vcolor = Colors.error_bg, "CAMERA", Colors.error_text
            self.indicator.setPixmap(mdi('camera-off-outline', Colors.error_text).pixmap(ICON_SIZE))
        else:  # idle
            bg, label, vcolor = Colors.bg_card, "CAMERA", Colors.text_secondary
            self.indicator.setPixmap(self._dot_pixmap(Colors.gray_8))

        self.setStyleSheet(f"StatePill {{ background: {bg}; }}")
        self._text = text
        self.value.setStyleSheet(
            f"color: {vcolor}; font-size: 13px; font-weight: 700; border: none;"
        )
        self.eyebrow.setText(label)
        self._present()
        self.content_changed.emit()

    @property
    def full_text(self) -> str:
        return self._text

    def set_value_width(self, width: int = None):
        """Elide the value to ``width`` px (None shows it whole)."""
        if width == self._elide_w:
            return
        self._elide_w = width
        self._present()

    def fit_item(self, key, elide_rank=None) -> FitItem:
        return FitItem(
            key=key,
            chrome_w=chrome_width(self.layout(), ICON_SIZE.width()),
            label_w=self.eyebrow.sizeHint().width(),
            value_w=label_text_width(self.value, self._text),
            steps=label_elide_steps(self.value, self._text),
            elide_rank=elide_rank,
        )

    def _present(self):
        shown = self._text
        if self._elide_w is not None:
            shown = elide_label_text(self.value, self._text, self._elide_w)
        self.value.setText(shown)
        self.setToolTip(self._text if shown != self._text else "")

    def paintEvent(self, event):
        super().paintEvent(event)
        h = self.height()
        paint_border_lines(self, [
            (0, 0, 0, h - 1),                  # left divider
            (0, h - 1, self.width(), h - 1),   # bottom border
        ])
