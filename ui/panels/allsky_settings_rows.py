"""Reusable rows for the All-Sky settings page: layer toggle, colour picker,
section card. Shared by AllSkySettingsPanel and its per-layer cards."""
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFrame, QGridLayout, QSizePolicy, QPushButton,
)
from PySide6.QtCore import Qt, Signal
from qfluentwidgets import SwitchButton, CardWidget, BodyLabel, SubtitleLabel

from ..theme.tokens import Spacing, Layout


def section_card(title: str) -> tuple:
    """Create a labelled card widget. Returns (card, inner_layout)."""
    card = CardWidget()
    card.setStyleSheet(f"CardWidget {{ border-radius: {Layout.radius_md}px; }}")
    vl = QVBoxLayout(card)
    vl.setContentsMargins(Spacing.base, Spacing.base, Spacing.base, Spacing.lg)
    vl.setSpacing(Spacing.sm)

    lbl = SubtitleLabel(title)
    vl.addWidget(lbl)
    return card, vl


class LayerToggleRow(QWidget):
    """A row with a label + SwitchButton for a single layer toggle."""

    toggled = Signal(bool)

    def __init__(self, label: str, default: bool = True, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        lbl = BodyLabel(label)
        lbl.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self._switch = SwitchButton()
        self._switch.setChecked(default)
        self._switch.checkedChanged.connect(self.toggled)
        layout.addWidget(lbl)
        layout.addWidget(self._switch)

    def is_checked(self) -> bool:
        return self._switch.isChecked()

    def set_checked(self, v: bool) -> None:
        self._switch.setChecked(v)


# 10 preset overlay colors — bright enough to read against a dark sky
OVERLAY_PALETTE = [
    ('#4488FF', 'Blue'),
    ('#FF8844', 'Orange'),
    ('#88FF44', 'Green'),
    ('#FF4444', 'Red'),
    ('#44DDFF', 'Cyan'),
    ('#FFDD44', 'Yellow'),
    ('#AA66FF', 'Purple'),
    ('#FF66AA', 'Pink'),
    ('#FFFFFF', 'White'),
    ('#44FFAA', 'Teal'),
]


class _ColorPopup(QFrame):
    """Popup grid of color swatches shown when the color button is clicked."""

    color_picked = Signal(str)

    def __init__(self, selected: str, parent=None):
        super().__init__(parent, Qt.Popup | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, False)
        self.setStyleSheet(
            "QFrame { background: #2b2b2b; border: 1px solid #555; border-radius: 6px; }"
        )
        grid = QGridLayout(self)
        grid.setContentsMargins(8, 8, 8, 8)
        grid.setSpacing(4)

        cols = 5
        for i, (hex_color, name) in enumerate(OVERLAY_PALETTE):
            btn = QPushButton()
            btn.setFixedSize(28, 28)
            btn.setToolTip(name)
            btn.setCursor(Qt.PointingHandCursor)
            if hex_color == selected:
                border = '2px solid #FFFFFF'
            else:
                border = '2px solid transparent'
            btn.setStyleSheet(
                f"QPushButton {{ background: {hex_color}; border: {border}; "
                f"border-radius: 4px; min-width: 28px; min-height: 28px; }}"
                f"QPushButton:hover {{ border: 2px solid #AAAAAA; }}"
            )
            btn.clicked.connect(lambda _=False, c=hex_color: self._pick(c))
            grid.addWidget(btn, i // cols, i % cols)

    def _pick(self, color: str) -> None:
        self.color_picked.emit(color)
        self.close()


class ColorPaletteRow(QWidget):
    """Label + single color swatch (right-aligned) that opens a popup picker."""

    color_changed = Signal(str)

    def __init__(self, default_color: str = '#4488FF', parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 4)

        lbl = BodyLabel("Color")
        lbl.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        layout.addWidget(lbl)

        self._selected = default_color
        self._swatch = QPushButton()
        self._swatch.setFixedSize(32, 22)
        self._swatch.setCursor(Qt.PointingHandCursor)
        self._swatch.clicked.connect(self._show_popup)
        layout.addWidget(self._swatch)

        self._update_swatch()

    def _show_popup(self) -> None:
        popup = _ColorPopup(self._selected, self)
        popup.color_picked.connect(self._select)
        # Position below the swatch button
        pos = self._swatch.mapToGlobal(self._swatch.rect().bottomLeft())
        popup.move(pos.x(), pos.y() + 2)
        popup.show()

    def _select(self, color: str) -> None:
        self._selected = color
        self._update_swatch()
        self.color_changed.emit(color)

    def _update_swatch(self) -> None:
        self._swatch.setStyleSheet(
            f"QPushButton {{ background: {self._selected}; border: 2px solid #888; "
            f"border-radius: 4px; min-width: 32px; min-height: 22px; }}"
            f"QPushButton:hover {{ border: 2px solid #FFFFFF; }}"
        )
        # Find the color name for the tooltip
        name = self._selected
        for hex_color, color_name in OVERLAY_PALETTE:
            if hex_color == self._selected:
                name = color_name
                break
        self._swatch.setToolTip(name)

    def selected_color(self) -> str:
        return self._selected

    def set_color(self, color: str) -> None:
        self._selected = color
        self._update_swatch()

