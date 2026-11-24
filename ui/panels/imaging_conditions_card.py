"""Imaging Conditions card on the NINA & ASCOM page.

Layout only: the current verdict (with a link to the live view on Live
Monitoring), the on/off switch, the sun limit, and the Sensitivity preset —
Relaxed / Balanced / Strict — with an Advanced section showing the exact
numbers. Editing any number shows the preset as Custom; picking a preset
rewrites them. Values are pushed in by
``ui/controllers/imaging_conditions_controller.py``; edits go out through
``changed``. The preset table and the cloud / star-floor coupling live in
``services/imaging_conditions/presets.py``.
"""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import (CaptionLabel, ComboBox, PushButton, StrongBodyLabel,
                            TransparentPushButton)

from services.config_defaults import DEFAULT_CONFIG
from services.imaging_conditions.presets import (
    CUSTOM, PRESET_KEYS, PRESET_LABELS, PRESET_ORDER, SENSITIVITY_KEY, preset_for,
    preset_values, star_floor_for)
from ..components.cards import CollapsibleCard, FormRow, SwitchRow
from ..components.conditions_live_text import FWHM_HELP, STAR_COUNT_HELP
from ..components.imaging_conditions_text import secondary_text, summarize_conditions
from ..components.target_moon_text import CLOSE_HINT, target_moon_text
from ..components.scroll_safe_spinbox import SpinBox
from ..theme.icons import mdi
from ..theme.tokens import Colors, Spacing

_DEFAULTS = DEFAULT_CONFIG['imaging_conditions']

# (label, max_sun_alt_deg)
SUN_LIMITS = [
    ("Astronomical night (sun below -18°)", -18.0),
    ("Nautical twilight (sun below -12°)", -12.0),
]

SENSITIVITY_ITEMS = [*PRESET_ORDER, CUSTOM]

PRESET_BLURB = {
    'relaxed': "Rides out passing cloud.",
    'balanced': "The default.",
    'strict': "Protects every sub-exposure.",
    CUSTOM: "Your own numbers, below in Advanced.",
}

_TONE_COLORS = {
    'ok': Colors.success_text,
    'warn': Colors.warning_text,
    'info': Colors.info_text,
    'muted': Colors.text_muted,
}


def sensitivity_text(name: str, values: dict) -> str:
    """One line on what the chosen sensitivity does, from its numbers."""
    blurb = PRESET_BLURB.get(name, "")
    return (f"{blurb} Pauses after {int(values['bad_frames'])} bad frames in a row "
            f"(above {values['max_cloud_cover_pct']:.0f}% cloud, or stars fading "
            f"{values['star_drop_pct']:.0f}%); resumes after "
            f"{int(values['good_frames'])} good frames.").strip()


def _spin(lo, hi, suffix=""):
    spin = SpinBox()
    spin.setRange(lo, hi)
    if suffix:
        spin.setSuffix(suffix)
    return spin


