"""Window-source rows for the Scheduled Capture card.

Lets the capture window follow either fixed HH:MM times or the Timelapse
recording window (widened by a margin). Layout only — the caption text comes
from ``services.capture_schedule_window.describe_capture_window``, which owns
all the actual schedule maths.
"""
from PySide6.QtWidgets import QWidget, QVBoxLayout
from PySide6.QtCore import Signal
from qfluentwidgets import CaptionLabel, ComboBox, SpinBox

from ..theme.tokens import Colors, Spacing
from ..components.cards import FormRow
from services.capture_schedule_window import describe_capture_window

# Window-source options. Order matches the ComboBox.
_WINDOW_SOURCES = ["fixed", "timelapse"]
_WINDOW_SOURCE_LABELS = ["Fixed times", "Same as Timelapse"]

_TIMELAPSE_EXPLANATION = (
    "Follows the Timelapse recording window, widened by the margin, so the "
    "camera is running before recording starts. Location comes from the "
    "weather settings."
)


class ScheduleWindowSourceRows(QWidget):
    """Window-source combo + margin spin + live preview, for the schedule card."""

    source_changed = Signal(str)
    margin_changed = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._loading = False
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(Spacing.input_gap)

        self.source_combo = ComboBox()
        self.source_combo.addItems(_WINDOW_SOURCE_LABELS)
        self.source_combo.setToolTip(
            "Fixed times: use the window below. "
            "Same as Timelapse: follow the Timelapse recording window instead."
        )
        self.source_combo.currentIndexChanged.connect(self._on_source_changed)
        layout.addWidget(FormRow("Window source", self.source_combo))

        self.margin_spin = SpinBox()
        self.margin_spin.setRange(0, 180)
        self.margin_spin.setValue(15)
        self.margin_spin.setSuffix(" min")
        self.margin_spin.setToolTip(
            "Widens the timelapse window at both ends, so the camera has time "
            "to reconnect and settle auto-exposure before recording starts."
        )
        self.margin_spin.valueChanged.connect(self._on_margin_changed)
        self.margin_row = FormRow("Margin", self.margin_spin)
        layout.addWidget(self.margin_row)

        self.preview_label = CaptionLabel("")
        self.preview_label.setStyleSheet(f"color: {Colors.text_muted}; padding: 0 8px;")
        self.preview_label.setWordWrap(True)
        layout.addWidget(self.preview_label)

        self.margin_row.hide()
        self.preview_label.hide()

    def _on_source_changed(self, index):
        source = _WINDOW_SOURCES[index] if 0 <= index < len(_WINDOW_SOURCES) else "fixed"
        is_timelapse = source == "timelapse"
        self.margin_row.setVisible(is_timelapse)
        self.preview_label.setVisible(is_timelapse)
        if not self._loading:
            self.source_changed.emit(source)

    def _on_margin_changed(self, value):
        if not self._loading:
            self.margin_changed.emit(value)

    def load(self, config):
        self._loading = True
        try:
            source = config.get('scheduled_window_source', 'fixed')
            if source not in _WINDOW_SOURCES:
                source = 'fixed'
            self.source_combo.setCurrentIndex(_WINDOW_SOURCES.index(source))
            self.margin_spin.setValue(config.get('scheduled_window_margin_min', 15))
            is_timelapse = source == 'timelapse'
            self.margin_row.setVisible(is_timelapse)
            self.preview_label.setVisible(is_timelapse)
            self.refresh_preview(config)
        finally:
            self._loading = False

    def refresh_preview(self, config):
        if not self.uses_timelapse():
            return
        label = describe_capture_window(config)
        self.preview_label.setText(f"{_TIMELAPSE_EXPLANATION}\nTonight: {label}")

    def source(self) -> str:
        index = self.source_combo.currentIndex()
        return _WINDOW_SOURCES[index] if 0 <= index < len(_WINDOW_SOURCES) else "fixed"

    def set_source(self, source: str):
        if source in _WINDOW_SOURCES:
            self.source_combo.setCurrentIndex(_WINDOW_SOURCES.index(source))

    def uses_timelapse(self) -> bool:
        return self.source() == "timelapse"
