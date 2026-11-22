"""Scheduled Capture card: always / only in a window / a different rate in a window.

Extracted from `_camera_settings_widget.py` (issue #43) to keep it under the size
cap; moved verbatim. Writes config through the owning settings widget, under its
`_can_save` guard.
"""
from PySide6.QtWidgets import QWidget, QHBoxLayout
from PySide6.QtCore import QTime
from qfluentwidgets import BodyLabel, CaptionLabel, ComboBox, TimePicker

from ..components.scroll_safe_spinbox import DoubleSpinBox
from ..theme.tokens import Colors, Spacing
from ..theme.icons import mdi
from ..components.cards import FormRow, CollapsibleCard
from ._schedule_window_source import ScheduleWindowSourceRows

# Scheduled-capture modes. Order matches the ComboBox.
_SCHEDULE_MODES = ["always", "gated", "variable"]
_SCHEDULE_MODE_LABELS = [
    "Always capture",
    "Only within time window",
    "Different rate within time window",
]
_SCHEDULE_MODE_HINTS = {
    "always": "Capture 24/7 at the interval set above.",
    "gated": "Capture only during the window below; pause (and disconnect the camera) outside it.",
    "variable": "Capture 24/7, but use the faster rate below while inside the window — useful for night timelapses.",
}


class ScheduledCaptureCard(CollapsibleCard):
    def __init__(self, owner, parent=None):
        super().__init__("Scheduled Capture", mdi('calendar-clock'), parent)
        self._owner = owner
        self._build()

    def _build(self):
        card = self

        # Mode selector
        self.schedule_mode_combo = ComboBox()
        self.schedule_mode_combo.addItems(_SCHEDULE_MODE_LABELS)
        self.schedule_mode_combo.setToolTip(
            "Always: capture 24/7. "
            "Only within window: pause outside the window. "
            "Different rate within window: switch rate inside vs outside (e.g. fast at night, slow by day)."
        )
        self.schedule_mode_combo.currentIndexChanged.connect(self._on_schedule_mode_changed)
        card.add_row("Mode", self.schedule_mode_combo)

        # Per-mode description line — updated by _on_schedule_mode_changed
        self.schedule_mode_hint = CaptionLabel(_SCHEDULE_MODE_HINTS["always"])
        self.schedule_mode_hint.setStyleSheet(f"color: {Colors.text_muted}; padding: 0 8px;")
        self.schedule_mode_hint.setWordWrap(True)
        card.add_widget(self.schedule_mode_hint)

        # Window-source rows (fixed vs. follow-timelapse) — shown for gated + variable
        self.schedule_source_rows = ScheduleWindowSourceRows()
        self.schedule_source_rows.source_changed.connect(self._on_schedule_source_changed)
        self.schedule_source_rows.margin_changed.connect(self._on_schedule_margin_changed)
        self.schedule_source_rows.hide()
        card.add_widget(self.schedule_source_rows)

        # Time window row (shown for gated + variable, fixed source only)
        self.schedule_time_widget = QWidget()
        time_row = QHBoxLayout(self.schedule_time_widget)
        time_row.setContentsMargins(0, 0, 0, 0)
        time_row.setSpacing(Spacing.md)

        time_label_start = BodyLabel("Window:")
        time_label_start.setStyleSheet(f"color: {Colors.text_secondary};")
        time_row.addWidget(time_label_start)

        self.schedule_start = TimePicker()
        self.schedule_start.setTime(QTime(17, 0))
        self.schedule_start.setToolTip("Window start time (24hr format)")
        self.schedule_start.timeChanged.connect(self._on_schedule_time_changed)
        time_row.addWidget(self.schedule_start)

        time_label_to = BodyLabel("to")
        time_label_to.setStyleSheet(f"color: {Colors.text_muted};")
        time_row.addWidget(time_label_to)

        self.schedule_end = TimePicker()
        self.schedule_end.setTime(QTime(9, 0))
        self.schedule_end.setToolTip("Window end time (24hr format; crossing midnight is supported)")
        self.schedule_end.timeChanged.connect(self._on_schedule_time_changed)
        time_row.addWidget(self.schedule_end)
        time_row.addStretch()

        self.schedule_time_widget.hide()
        card.add_widget(self.schedule_time_widget)

        # Window interval row (shown for variable only)
        self.schedule_window_interval_spin = DoubleSpinBox()
        self.schedule_window_interval_spin.setRange(0.1, 3600.0)
        self.schedule_window_interval_spin.setDecimals(1)
        self.schedule_window_interval_spin.setSuffix(" s")
        self.schedule_window_interval_spin.setValue(5.0)
        self.schedule_window_interval_spin.setToolTip(
            "Time between captures while inside the window. Outside the window, the "
            "Interval above is used."
        )
        self.schedule_window_interval_spin.valueChanged.connect(self._on_schedule_window_interval_changed)
        self.schedule_window_row = FormRow(
            "In-window interval",
            self.schedule_window_interval_spin,
            "Overrides the Interval above while inside the window."
        )
        self.schedule_window_row.hide()
        card.add_widget(self.schedule_window_row)

    def _update_schedule_window_visibility(self, mode: str):
        """Shared visibility rule for the window rows, keyed by mode + source."""
        uses_window = mode in ("gated", "variable")
        self.schedule_source_rows.setVisible(uses_window)
        self.schedule_time_widget.setVisible(uses_window and not self.schedule_source_rows.uses_timelapse())

    def _on_schedule_mode_changed(self, index):
        mode = _SCHEDULE_MODES[index] if 0 <= index < len(_SCHEDULE_MODES) else "always"
        self.schedule_mode_hint.setText(_SCHEDULE_MODE_HINTS.get(mode, ""))
        self._update_schedule_window_visibility(mode)
        # In-window interval is only meaningful in variable mode.
        self.schedule_window_row.setVisible(mode == "variable")
        if self._owner._can_save:
            self._owner.main_window.config.set('scheduled_capture_mode', mode)
            # Keep legacy flag in sync so any external tooling still reading it
            # (migrations, pre-update installs) sees a sensible value.
            self._owner.main_window.config.set('scheduled_capture_enabled', mode != "always")
            self._owner.settings_changed.emit()

    def _on_schedule_time_changed(self):
        if not self._owner._can_save:
            return
        start_time = self.schedule_start.getTime()
        end_time = self.schedule_end.getTime()
        self._owner.main_window.config.set('scheduled_start_time', start_time.toString('HH:mm'))
        self._owner.main_window.config.set('scheduled_end_time', end_time.toString('HH:mm'))
        self._owner.settings_changed.emit()

    def _on_schedule_window_interval_changed(self, value):
        if self._owner._can_save:
            self._owner.main_window.config.set('scheduled_window_interval', value)
            self._owner.settings_changed.emit()

    def _on_schedule_source_changed(self, source):
        mode = _SCHEDULE_MODES[self.schedule_mode_combo.currentIndex()]
        self._update_schedule_window_visibility(mode)
        if self._owner._can_save:
            self._owner.main_window.config.set('scheduled_window_source', source)
            self.schedule_source_rows.refresh_preview(self._owner.main_window.config)
            self._owner.settings_changed.emit()

    def _on_schedule_margin_changed(self, value):
        if self._owner._can_save:
            self._owner.main_window.config.set('scheduled_window_margin_min', value)
            self.schedule_source_rows.refresh_preview(self._owner.main_window.config)
            self._owner.settings_changed.emit()

    def load(self, config):
        # Derive the scheduled-capture mode, accepting legacy configs that
        # only stored the boolean flag.
        mode = config.get('scheduled_capture_mode')
        if mode not in _SCHEDULE_MODES:
            mode = 'gated' if config.get('scheduled_capture_enabled', False) else 'always'
        self.schedule_mode_combo.setCurrentIndex(_SCHEDULE_MODES.index(mode))
        self.schedule_mode_hint.setText(_SCHEDULE_MODE_HINTS.get(mode, ""))

        start_h, start_m = map(int, config.get('scheduled_start_time', '17:00').split(':'))
        end_h, end_m = map(int, config.get('scheduled_end_time', '09:00').split(':'))
        self.schedule_start.setTime(QTime(start_h, start_m))
        self.schedule_end.setTime(QTime(end_h, end_m))
        self.schedule_window_interval_spin.setValue(config.get('scheduled_window_interval', 5.0))

        self.schedule_source_rows.load(config)
        self._update_schedule_window_visibility(mode)
        self.schedule_window_row.setVisible(mode == "variable")