class ImagingConditionsCard(CollapsibleCard):
    changed = Signal()
    reset_baseline_requested = Signal()
    live_view_requested = Signal()

    def __init__(self, parent=None):
        super().__init__("Imaging Conditions", mdi('telescope'), parent)
        self._star_ratio = float(_DEFAULTS['min_star_ratio'])
        caption = CaptionLabel(
            "Tells NINA whether the sky is good enough to image, so a sequence "
            "pauses under cloud and resumes when it clears. Judged on every frame "
            "from the roof and sky readings, how many stars show compared with "
            "this camera's clear sky, and how sharp they are.")
        caption.setWordWrap(True)
        caption.setStyleSheet(f"color: {Colors.text_secondary};")
        self.add_widget(caption)

        status_row = QHBoxLayout()
        status_row.setContentsMargins(0, 0, 0, 0)
        status_row.setSpacing(Spacing.sm)
        text_col = QVBoxLayout()
        text_col.setSpacing(2)
        self.verdict_label = StrongBodyLabel("Off")
        text_col.addWidget(self.verdict_label)
        self.reasons_label = CaptionLabel("")
        self.reasons_label.setWordWrap(True)
        self.reasons_label.hide()
        text_col.addWidget(self.reasons_label)
        self.target_label = CaptionLabel("")
        self.target_label.setWordWrap(True)
        self.target_label.hide()
        text_col.addWidget(self.target_label)
        status_row.addLayout(text_col, 1)
        self.live_button = PushButton(mdi('chart-line'), "Live view")
        self.live_button.setToolTip("Open the conditions panel on Live Monitoring")
        self.live_button.clicked.connect(self.live_view_requested.emit)
        status_row.addWidget(self.live_button, 0, Qt.AlignTop)
        status = QWidget()
        status.setLayout(status_row)
        self.add_widget(status)

        self.enabled_switch = SwitchRow(
            "Judge imaging conditions",
            "Off: NINA is always told not to image (reason: disabled)")
        self.enabled_switch.toggled.connect(self._emit_changed)
        self.add_widget(self.enabled_switch)

        self.sun_combo = ComboBox()
        for label, alt in SUN_LIMITS:
            self.sun_combo.addItem(label, userData=alt)
        self.sun_combo.currentIndexChanged.connect(self._emit_changed)
        self.add_row("Image only in", self.sun_combo)

        self.sensitivity_combo = ComboBox()
        for name in SENSITIVITY_ITEMS:
            self.sensitivity_combo.addItem(PRESET_LABELS[name], userData=name)
        self.sensitivity_combo.currentIndexChanged.connect(self._on_preset_picked)
        sens_row = self.add_row("Sensitivity", self.sensitivity_combo)
        self.sensitivity_hint = CaptionLabel("")
        self.sensitivity_hint.setWordWrap(True)
        self.sensitivity_hint.setStyleSheet(f"color: {Colors.text_muted};")
        sens_row.layout().itemAt(1).layout().addWidget(self.sensitivity_hint)

        self.advanced_button = TransparentPushButton(mdi('chevron-right'), "Advanced — the exact numbers")
        self.advanced_button.clicked.connect(lambda: self.set_advanced_open(
            not self.advanced_box.isVisible()))
        # Lined up with the input column of the rows above (FormRow: 140 px
        # label + 12 px gap).
        advanced_row = QHBoxLayout()
        advanced_row.setContentsMargins(140 + Spacing.md - 12, 0, 0, 0)
        advanced_row.addWidget(self.advanced_button)
        advanced_row.addStretch(1)
        advanced_holder = QWidget()
        advanced_holder.setLayout(advanced_row)
        self.add_widget(advanced_holder)
        self._build_advanced()

        self.reset_button = PushButton("Reset baseline")
        self.reset_button.clicked.connect(self.reset_baseline_requested.emit)
        self.add_row("Clear-sky baseline", self.reset_button,
                     "Learned from this camera's own clear nights. Reset after "
                     "changing what the camera sees or its exposure.")

        self.load({})

    def _build_advanced(self):
        self.advanced_box = QWidget()
        lay = QVBoxLayout(self.advanced_box)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(Spacing.input_gap)

        self.cloud_spin = _spin(0, 100, " %")
        self.cloud_spin.setToolTip(STAR_COUNT_HELP)
        cloud_row = FormRow("Cloud limit", self.cloud_spin,
                            "Pause above this much cloud — read from how many of "
                            "your clear-sky stars are missing")
        lay.addWidget(cloud_row)
        self.star_floor_label = CaptionLabel("")
        self.star_floor_label.setWordWrap(True)
        self.star_floor_label.setStyleSheet(f"color: {Colors.text_muted};")
        cloud_row.layout().itemAt(1).layout().addWidget(self.star_floor_label)

        self.drop_spin = _spin(5, 100, " %")
        lay.addWidget(FormRow("Stars fading", self.drop_spin,
                              "Pause when the star count falls this far below the "
                              "last few frames"))
        self.fwhm_spin = _spin(5, 400, " %")
        self.fwhm_spin.setToolTip(FWHM_HELP)
        lay.addWidget(FormRow("Stars softer", self.fwhm_spin,
                              "Pause when stars are this much wider (FWHM) than "
                              "tonight's usual"))
        self.bad_spin = _spin(1, 20)
        lay.addWidget(FormRow("Pause after", self.bad_spin,
                              "Bad frames in a row before pausing"))
        self.good_spin = _spin(1, 20)
        lay.addWidget(FormRow("Resume after", self.good_spin,
                              "Good frames in a row before resuming"))
        for spin in self._number_spins():
            spin.valueChanged.connect(self._on_number_edited)
        self.advanced_box.hide()
        self.add_widget(self.advanced_box)

    def _number_spins(self):
        return (self.cloud_spin, self.drop_spin, self.fwhm_spin, self.bad_spin, self.good_spin)

    def _widgets(self):
        return (self, self.enabled_switch, self.enabled_switch.switch, self.sun_combo,
                self.sensitivity_combo, *self._number_spins())

    def set_advanced_open(self, open_: bool) -> None:
        self.advanced_box.setVisible(bool(open_))
        self.advanced_button.setIcon(mdi('chevron-down' if open_ else 'chevron-right'))

    # -- values ------------------------------------------------------------

    def load(self, cfg: dict) -> None:
        """Set every control from ``cfg`` without emitting ``changed``."""
        cfg = cfg if isinstance(cfg, dict) else {}
        previous = [w.blockSignals(True) for w in self._widgets()]
        try:
            self.enabled_switch.set_checked(bool(cfg.get('enabled', _DEFAULTS['enabled'])))
            alt = _number(cfg.get('max_sun_alt_deg'), _DEFAULTS['max_sun_alt_deg'])
            index = min(range(len(SUN_LIMITS)), key=lambda i: abs(SUN_LIMITS[i][1] - alt))
            self.sun_combo.setCurrentIndex(index)
            self._set_numbers({key: _number(cfg.get(key), _DEFAULTS[key]) for key in PRESET_KEYS})
            self._show_preset()
        finally:
            for w, was in zip(self._widgets(), previous):
                w.blockSignals(was)

    def _set_numbers(self, values: dict) -> None:
        self.cloud_spin.setValue(int(round(values['max_cloud_cover_pct'])))
        self.drop_spin.setValue(int(round(values['star_drop_pct'])))
        self.fwhm_spin.setValue(int(round(values['fwhm_rise_pct'])))
        self.bad_spin.setValue(int(values['bad_frames']))
        self.good_spin.setValue(int(values['good_frames']))
        self._star_ratio = round(float(values['min_star_ratio']), 2)

    def _numbers(self) -> dict:
        return {
            'bad_frames': int(self.bad_spin.value()),
            'good_frames': int(self.good_spin.value()),
            'max_cloud_cover_pct': float(self.cloud_spin.value()),
            'min_star_ratio': self._star_ratio,
            'star_drop_pct': float(self.drop_spin.value()),
            'fwhm_rise_pct': float(self.fwhm_spin.value()),
        }

    def values(self) -> dict:
        numbers = self._numbers()
        return {
            'enabled': self.enabled_switch.is_checked(),
            'max_sun_alt_deg': SUN_LIMITS[max(0, self.sun_combo.currentIndex())][1],
            SENSITIVITY_KEY: preset_for(numbers),
            **numbers,
        }

    def sensitivity(self) -> str:
        return self.sensitivity_combo.currentData() or CUSTOM

    def _show_preset(self) -> None:
        numbers = self._numbers()
        name = preset_for(numbers)
        was = self.sensitivity_combo.blockSignals(True)
        self.sensitivity_combo.setCurrentIndex(SENSITIVITY_ITEMS.index(name))
        self.sensitivity_combo.blockSignals(was)
        self.sensitivity_hint.setText(sensitivity_text(name, numbers))
        self.star_floor_label.setText(
            f"“Too few stars” is also reported below {self._star_ratio * 100:.0f}% of the "
            "clear-sky star count (follows the cloud limit)")

    # -- edits -------------------------------------------------------------

    def _on_preset_picked(self, *_):
        name = self.sensitivity_combo.currentData()
        if name == CUSTOM or name is None:
            self.set_advanced_open(True)
            self._show_preset()
            return
        spins = self._number_spins()
        previous = [s.blockSignals(True) for s in spins]
        try:
            self._set_numbers(preset_values(name))
        finally:
            for s, was in zip(spins, previous):
                s.blockSignals(was)
        self._show_preset()
        self._emit_changed()

    def _on_number_edited(self, *_):
        if self.sender() is self.cloud_spin:
            self._star_ratio = star_floor_for(self.cloud_spin.value())
        self._show_preset()
        self._emit_changed()

    def show_conditions(self, snapshot: dict, capturing=None) -> None:
        text, tone = summarize_conditions(snapshot, capturing)
        self.verdict_label.setText(f"Now: {text}")
        self.verdict_label.setStyleSheet(f"color: {_TONE_COLORS.get(tone, Colors.text_primary)};")
        _set_optional(self.reasons_label,
                      secondary_text(snapshot, capturing, self.good_spin.value()))
        target_text, close = target_moon_text(snapshot)
        _set_optional(self.target_label, target_text)
        self.target_label.setStyleSheet(
            f"color: {_TONE_COLORS['warn'] if close else Colors.text_secondary};")
        self.target_label.setToolTip(CLOSE_HINT if close else "")

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
