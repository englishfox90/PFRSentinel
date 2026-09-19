"""
Appearance card for the Settings page: accent swatches, a divider, then the
special theme chips. Layout and selection state only — the panel that owns it
saves the choice and the main window applies it.
"""
from PySide6.QtCore import Qt, Signal, QSize
from PySide6.QtWidgets import QWidget, QHBoxLayout, QPushButton, QFrame, QGraphicsOpacityEffect
from qfluentwidgets import CaptionLabel

from ..components.cards import SettingsCard
from ..theme.accent_themes import ACCENT_PRESETS
from ..theme.icons import mdi
from ..theme.special_themes import SPECIAL_THEMES
from ..theme.tokens import Colors, Spacing


class AppearanceCard(SettingsCard):
    """Accent swatches | special theme chips."""

    accent_selected = Signal(str)          # accent preset key
    special_theme_selected = Signal(str)   # pack key, '' when switched off

    def __init__(self, parent=None):
        super().__init__(
            "Appearance",
            "Accent colour, or a special theme that also restyles headings, the "
            "navigation icons and the status animation — dark theme is always preserved",
            parent,
        )
        self._swatches: dict[str, QPushButton] = {}
        self._chips: dict[str, QPushButton] = {}
        self._build()

    def _build(self):
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(Spacing.sm)
        row.setAlignment(Qt.AlignmentFlag.AlignLeft)

        # The global stylesheet paints every bare QWidget in the app background,
        # which showed as a black box across the row. Scoped by object name so
        # the swatches' own backgrounds are untouched.
        self._swatch_box = QWidget()
        self._swatch_box.setObjectName("appearanceSwatches")
        self._swatch_box.setStyleSheet("#appearanceSwatches { background: transparent; }")
        swatch_row = QHBoxLayout(self._swatch_box)
        swatch_row.setContentsMargins(0, 0, 0, 0)
        swatch_row.setSpacing(Spacing.sm)
        for key, preset in ACCENT_PRESETS.items():
            btn = QPushButton()
            btn.setFixedSize(28, 28)
            btn.setCheckable(True)
            btn.setToolTip(preset['label'])
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: {preset['swatch']};
                    border-radius: 14px;
                    border: 2px solid transparent;
                }}
                QPushButton:hover {{ border-color: rgba(255,255,255,0.5); }}
                QPushButton:checked {{ border-color: white; border-width: 3px; }}
            """)
            btn.clicked.connect(lambda checked, k=key: self._on_swatch(k))
            swatch_row.addWidget(btn)
            self._swatches[key] = btn
        self._swatch_fade = QGraphicsOpacityEffect(self._swatch_box)
        self._swatch_fade.setOpacity(1.0)
        self._swatch_box.setGraphicsEffect(self._swatch_fade)
        row.addWidget(self._swatch_box)

        if SPECIAL_THEMES:
            divider = QFrame()
            divider.setFixedSize(1, 24)
            divider.setStyleSheet(f"background-color: {Colors.border_default}; border: none;")
            row.addSpacing(Spacing.sm)
            row.addWidget(divider)
            row.addSpacing(Spacing.sm)

        for key, pack in SPECIAL_THEMES.items():
            chip = QPushButton(pack['label'])
            chip.setCheckable(True)
            chip.setFixedHeight(30)
            chip.setIconSize(QSize(16, 16))
            chip.setCursor(Qt.CursorShape.PointingHandCursor)
            chip.setToolTip(f"{pack['label']} theme — click again to switch it off")
            chip.clicked.connect(lambda checked, k=key: self._on_chip(k, checked))
            row.addWidget(chip)
            self._chips[key] = chip

        holder = QWidget()
        holder.setObjectName("appearanceRow")
        holder.setStyleSheet("#appearanceRow { background: transparent; }")
        holder.setLayout(row)
        self.add_row("Theme", holder)

        self._restart_note = CaptionLabel(
            "Headings, icons and animations change straight away. Some panels "
            "keep their previous colours until the app is restarted."
        )
        self._restart_note.setWordWrap(True)
        self._restart_note.setStyleSheet(f"color: {Colors.text_muted};")
        self._restart_note.hide()
        self.add_widget(self._restart_note)

        self._style_chips()

    def _style_chips(self):
        for key, chip in self._chips.items():
            chip.setIcon(mdi(SPECIAL_THEMES[key]['icon'], Colors.text_primary))
            chip.setStyleSheet(f"""
                QPushButton {{
                    background-color: {Colors.bg_input};
                    color: {Colors.text_secondary};
                    border: 1px solid {Colors.border_default};
                    border-radius: 15px;
                    padding: 0 12px 0 8px;
                }}
                QPushButton:hover {{ background-color: {Colors.bg_hover}; color: {Colors.text_primary}; }}
                QPushButton:checked {{
                    background-color: {Colors.accent_active};
                    border-color: {Colors.accent_default};
                    color: {Colors.iris_12};
                }}
            """)

    def _on_swatch(self, key: str):
        # Picking an accent is also how you leave a special theme.
        had_special = any(chip.isChecked() for chip in self._chips.values())
        self.set_selection(key, '')
        if had_special:
            self.special_theme_selected.emit('')
        self.accent_selected.emit(key)
        self._restart_note.setVisible(had_special)

    def _on_chip(self, key: str, checked: bool):
        special = key if checked else ''
        self.set_selection(self._current_accent(), special)
        self.special_theme_selected.emit(special)
        self._restart_note.show()

    def _current_accent(self) -> str:
        for key, btn in self._swatches.items():
            if btn.isChecked():
                return key
        return 'iris'

    def set_selection(self, accent: str, special: str):
        """Reflect the saved choice without emitting anything."""
        for key, btn in self._swatches.items():
            btn.setChecked(key == accent)
        for key, chip in self._chips.items():
            chip.setChecked(key == special)
        self._swatch_fade.setOpacity(0.35 if special in self._chips else 1.0)
        self._style_chips()

    def refresh_styles(self):
        """Chips bake token colours; rebuild them after a theme change."""
        self._style_chips()
