"""Roof Safety card on the NINA & ASCOM page: the two ways Sentinel tells NINA
whether the roof is safe — the ASCOM Alpaca SafetyMonitor it serves, and the
GenericFile safety file it writes. Moved from the ML Models card on Image
Processing; the config keys are unchanged (``ml_models.alpaca_safety_monitor``
and ``ml_models.ascom_safety_file``).

Layout only: edits leave through the signals and
``ui/controllers/nina_ascom_controller.py`` writes them to config.
"""
import os

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QFileDialog, QHBoxLayout, QWidget
from qfluentwidgets import CaptionLabel, ComboBox, LineEdit, PushButton

from services.alpaca_safety.verdict_source import IMAGING_CONDITIONS, ROOF, read_source
from services.utils_paths import get_app_data_dir
from ..components.cards import CollapsibleCard, SwitchRow
from ..theme.icons import mdi
from ..theme.tokens import Colors, Spacing


def _caption(text: str = "", muted: bool = True) -> CaptionLabel:
    label = CaptionLabel(text)
    label.setWordWrap(True)
    label.setStyleSheet(f"color: {Colors.text_muted if muted else Colors.warning_text};")
    return label


class RoofSafetyCard(CollapsibleCard):
    alpaca_toggled = Signal(bool)
    alpaca_source_changed = Signal(str)
    safety_file_toggled = Signal(bool)
    safety_file_path_changed = Signal(str)

    def __init__(self, parent=None):
        super().__init__("Roof Safety Monitor", mdi('home-roof'), parent)
        intro = _caption(
            "Tells NINA whether it is safe, from the roof classifier (or, for the Alpaca "
            "SafetyMonitor, from Imaging Conditions), so NINA's safety checks can stop "
            "a sequence.")
        intro.setStyleSheet(f"color: {Colors.text_secondary};")
        self.add_widget(intro)
        self.ml_off_label = _caption(
            "Needs ML Models — switch them on in Image Processing → ML Models. "
            "Until then both read unsafe.", muted=False)
        self.ml_off_label.hide()
        self.add_widget(self.ml_off_label)

        self.alpaca_safety_switch = SwitchRow(
            "Alpaca SafetyMonitor",
            "Serve roof safety as an ASCOM Alpaca SafetyMonitor (NINA, INDIGO A1, "
            "any Alpaca client)")
        self.alpaca_safety_switch.toggled.connect(self.alpaca_toggled.emit)
        self.add_widget(self.alpaca_safety_switch)
        self.alpaca_source_combo = ComboBox()
        self.alpaca_source_combo.addItem("Roof is open", userData=ROOF)
        self.alpaca_source_combo.addItem("Imaging conditions are OK", userData=IMAGING_CONDITIONS)
        self.alpaca_source_combo.currentIndexChanged.connect(self._on_source_picked)
        self.add_row("Safe when", self.alpaca_source_combo,
                     "Imaging conditions also read unsafe under cloud, so NINA's safety "
                     "triggers act on the sky as well as the roof")
        self.alpaca_safety_info = _caption()
        self.add_widget(self.alpaca_safety_info)

        self.ascom_safety_switch = SwitchRow(
            "ASCOM safety file",
            "Write the roof state to a file for NINA's GenericFile safety monitor")
        self.ascom_safety_switch.toggled.connect(self.safety_file_toggled.emit)
        self.add_widget(self.ascom_safety_switch)

        path_row = QHBoxLayout()
        path_row.setContentsMargins(0, 0, 0, 0)
        path_row.setSpacing(Spacing.sm)
        self.ascom_file_path = LineEdit()
        self.ascom_file_path.setPlaceholderText(
            os.path.join(get_app_data_dir(), 'RoofStatusFile.txt'))
        self.ascom_file_path.editingFinished.connect(
            lambda: self.safety_file_path_changed.emit(self.ascom_file_path.text().strip()))
        path_row.addWidget(self.ascom_file_path, 1)
        self.ascom_browse_btn = PushButton("Browse")
        self.ascom_browse_btn.clicked.connect(self._browse)
        path_row.addWidget(self.ascom_browse_btn)
        path_widget = QWidget()
        path_widget.setLayout(path_row)
        self.add_row("File path", path_widget, "The file NINA watches")
        self.add_widget(_caption(
            "In NINA's GenericFile safety monitor set: preamble \"Roof Status:\", "
            "safe trigger \"OPEN\", unsafe trigger \"CLOSED\"."))

    def load(self, ml_config: dict) -> None:
        """Set the controls from the ``ml_models`` block without emitting."""
        ml_config = ml_config if isinstance(ml_config, dict) else {}
        widgets = (self.alpaca_safety_switch, self.alpaca_safety_switch.switch,
                   self.alpaca_source_combo,
                   self.ascom_safety_switch, self.ascom_safety_switch.switch,
                   self.ascom_file_path)
        previous = [w.blockSignals(True) for w in widgets]
        try:
            alpaca = ml_config.get('alpaca_safety_monitor', {}) or {}
            self.alpaca_safety_switch.set_checked(bool(alpaca.get('enabled', False)))
            self._select_source(read_source(alpaca))
            ascom = ml_config.get('ascom_safety_file', {}) or {}
            self.ascom_safety_switch.set_checked(bool(ascom.get('enabled', False)))
            self.ascom_file_path.setText(ascom.get('file_path', '') or '')
        finally:
            for w, was in zip(widgets, previous):
                w.blockSignals(was)
        self.show_alpaca_info(alpaca)
        self.ml_off_label.setVisible(not ml_config.get('enabled', False))

    def show_alpaca_info(self, alpaca_config: dict) -> None:
        alpaca_config = alpaca_config or {}
        address = f"{alpaca_config.get('bind', '127.0.0.1')}:{alpaca_config.get('port', 11111)}"
        prefix = (f"Listening on {address}. Add \"PFR Sentinel Roof\" as an Alpaca "
                  "SafetyMonitor in NINA. ")
        if read_source(alpaca_config) == IMAGING_CONDITIONS:
            self.alpaca_safety_info.setText(
                prefix + "Safe only while Imaging Conditions reads OK (below); unsafe "
                "when it pauses, is switched off, or has had no recent frame.")
            return
        minutes = int(alpaca_config.get('stale_after_seconds', 600) or 600) // 60
        self.alpaca_safety_info.setText(
            prefix + "Safe only after two confident Open frames; unsafe "
            f"when Closed, uncertain, or when no frame has arrived for {minutes} min.")

    def alpaca_source(self) -> str:
        return self.alpaca_source_combo.currentData() or ROOF

    def _select_source(self, source: str) -> None:
        index = self.alpaca_source_combo.findData(source)
        self.alpaca_source_combo.setCurrentIndex(index if index >= 0 else 0)

    def _on_source_picked(self, _index: int) -> None:
        self.alpaca_source_changed.emit(self.alpaca_source())

    def _browse(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Select ASCOM Safety File Location", self.ascom_file_path.text() or "",
            "Text Files (*.txt);;All Files (*.*)")
        if path:
            self.ascom_file_path.setText(path)
            self.safety_file_path_changed.emit(path.strip())
