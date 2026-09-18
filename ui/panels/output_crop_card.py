"""
Output framing card — Processing page UI for the output-stage crop (issue #12).

Layout and config plumbing only. The thumbnail and the Fit-to-sky measurement
come from ui/controllers/output_crop_controller.py through signals.
"""
from PySide6.QtWidgets import QWidget, QHBoxLayout, QVBoxLayout
from PySide6.QtCore import Qt, Signal
from qfluentwidgets import BodyLabel, CaptionLabel, PushButton, SwitchButton

from ..components.scroll_safe_spinbox import SpinBox

from ..theme.tokens import Colors, Spacing
from ..theme.icons import mdi
from ..components.cards import CollapsibleCard, SwitchRow
from .crop_box_editor import CropBoxEditor
from services.output_crop import DEFAULT_OUTPUT_CROP, centred_box, describe, normalise_box

_DEFAULT_FRACTION = 0.8   # first-enable box: 80% of the frame, centred


class OutputCropCard(CollapsibleCard):
    settings_changed = Signal()
    fit_requested = Signal()
    activity_changed = Signal(bool)

    def __init__(self, main_window, parent=None):
        super().__init__("Output Framing", mdi('crop'), parent)
        self.main_window = main_window
        self._loading = False
        self._build()

    # ---- layout ------------------------------------------------------------

    def _build(self):
        self.enable_switch = SwitchRow(
            "Crop outputs to a region",
            "Saved files, the web frame, Discord posts and the timelapse are cut to the box "
            "below. Auto-exposure, auto-stretch, ML and all-sky calibration keep using the "
            "full frame, so the dark corners still anchor them.",
        )
        self.enable_switch.toggled.connect(self._on_enabled_toggled)
        self.add_widget(self.enable_switch)

        self.editor = CropBoxEditor()
        self.editor.box_changed.connect(self._on_box_dragged)
        self.editor.box_committed.connect(self._on_box_committed)
        self.editor.visibility_changed.connect(self.activity_changed)
        self.add_widget(self.editor)

        hint = CaptionLabel("Drag inside the box to move it, drag a corner to resize. "
                            "Values are pixels of the full frame.")
        hint.setStyleSheet(f"color: {Colors.text_muted};")
        hint.setWordWrap(True)
        self.add_widget(hint)

        self.add_widget(self._build_numeric_row())
        self.add_widget(self._build_button_row())

        self.status_label = CaptionLabel("")
        self.status_label.setStyleSheet(f"color: {Colors.text_secondary};")
        self.status_label.setWordWrap(True)
        self.add_widget(self.status_label)

    def _spin(self, tooltip: str) -> SpinBox:
        spin = SpinBox()
        spin.setRange(0, 0)
        spin.setSingleStep(2)
        spin.setToolTip(tooltip)
        spin.setFixedWidth(120)
        spin.valueChanged.connect(self._on_spin_changed)
        return spin

    def _build_numeric_row(self) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(Spacing.sm)
        self.x_spin = self._spin("Left edge of the box (px)")
        self.y_spin = self._spin("Top edge of the box (px)")
        self.w_spin = self._spin("Box width (px, even)")
        self.h_spin = self._spin("Box height (px, even)")
        for text, spin in (("X", self.x_spin), ("Y", self.y_spin),
                           ("W", self.w_spin), ("H", self.h_spin)):
            label = BodyLabel(text)
            label.setStyleSheet(f"color: {Colors.text_secondary};")
            layout.addWidget(label)
            layout.addWidget(spin)
        layout.addSpacing(Spacing.md)
        square_label = BodyLabel("Keep square")
        square_label.setStyleSheet(f"color: {Colors.text_secondary};")
        layout.addWidget(square_label)
        self.square_switch = SwitchButton()
        self.square_switch.setChecked(True)
        self.square_switch.setToolTip("Lock the box to a square — the natural shape for an all-sky disc")
        self.square_switch.checkedChanged.connect(self._on_square_toggled)
        layout.addWidget(self.square_switch)
        layout.addStretch()
        return row

    def _build_button_row(self) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(Spacing.sm)
        self.fit_btn = PushButton("Fit to sky")
        self.fit_btn.setIcon(mdi('weather-night'))
        self.fit_btn.setToolTip("Measure the sky circle in the last frame and fit a square around it")
        self.fit_btn.clicked.connect(self._on_fit_clicked)
        self.centre_btn = PushButton("Centre")
        self.centre_btn.setIcon(mdi('image-filter-center-focus'))
        self.centre_btn.setToolTip("Move the box to the middle of the frame")
        self.centre_btn.clicked.connect(self._on_centre_clicked)
        self.reset_btn = PushButton("Full frame")
        self.reset_btn.setIcon(mdi('fullscreen'))
        self.reset_btn.setToolTip("Reset the box to the whole frame")
        self.reset_btn.clicked.connect(self._on_reset_clicked)
        for btn in (self.fit_btn, self.centre_btn, self.reset_btn):
            layout.addWidget(btn)
        layout.addStretch()
        return row

    # ---- inputs from the controller --------------------------------------

    def set_frame(self, pil_thumb, ref_w: int, ref_h: int):
        had_reference = self.editor.has_reference()
        self.editor.set_frame(pil_thumb, ref_w, ref_h)
        self._sync_ranges()
        if not had_reference and self.editor.box()[2] <= 0:
            self._set_box(*self._default_box(), save=False)
        self._sync_spins()
        self._refresh_status()

    def apply_box(self, x, y, w, h):
        """A Fit-to-sky result: adopt it, enable the crop, save."""
        self.fit_btn.setEnabled(True)
        self.fit_btn.setText("Fit to sky")
        if not self.enable_switch.is_checked():
            self.enable_switch.set_checked(True)   # its handler saves
        self._set_box(x, y, w, h, save=True)

    def show_fit_error(self, message: str):
        self.fit_btn.setEnabled(True)
        self.fit_btn.setText("Fit to sky")
        self.status_label.setText(message)
        self.status_label.setStyleSheet(f"color: {Colors.warning_text};")

    # ---- handlers ------------------------------------------------------------

    @property
    def _can_save(self):
        return not self._loading and self.main_window is not None and hasattr(self.main_window, 'config')

    def _default_box(self):
        ref_w, ref_h = self.editor.reference()
        return centred_box(ref_w, ref_h, ref_w * _DEFAULT_FRACTION, ref_h * _DEFAULT_FRACTION,
                           keep_square=self.square_switch.isChecked())

    def _on_enabled_toggled(self, checked: bool):
        self.editor.set_active(checked)
        if checked and self.editor.has_reference() and self.editor.box()[2] <= 0:
            self._set_box(*self._default_box(), save=False)
        self._save()

    def _on_box_dragged(self, x, y, w, h):
        self._sync_spins()
        self._refresh_status()

    def _on_box_committed(self, x, y, w, h):
        self._sync_spins()
        self._save()

    def _on_spin_changed(self, _value):
        if self._loading or not self.editor.has_reference():
            return
        self.editor.set_box(self.x_spin.value(), self.y_spin.value(),
                            self.w_spin.value(), self.h_spin.value())
        self._sync_spins()
        self._save()

    def _on_square_toggled(self, checked: bool):
        self.editor.set_keep_square(checked)
        if not self._loading:
            self._sync_spins()
            self._save()

    def _on_fit_clicked(self):
        self.fit_btn.setEnabled(False)
        self.fit_btn.setText("Measuring…")
        self.fit_requested.emit()

    def _on_centre_clicked(self):
        if not self.editor.has_reference():
            return
        ref_w, ref_h = self.editor.reference()
        _x, _y, w, h = self.editor.box()
        if w <= 0:
            w, h = self._default_box()[2:]
        self._set_box(*centred_box(ref_w, ref_h, w, h), save=True)

    def _on_reset_clicked(self):
        if not self.editor.has_reference():
            return
        self.editor.set_full_frame()
        self._sync_spins()
        self._save()

    # ---- helpers ---------------------------------------------------------------

    def _set_box(self, x, y, w, h, save: bool):
        self.editor.set_box(x, y, w, h)
        self._sync_spins()
        if save:
            self._save()

    def _sync_ranges(self):
        ref_w, ref_h = self.editor.reference()
        usable = self.editor.has_reference()
        for spin in (self.x_spin, self.y_spin, self.w_spin, self.h_spin):
            spin.setEnabled(usable)
        # setRange() clamps an out-of-range value and emits valueChanged, which
        # would re-enter _on_spin_changed with the ranges half updated.
        was_loading, self._loading = self._loading, True
        try:
            self.x_spin.setRange(0, max(0, ref_w))
            self.y_spin.setRange(0, max(0, ref_h))
            self.w_spin.setRange(0, max(0, ref_w))
            self.h_spin.setRange(0, max(0, ref_h))
        finally:
            self._loading = was_loading

    def _sync_spins(self):
        x, y, w, h = self.editor.box()
        was_loading, self._loading = self._loading, True
        try:
            self.x_spin.setValue(x)
            self.y_spin.setValue(y)
            self.w_spin.setValue(w)
            self.h_spin.setValue(h)
        finally:
            self._loading = was_loading
        self._refresh_status()

    def _current_config(self) -> dict:
        ref_w, ref_h = self.editor.reference()
        x, y, w, h = self.editor.box()
        return {
            'enabled': self.enable_switch.is_checked(),
            'x': int(x), 'y': int(y), 'width': int(w), 'height': int(h),
            'ref_width': int(ref_w), 'ref_height': int(ref_h),
            'keep_square': bool(self.square_switch.isChecked()),
        }

    def _save(self):
        self._refresh_status()
        if not self._can_save:
            return
        self.main_window.config.set('output_crop', self._current_config())
        self.settings_changed.emit()

    def _refresh_status(self):
        cfg = self._current_config()
        ref_w, ref_h = self.editor.reference()
        if not self.editor.has_reference():
            text = "No reference frame yet — start capture and the last frame will appear above."
        elif not cfg['enabled']:
            text = f"Crop off — outputs use the full {ref_w} × {ref_h} frame."
        else:
            text = f"Outputs: {describe(cfg)} of the {ref_w} × {ref_h} frame."
        self.status_label.setText(text)
        self.status_label.setStyleSheet(f"color: {Colors.text_secondary};")

    # ---- config ----------------------------------------------------------------

    def load_from_config(self, config):
        self._loading = True
        try:
            cfg = dict(DEFAULT_OUTPUT_CROP)
            cfg.update(config.get('output_crop', {}) or {})
            keep_square = bool(cfg.get('keep_square', True))
            self.square_switch.setChecked(keep_square)
            self.editor.set_keep_square(keep_square)
            ref_w, ref_h = int(cfg.get('ref_width', 0) or 0), int(cfg.get('ref_height', 0) or 0)
            if ref_w > 0 and ref_h > 0 and not self.editor.has_reference():
                self.editor.set_reference(ref_w, ref_h)
            self._sync_ranges()
            if self.editor.has_reference() and int(cfg.get('width', 0) or 0) > 0:
                rw, rh = self.editor.reference()
                box = normalise_box(cfg['x'], cfg['y'], cfg['width'], cfg['height'], rw, rh, keep_square)
                self.editor.set_box(*box)
            enabled = bool(cfg.get('enabled', False))
            self.enable_switch.set_checked(enabled)
            self.editor.set_active(enabled)
            self._sync_spins()
        finally:
            self._loading = False
        self._refresh_status()
