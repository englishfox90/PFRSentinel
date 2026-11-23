"""Imaging Conditions card on the Image Processing page, beside the Alpaca
SafetyMonitor it complements.

Layout only: the live verdict the NINA plugin reads at GET /conditions and
the main thresholds of the ``imaging_conditions`` config block. Values are
pushed in by ``ui/controllers/imaging_conditions_controller.py``; edits go
out through ``changed``.
"""
from PySide6.QtCore import Signal
from qfluentwidgets import CaptionLabel, ComboBox, PushButton, StrongBodyLabel

from services.config_defaults import DEFAULT_CONFIG
from ..components.cards import CollapsibleCard, SwitchRow
from ..components.imaging_conditions_text import (
    STAR_INDEX_TOOLTIP, detail_lines, secondary_text, summarize_conditions)
from ..components.scroll_safe_spinbox import DoubleSpinBox, SpinBox
from ..theme.icons import mdi
from ..theme.tokens import Colors

_DEFAULTS = DEFAULT_CONFIG['imaging_conditions']

# (label, max_sun_alt_deg)
SUN_LIMITS = [
    ("Astronomical night (sun below -18°)", -18.0),
    ("Nautical twilight (sun below -12°)", -12.0),
]

_TONE_COLORS = {
    'ok': Colors.success_text,
    'warn': Colors.warning_text,
    'info': Colors.info_text,
    'muted': Colors.text_muted,
}


class ImagingConditionsCard(CollapsibleCard):
    changed = Signal()
    reset_baseline_requested = Signal()

    def __init__(self, parent=None):
        super().__init__("Imaging Conditions", mdi('telescope'), parent)
        caption = CaptionLabel(
            "Tells the NINA plugin whether the sky is good enough to image, so a "
            "sequence can pause under cloud and resume when it clears. Judged on "
            "every frame from the roof and sky readings, this rig's own clear-sky "
            "star index and the star sharpness. Served at /conditions with the "
            "control API token."
        )
        caption.setWordWrap(True)
        self.add_widget(caption)

        self.verdict_label = StrongBodyLabel("Off")
        self.add_widget(self.verdict_label)
        self.reasons_label = CaptionLabel("")
        self.reasons_label.setWordWrap(True)
        self.reasons_label.hide()
        self.add_widget(self.reasons_label)
        self.detail_label = CaptionLabel("")
        self.detail_label.setWordWrap(True)
        self.detail_label.setStyleSheet(f"color: {Colors.text_muted};")
        self.detail_label.setToolTip(STAR_INDEX_TOOLTIP)
        self.detail_label.hide()
        self.add_widget(self.detail_label)

        self.enabled_switch = SwitchRow("Evaluate imaging conditions",
                                        "Off: /conditions reports imaging_ok false, reason disabled")
        self.enabled_switch.toggled.connect(self._emit_changed)
        self.add_widget(self.enabled_switch)

        self.sun_combo = ComboBox()
        for label, alt in SUN_LIMITS:
            self.sun_combo.addItem(label, userData=alt)
        self.sun_combo.currentIndexChanged.connect(self._emit_changed)
        self.add_row("Image only in", self.sun_combo)

        self.cloud_spin = SpinBox()
        self.cloud_spin.setRange(0, 100)
        self.cloud_spin.setSuffix(" %")
        self.cloud_spin.valueChanged.connect(self._emit_changed)
        self.add_row("Max cloud cover", self.cloud_spin,
                     "Pause when the estimated cloud cover is above this")

        self.ratio_spin = DoubleSpinBox()
        self.ratio_spin.setRange(0.05, 1.0)
        self.ratio_spin.setSingleStep(0.05)
        self.ratio_spin.setDecimals(2)
        self.ratio_spin.valueChanged.connect(self._emit_changed)
        self.add_row("Min star ratio", self.ratio_spin,
                     "Pause below this fraction of this rig's clear-sky star index")

        self.bad_spin = SpinBox()
        self.bad_spin.setRange(1, 20)
        self.bad_spin.valueChanged.connect(self._emit_changed)
        self.add_row("Frames to pause", self.bad_spin, "Bad frames in a row before pausing")

        self.good_spin = SpinBox()
        self.good_spin.setRange(1, 20)
        self.good_spin.valueChanged.connect(self._emit_changed)
        self.add_row("Frames to resume", self.good_spin, "Good frames in a row before resuming")

        self.reset_button = PushButton("Reset baseline")
        self.reset_button.clicked.connect(self.reset_baseline_requested.emit)
        self.add_row("Clear-sky baseline", self.reset_button,
                     "Learned from this rig's own clear frames; reset after changing "
                     "the camera's view or exposure regime")

        self.load({})

    def _widgets(self):
        return (self, self.enabled_switch, self.enabled_switch.switch, self.sun_combo,
                self.cloud_spin, self.ratio_spin, self.bad_spin, self.good_spin)

    def load(self, cfg: dict) -> None:
        """Set every control from ``cfg`` without emitting ``changed``."""
        cfg = cfg if isinstance(cfg, dict) else {}
        previous = [w.blockSignals(True) for w in self._widgets()]
        try:
            self.enabled_switch.set_checked(bool(cfg.get('enabled', _DEFAULTS['enabled'])))
            alt = _number(cfg.get('max_sun_alt_deg'), _DEFAULTS['max_sun_alt_deg'])
            index = min(range(len(SUN_LIMITS)), key=lambda i: abs(SUN_LIMITS[i][1] - alt))
            self.sun_combo.setCurrentIndex(index)
            self.cloud_spin.setValue(int(round(_number(
                cfg.get('max_cloud_cover_pct'), _DEFAULTS['max_cloud_cover_pct']))))
            self.ratio_spin.setValue(_number(cfg.get('min_star_ratio'), _DEFAULTS['min_star_ratio']))
            self.bad_spin.setValue(int(_number(cfg.get('bad_frames'), _DEFAULTS['bad_frames'])))
            self.good_spin.setValue(int(_number(cfg.get('good_frames'), _DEFAULTS['good_frames'])))
        finally:
            for w, was in zip(self._widgets(), previous):
                w.blockSignals(was)

    def values(self) -> dict:
        return {
            'enabled': self.enabled_switch.is_checked(),
            'max_sun_alt_deg': SUN_LIMITS[max(0, self.sun_combo.currentIndex())][1],
            'max_cloud_cover_pct': float(self.cloud_spin.value()),
            'min_star_ratio': round(float(self.ratio_spin.value()), 2),
            'bad_frames': int(self.bad_spin.value()),
            'good_frames': int(self.good_spin.value()),
        }

    def show_conditions(self, snapshot: dict, capturing=None) -> None:
        text, tone = summarize_conditions(snapshot, capturing)
        self.verdict_label.setText(f"Imaging: {text}")
        self.verdict_label.setStyleSheet(f"color: {_TONE_COLORS.get(tone, Colors.text_primary)};")
        _set_optional(self.reasons_label,
                      secondary_text(snapshot, capturing, self.good_spin.value()))
        _set_optional(self.detail_label,
                      detail_lines(snapshot) if (snapshot or {}).get('enabled') else "")

    def _emit_changed(self, *_):
        self.changed.emit()


def _set_optional(label, text: str) -> None:
    label.setText(text)
    label.setVisible(bool(text))


def _number(value, default):
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return float(default)
    return number if number == number else float(default)
