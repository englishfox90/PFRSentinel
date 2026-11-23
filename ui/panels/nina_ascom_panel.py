"""NINA & ASCOM page: everything Sentinel tells NINA, in one place.

Cards, in setup order: the NINA plugin (Windows only, as before), the
control API token it pairs with, Imaging Conditions (open by default — the
card people come back to) and the roof SafetyMonitor / safety file.

Layout only. ``ui/controllers/nina_ascom_controller.py`` saves the control
API and roof-safety edits, ``imaging_conditions_controller.py`` the Imaging
Conditions card, and the window's NINA-plugin controller fills the plugin
card through ``NinaPluginCardMixin``.
"""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QScrollArea, QVBoxLayout, QWidget
from qfluentwidgets import CaptionLabel

from services.host_platform import IS_WINDOWS
from ..theme.tokens import Colors, Spacing
from ._nina_plugin_card import NinaPluginCardMixin
from .control_api_card import ControlApiCard
from .imaging_conditions_card import ImagingConditionsCard
from .roof_safety_card import RoofSafetyCard


class NinaAscomPanel(NinaPluginCardMixin, QScrollArea):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.main_window = parent
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setStyleSheet(f"QScrollArea {{ background-color: {Colors.bg_app}; border: none; }}")
        content = QWidget()
        self.setWidget(content)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(Spacing.base, Spacing.base, Spacing.base, Spacing.base)
        layout.setSpacing(Spacing.card_gap)

        intro = CaptionLabel(
            "How Sentinel works with NINA: the plugin, the token it connects with, "
            "when the sky is good enough to image, and whether the roof is safe.")
        intro.setWordWrap(True)
        intro.setStyleSheet(f"color: {Colors.text_secondary}; padding: 0 4px;")
        layout.addWidget(intro)

        # NINA itself is Windows-only (the plugin DLL and its installer both
        # target the Windows PE format), so the card is skipped elsewhere.
        if IS_WINDOWS:
            self._build_nina_plugin_card(layout)

        self.control_card = ControlApiCard()
        layout.addWidget(self.control_card)

        self.conditions_card = ImagingConditionsCard()
        self.conditions_card._toggle_expanded(None)
        layout.addWidget(self.conditions_card)

        self.safety_card = RoofSafetyCard()
        layout.addWidget(self.safety_card)
        layout.addStretch(1)

    def load_from_config(self, config) -> None:
        self.control_card.load(config.get('output', {}) or {})
        self.conditions_card.load(config.get('imaging_conditions', {}) or {})
        self.safety_card.load(config.get('ml_models', {}) or {})
