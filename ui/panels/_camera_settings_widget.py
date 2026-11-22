from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout
from PySide6.QtCore import Qt, Signal
from qfluentwidgets import BodyLabel, CaptionLabel, ComboBox
from ..components.scroll_safe_spinbox import SpinBox, DoubleSpinBox

from ..theme.tokens import Colors, Spacing
from ..theme.icons import mdi
from ..components.cards import SettingsCard, FormRow, SwitchRow, ClickSlider, CollapsibleCard
from ._scheduled_capture_card import ScheduledCaptureCard
from ._zwo_connection_card import ZwoConnectionCard, clean_camera_name
from ._alpaca_connection_card import AlpacaConnectionCard
from ._camera_capability_gate import apply_capabilities, ZWO_WB_MODES
from services.logger import app_logger
from services.config import DEFAULT_CAMERA_PROFILE
from services.camera.camera_white_balance import low_gain_warning
from services.camera.capabilities import ZWO_STATIC, static_capabilities
from services.camera.camera_source import (
    ALPACA, alpaca_profile_key, alpaca_settings, camera_source,
)

_WB_MODES = ZWO_WB_MODES  # re-exported for existing callers
_BAYER_PATTERNS = ["BGGR", "RGGB", "GRBG", "GBRG"]


class CameraSettingsWidget(QWidget):
    settings_changed = Signal()
    detect_cameras_clicked = Signal()
    raw16_mode_changed = Signal(bool)
    revive_camera_clicked = Signal(str)  # Saved camera name to revive via USB reset
    alpaca_discover_clicked = Signal()
    alpaca_test_clicked = Signal()

    def __init__(self, main_window, parent=None):
        super().__init__(parent)
        self.main_window = main_window
        self._loading_config = False
        self._caps = ZWO_STATIC
        self._wb_modes = list(ZWO_WB_MODES)
        self._stored_wb_mode = None
        self._profile_bayer = None
        self._raw16_status_borrowed = False
        self._setup_ui()
        apply_capabilities(self, self._caps)

    @property
    def _can_save(self):
        return not self._loading_config and self.main_window and hasattr(self.main_window, 'config')

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(Spacing.card_gap)
        self._build_connection_cards()
        layout.addWidget(self.zwo_card)
        layout.addWidget(self.alpaca_card)
        layout.addWidget(self._build_exposure_card())
        layout.addWidget(self._build_auto_exposure_card())
        self.schedule_card = ScheduledCaptureCard(self)
        self.schedule_source_rows = self.schedule_card.schedule_source_rows
        layout.addWidget(self.schedule_card)
        layout.addWidget(self._build_wb_card())
        layout.addWidget(self._build_advanced_card())
        layout.addStretch()

    def _build_connection_cards(self):
        self.zwo_card = ZwoConnectionCard()
        self.zwo_card.sdk_path_changed.connect(self._on_sdk_path_changed)
        self.zwo_card.camera_picked.connect(self._on_camera_selected)
        self.zwo_card.detect_clicked.connect(self.detect_cameras_clicked)
        self.zwo_card.revive_clicked.connect(self.revive_camera_clicked)
        self.sdk_path_input = self.zwo_card.sdk_path_input
        self.camera_combo = self.zwo_card.camera_combo
        self.detect_btn = self.zwo_card.detect_btn
        self._missing_notice = self.zwo_card.missing_notice

        self.alpaca_card = AlpacaConnectionCard()
        self.alpaca_card.connection_edited.connect(self._on_alpaca_connection)
        self.alpaca_card.device_picked.connect(self._on_alpaca_connection)
        self.alpaca_card.discover_clicked.connect(self.alpaca_discover_clicked)
        self.alpaca_card.test_clicked.connect(self.alpaca_test_clicked)

    def set_missing_camera_warning(self, saved_name: str, phantom_count: int = 0):
        """Pass empty saved_name to hide. phantom_count>0 shows Revive (Windows) or
        Detect Again with unplug guidance (elsewhere)."""
        self.zwo_card.missing_notice.set_warning(saved_name, phantom_count)

    def reset_revive_button(self):
        """Restore the Revive button after an async reset finishes."""
        self.zwo_card.missing_notice.reset_revive_button()

    def _build_exposure_card(self):
        card = SettingsCard("Exposure Settings", "Control exposure time and gain")

        self.exposure_spin = DoubleSpinBox()
        self.exposure_spin.setRange(0.001, 3600.0)
        self.exposure_spin.setDecimals(3)
        self.exposure_spin.setSuffix(" s")
        self.exposure_spin.setValue(1.0)
        self.exposure_spin.setToolTip("Single frame exposure time (0.001s to 3600s)")
        self.exposure_spin.valueChanged.connect(self._on_exposure_changed)
        card.add_row("Exposure", self.exposure_spin, "0.001s to 3600s")

        self.gain_spin = SpinBox()
        self.gain_spin.setRange(0, 600)
        self.gain_spin.setValue(100)
        self.gain_spin.setToolTip("Camera gain/sensitivity (higher = brighter but more noise)")
        self.gain_spin.valueChanged.connect(self._on_gain_changed)
        self.gain_row = card.add_row("Gain", self.gain_spin, "0 to 600")

        self.interval_spin = DoubleSpinBox()
        self.interval_spin.setRange(0.1, 3600.0)
        self.interval_spin.setDecimals(1)
        self.interval_spin.setSuffix(" s")
        self.interval_spin.setValue(5.0)
        self.interval_spin.setToolTip("Time to wait between capturing frames")
        self.interval_spin.valueChanged.connect(self._on_interval_changed)
        card.add_row("Interval", self.interval_spin, "Time between captures")

        return card

    def _build_auto_exposure_card(self):
        card = CollapsibleCard("Auto Exposure", mdi('camera-iris'))

        self.auto_exp_switch = SwitchRow(
            "Enable Auto Exposure",
            "Automatically adjust exposure based on image brightness"
        )
        self.auto_exp_switch.toggled.connect(self._on_auto_exposure_changed)
        card.add_widget(self.auto_exp_switch)

        self.auto_exp_settings = QWidget()
        auto_exp_layout = QVBoxLayout(self.auto_exp_settings)
        auto_exp_layout.setContentsMargins(0, 0, 0, 0)
        auto_exp_layout.setSpacing(Spacing.input_gap)

        self.target_brightness_slider = ClickSlider(Qt.Horizontal)
        self.target_brightness_slider.setRange(20, 200)
        self.target_brightness_slider.setValue(100)
        self.target_brightness_slider.setToolTip(
            "Raw-frame target for the auto-exposure loop (75th-percentile pixel, 0-255). It only moves\n"
            "exposure, so it does nothing once exposure is pinned at Max Exposure after dark.\n"
            "With Auto Stretch on, output brightness comes from Target Median, not here."
        )
        self.target_brightness_slider.valueChanged.connect(self._on_target_brightness_changed)
        self.target_brightness_label = BodyLabel("100")
        self.target_brightness_label.setFixedWidth(50)
        self.target_brightness_slider.valueChanged.connect(
            lambda v: self.target_brightness_label.setText(str(v))
        )
        target_row = QHBoxLayout()
        target_row.addWidget(self.target_brightness_slider, 1)
        target_row.addWidget(self.target_brightness_label)
        target_widget = QWidget()
        target_widget.setLayout(target_row)
        auto_exp_layout.addWidget(FormRow(
            "Target Brightness", target_widget, "Raw frame only; output brightness is set by Auto Stretch"))

        self.max_exposure_spin = DoubleSpinBox()
        self.max_exposure_spin.setRange(0.1, 3600.0)
        self.max_exposure_spin.setDecimals(1)
        self.max_exposure_spin.setSuffix(" s")
        self.max_exposure_spin.setValue(30.0)
        self.max_exposure_spin.setToolTip("Maximum exposure time for auto-exposure")
        self.max_exposure_spin.valueChanged.connect(self._on_max_exposure_changed)
        auto_exp_layout.addWidget(FormRow("Max Exposure", self.max_exposure_spin, "Upper limit for auto exposure"))

        self.auto_exp_settings.hide()
        card.add_widget(self.auto_exp_settings)

        return card

    def _build_wb_card(self):
        card = CollapsibleCard("White Balance", mdi('palette'))

        self.wb_mode_combo = ComboBox()
        self.wb_mode_combo.addItems(_WB_MODES)
        self.wb_mode_combo.setToolTip("ASI Auto: SDK auto WB | Manual: Use R/B values | Gray World: Software algorithm")
        self.wb_mode_combo.currentIndexChanged.connect(self._on_wb_mode_changed)
        card.add_row("Mode", self.wb_mode_combo)

        self.gray_world_settings = QWidget()
        gray_world_layout = QVBoxLayout(self.gray_world_settings)
        gray_world_layout.setContentsMargins(0, 0, 0, 0)
        gray_world_layout.setSpacing(Spacing.input_gap)

        self.wb_low_spin = SpinBox()
        self.wb_low_spin.setRange(0, 49)
        self.wb_low_spin.setValue(5)
        self.wb_low_spin.setToolTip("Mask dark pixels below this percentile")
        self.wb_low_spin.valueChanged.connect(self._on_wb_gray_world_changed)
        gray_world_layout.addWidget(FormRow("Low %", self.wb_low_spin, "Mask dark pixels"))

        self.wb_high_spin = SpinBox()
        self.wb_high_spin.setRange(51, 100)
        self.wb_high_spin.setValue(95)
        self.wb_high_spin.setToolTip("Mask bright pixels above this percentile")
        self.wb_high_spin.valueChanged.connect(self._on_wb_gray_world_changed)
        gray_world_layout.addWidget(FormRow("High %", self.wb_high_spin, "Mask bright pixels"))

        wb_info = CaptionLabel("Gray World uses mid-tones to balance colors.\nBest for scenes with mixed colors.")
        wb_info.setStyleSheet(f"color: {Colors.text_muted};")
        wb_info.setWordWrap(True)
        gray_world_layout.addWidget(wb_info)

        self.gray_world_settings.hide()
        card.add_widget(self.gray_world_settings)

        self.manual_wb_settings = QWidget()
        manual_box = QVBoxLayout(self.manual_wb_settings)
        manual_box.setContentsMargins(0, 0, 0, 0)
        self.hw_wb_settings = QWidget()
        manual_wb_layout = QVBoxLayout(self.hw_wb_settings)
        manual_wb_layout.setContentsMargins(0, 0, 0, 0)
        manual_wb_layout.setSpacing(Spacing.input_gap)
        manual_box.addWidget(self.hw_wb_settings)
        manual_box.addWidget(self._build_software_wb())

        self.wb_r_slider = ClickSlider(Qt.Horizontal)
        self.wb_r_slider.setRange(1, 99)
        self.wb_r_slider.setValue(75)
        self.wb_r_slider.setToolTip("Red channel white balance: 75")
        self.wb_r_slider.valueChanged.connect(self._on_wb_changed)
        self.wb_r_slider.valueChanged.connect(lambda v: self.wb_r_slider.setToolTip(f"Red channel white balance: {v}"))
        manual_wb_layout.addWidget(FormRow("Red", self.wb_r_slider))

        self.wb_b_slider = ClickSlider(Qt.Horizontal)
        self.wb_b_slider.setRange(1, 99)
        self.wb_b_slider.setValue(99)
        self.wb_b_slider.setToolTip("Blue channel white balance: 99")
        self.wb_b_slider.valueChanged.connect(self._on_wb_changed)
        self.wb_b_slider.valueChanged.connect(lambda v: self.wb_b_slider.setToolTip(f"Blue channel white balance: {v}"))
        manual_wb_layout.addWidget(FormRow("Blue", self.wb_b_slider))

        wb_scale = CaptionLabel(
            "Camera gains, 1-99 with about 50 neutral. Low values remove that colour.")
        wb_scale.setStyleSheet(f"color: {Colors.text_muted};")
        wb_scale.setWordWrap(True)
        manual_wb_layout.addWidget(wb_scale)

        self.wb_low_warning = CaptionLabel()
        self.wb_low_warning.setStyleSheet(f"color: {Colors.warning_text};")
        self.wb_low_warning.setWordWrap(True)
        self.wb_low_warning.hide()
        manual_wb_layout.addWidget(self.wb_low_warning)
        self.wb_r_slider.valueChanged.connect(self._refresh_wb_warning)
        self.wb_b_slider.valueChanged.connect(self._refresh_wb_warning)

        self.manual_wb_settings.hide()
        card.add_widget(self.manual_wb_settings)

        return card

    def _build_software_wb(self):
        """Red/Blue multipliers applied to the debayered frame, for a camera
        without hardware white balance (white_balance.manual_*_gain)."""
        self.sw_wb_settings = QWidget()
        layout = QVBoxLayout(self.sw_wb_settings)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(Spacing.input_gap)
        for attr, label in (('sw_red_gain_spin', "Red gain"), ('sw_blue_gain_spin', "Blue gain")):
            spin = DoubleSpinBox()
            spin.setRange(0.1, 4.0)
            spin.setSingleStep(0.05)
            spin.setDecimals(2)
            spin.setValue(1.0)
            spin.valueChanged.connect(self._on_software_wb_changed)
            setattr(self, attr, spin)
            layout.addWidget(FormRow(label, spin))
        note = CaptionLabel("Applied in software, 1.00 leaves the colour as it is.")
        note.setStyleSheet(f"color: {Colors.text_muted};")
        note.setWordWrap(True)
        layout.addWidget(note)
        self.sw_wb_settings.hide()
        return self.sw_wb_settings

    def _build_advanced_card(self):
        card = CollapsibleCard("Advanced Settings", mdi('tune-variant'))

        self.offset_spin = SpinBox()
        self.offset_spin.setRange(0, 255)
        self.offset_spin.setValue(20)
        self.offset_spin.setToolTip("Black level offset to prevent clipping")
        self.offset_spin.valueChanged.connect(self._on_offset_changed)
        self.offset_row = card.add_row("Offset", self.offset_spin, "Black level (0-255)")

        self.flip_combo = ComboBox()
        self.flip_combo.addItems(["None", "Horizontal", "Vertical", "Both"])
        self.flip_combo.setToolTip("Flip/mirror the image")
        self.flip_combo.currentIndexChanged.connect(self._on_flip_changed)
        card.add_row("Flip", self.flip_combo, "Mirror image orientation")

        self.bayer_combo = ComboBox()
        self.bayer_combo.addItems(_BAYER_PATTERNS)
        self.bayer_combo.setToolTip("Color filter array pattern - BGGR for most ZWO cameras")
        self.bayer_combo.currentIndexChanged.connect(self._on_bayer_changed)
        self.bayer_row = card.add_row("Bayer Pattern", self.bayer_combo, "BGGR for most ASI cameras")

        self.raw16_switch = SwitchRow(
            "Use RAW16 Mode",
            "Capture full sensor bit depth (12-14 bit) instead of RAW8"
        )
        self.raw16_switch.toggled.connect(self._on_raw16_changed)
        self.raw16_switch.setEnabled(False)
        card.add_widget(self.raw16_switch)

        self.raw16_status = CaptionLabel("Connect camera to check RAW16 support")
        self.raw16_status.setStyleSheet(f"color: {Colors.text_secondary}; padding: 4px 8px;")
        card.add_widget(self.raw16_status)

        return card

    _clean_camera_name = staticmethod(clean_camera_name)

    def _on_sdk_path_changed(self, text):
        if self._can_save:
            self.main_window.config.set('zwo_sdk_path', text)
            self.settings_changed.emit()

    def _on_camera_selected(self, actual_index, camera_name):
        if not self._can_save:
            return
        self.main_window.config.set('zwo_selected_camera', actual_index)
        self.main_window.config.set('zwo_selected_camera_name', camera_name)
        # Identity is keyed by serial, which is learned on connect. A manual
        # pick may point at a different body, so clear the stored serial — it is
        # re-learned on the next connect. Leaving a stale serial would make the
        # identity gate reject the camera the user just chose.
        self.main_window.config.set('zwo_selected_camera_serial', '')
        self.load_from_config(self.main_window.config)
        self.settings_changed.emit()

    def _on_alpaca_connection(self, values: dict):
        """Typed address: forget the learned UniqueID and Name, like a manual ZWO
        pick forgets the serial. A discovered device brings its own."""
        if not self._can_save:
            return
        cfg = dict(self.main_window.config.get('alpaca_camera') or {})
        cfg.update({'unique_id': '', 'name': ''})
        cfg.update(values)
        self.main_window.config.set('alpaca_camera', cfg)
        self.load_from_config(self.main_window.config)
        self.settings_changed.emit()

    def _alpaca_identity(self, config):
        settings = alpaca_settings(config)
        return settings['name'], alpaca_profile_key(settings)

    def _save_to_camera_profile(self, **kwargs):
        if not self.main_window or not hasattr(self.main_window, 'config'):
            return
        config = self.main_window.config
        if camera_source(config) == ALPACA:
            camera_name, serial = self._alpaca_identity(config)
            if serial:
                config.update_camera_profile(camera_name, serial=serial, **kwargs)
        else:
            camera_name = self._clean_camera_name(config.get('zwo_selected_camera_name', ''))
            serial = config.get('zwo_selected_camera_serial', '')
            if camera_name:
                config.update_camera_profile(camera_name, serial=serial, **kwargs)
        # The zwo_* mirror is what capture reads for auto-exposure, on either backend.
        for key, value in kwargs.items():
            config.set(f'zwo_{key}', value)

    def _on_exposure_changed(self, value):
        if self._can_save:
            self._save_to_camera_profile(exposure_ms=value * 1000)
            self.settings_changed.emit()

    def _on_gain_changed(self, value):
        if self._can_save:
            self._save_to_camera_profile(gain=value)
            self.settings_changed.emit()

    def _on_interval_changed(self, value):
        if self._can_save:
            self.main_window.config.set('zwo_interval', value)
            self.settings_changed.emit()

    def _on_auto_exposure_changed(self, checked):
        self.auto_exp_settings.setVisible(checked)
        if self._can_save:
            self._save_to_camera_profile(auto_exposure=checked)
            self.settings_changed.emit()

    def _on_target_brightness_changed(self, value):
        if self._can_save:
            self._save_to_camera_profile(target_brightness=value)
            self.settings_changed.emit()

    def _on_max_exposure_changed(self, value):
        if self._can_save:
            self._save_to_camera_profile(max_exposure_ms=value * 1000)
            self.settings_changed.emit()

    def _refresh_wb_warning(self, _value=None):
        warning = low_gain_warning(self.wb_r_slider.value(), self.wb_b_slider.value())
        self.wb_low_warning.setText(f"⚠ {warning}." if warning else "")
        self.wb_low_warning.setVisible(bool(warning))

    def _on_wb_changed(self):
        if self._can_save:
            self._save_to_camera_profile(wb_r=self.wb_r_slider.value(), wb_b=self.wb_b_slider.value())
            self.settings_changed.emit()

    def _on_offset_changed(self, value):
        if self._can_save:
            self._save_to_camera_profile(offset=value)
            self.settings_changed.emit()

    def _on_flip_changed(self, index):
        if self._can_save:
            self._save_to_camera_profile(flip=index)
            self.settings_changed.emit()

    def _on_bayer_changed(self, index):
        if self._can_save:
            self._save_to_camera_profile(bayer_pattern=_BAYER_PATTERNS[index])
            self.settings_changed.emit()

    def _on_raw16_changed(self, checked):
        if not self._can_save:
            return
        dev_mode = self.main_window.config.get('dev_mode', {})
        dev_mode['use_raw16'] = checked
        self.main_window.config.set('dev_mode', dev_mode)
        self.main_window.config.save()
        self.settings_changed.emit()
        self.raw16_mode_changed.emit(checked)
        app_logger.info(
            f"RAW16 mode {'enabled' if checked else 'disabled'}: "
            f"{'Full' if checked else 'Standard 8-bit'} sensor bit depth will be used"
        )

    def _select_wb_mode(self, stored):
        """Show `stored`; a mode this camera lacks (asi_auto) reads as gray_world
        without being written back."""
        shown = "gray_world" if stored == "asi_auto" and stored not in self._wb_modes else stored
        if shown in self._wb_modes:
            self.wb_mode_combo.blockSignals(True)
            self.wb_mode_combo.setCurrentIndex(self._wb_modes.index(shown))
            self.wb_mode_combo.blockSignals(False)
        self.manual_wb_settings.setVisible(shown == "manual")
        self.gray_world_settings.setVisible(shown == "gray_world")

    def _select_bayer(self, pattern) -> bool:
        if pattern not in _BAYER_PATTERNS:
            return False
        self.bayer_combo.setCurrentIndex(_BAYER_PATTERNS.index(pattern))
        return True

    def _on_software_wb_changed(self, _value=None):
        if not self._can_save:
            return
        wb_settings = self.main_window.config.get('white_balance', {})
        wb_settings['manual_red_gain'] = round(self.sw_red_gain_spin.value(), 2)
        wb_settings['manual_blue_gain'] = round(self.sw_blue_gain_spin.value(), 2)
        self.main_window.config.set('white_balance', wb_settings)
        self.settings_changed.emit()

    def _on_wb_mode_changed(self, index):
        if not 0 <= index < len(self._wb_modes):
            return
        mode = self._wb_modes[index]
        self.manual_wb_settings.setVisible(mode == "manual")
        self.gray_world_settings.setVisible(mode == "gray_world")
        if self._can_save:
            self._stored_wb_mode = mode
            wb_settings = self.main_window.config.get('white_balance', {})
            wb_settings['mode'] = mode
            self.main_window.config.set('white_balance', wb_settings)
            self.settings_changed.emit()

    def _on_wb_gray_world_changed(self):
        if not self._can_save:
            return
        wb_settings = self.main_window.config.get('white_balance', {})
        wb_settings['gray_world_low_pct'] = self.wb_low_spin.value()
        wb_settings['gray_world_high_pct'] = self.wb_high_spin.value()
        self.main_window.config.set('white_balance', wb_settings)
        self.settings_changed.emit()

    def apply_capabilities(self, caps):
        """Gate the widgets on `caps` (static per source, or a live session's)."""
        apply_capabilities(self, caps)

    def _active_profile(self, config):
        if camera_source(config) == ALPACA:
            # Read, never create: a profile per keystroke of a typed host would
            # pile up. The first edited setting creates it.
            name, key = self._alpaca_identity(config)
            if key and key in config.list_camera_profiles():
                return config.get_camera_profile(name, key) or dict(DEFAULT_CAMERA_PROFILE)
            return dict(DEFAULT_CAMERA_PROFILE)
        active_name = config.get('zwo_selected_camera_name', '') or config.get('zwo_camera_name', '')
        active_serial = config.get('zwo_selected_camera_serial', '')
        return (
            self.main_window.config.get_camera_profile(active_name, active_serial)
            if active_name else dict(DEFAULT_CAMERA_PROFILE)
        )

    def load_from_config(self, config):
        self._loading_config = True
        try:
            source = camera_source(config)
            if self._caps.backend != source:
                self._caps = static_capabilities(source)
            # Ranges first, so a stored value is never clamped by the last
            # camera's limits.
            apply_capabilities(self, self._caps)
            self.sdk_path_input.setText(config.get('zwo_sdk_path', ''))
            self.alpaca_card.load(alpaca_settings(config))
            profile = self._active_profile(config)

            self.exposure_spin.setValue(
                profile.get('exposure_ms', DEFAULT_CAMERA_PROFILE['exposure_ms']) / 1000.0
            )
            self.gain_spin.setValue(profile.get('gain', DEFAULT_CAMERA_PROFILE['gain']))
            self.interval_spin.setValue(config.get('zwo_interval', 5.0))

            auto_exp_enabled = config.get('zwo_auto_exposure', False)
            self.auto_exp_switch.set_checked(auto_exp_enabled)
            self.target_brightness_slider.setValue(
                profile.get('target_brightness', DEFAULT_CAMERA_PROFILE['target_brightness'])
            )
            self.max_exposure_spin.setValue(
                profile.get('max_exposure_ms', DEFAULT_CAMERA_PROFILE['max_exposure_ms']) / 1000.0
            )
            self.auto_exp_settings.setVisible(auto_exp_enabled)

            self.schedule_card.load(config)

            wb_settings = config.get('white_balance', {})
            self._stored_wb_mode = wb_settings.get('mode', 'asi_auto')
            self._select_wb_mode(self._stored_wb_mode)
            self.wb_low_spin.setValue(wb_settings.get('gray_world_low_pct', 5))
            self.wb_high_spin.setValue(wb_settings.get('gray_world_high_pct', 95))
            self.sw_red_gain_spin.setValue(float(wb_settings.get('manual_red_gain', 1.0)))
            self.sw_blue_gain_spin.setValue(float(wb_settings.get('manual_blue_gain', 1.0)))

            self.wb_r_slider.setValue(profile.get('wb_r', DEFAULT_CAMERA_PROFILE['wb_r']))
            self.wb_b_slider.setValue(profile.get('wb_b', DEFAULT_CAMERA_PROFILE['wb_b']))
            self.offset_spin.setValue(profile.get('offset', DEFAULT_CAMERA_PROFILE['offset']))
            self.flip_combo.setCurrentIndex(profile.get('flip', DEFAULT_CAMERA_PROFILE['flip']))

            self._profile_bayer = profile.get('bayer_pattern', DEFAULT_CAMERA_PROFILE['bayer_pattern'])
            if not self._caps.bayer_from_device:
                self._select_bayer(self._profile_bayer)
        finally:
            self._loading_config = False

    def set_cameras(self, camera_list: list):
        self.zwo_card.set_cameras(camera_list)

    def set_detecting(self, is_detecting: bool):
        self.zwo_card.set_detecting(is_detecting)

    def set_capture_active(self, active: bool):
        """Lock camera selection while capturing: Stop → pick a camera → Start."""
        self.zwo_card.set_capture_active(active)
        self.alpaca_card.set_capture_active(active)

    def update_camera_capabilities(self, supports_raw16: bool, bit_depth: int):
        self._loading_config = True
        try:
            if supports_raw16:
                self.raw16_switch.setEnabled(True)
                self.raw16_status.setText(f"✓ Camera supports RAW16 ({bit_depth}-bit ADC)")
                self.raw16_status.setStyleSheet(f"color: {Colors.success_text}; padding: 4px 8px;")
                if self.main_window and hasattr(self.main_window, 'config'):
                    dev_mode = self.main_window.config.get('dev_mode', {})
                    self.raw16_switch.set_checked(dev_mode.get('use_raw16', False))
            else:
                self.raw16_switch.setEnabled(False)
                self.raw16_switch.set_checked(False)
                self.raw16_status.setText(f"✗ Camera does not support RAW16 ({bit_depth}-bit ADC, RAW8 only)")
                self.raw16_status.setStyleSheet(f"color: {Colors.text_secondary}; padding: 4px 8px;")
        finally:
            self._loading_config = False

    def reset_camera_capabilities(self):
        if self._caps.backend != 'zwo':
            apply_capabilities(self, static_capabilities(self._caps.backend))
            return
        self._loading_config = True
        try:
            self.raw16_switch.setEnabled(False)
            self.raw16_switch.set_checked(False)
            self.raw16_status.setText("Connect camera to check RAW16 support")
            self.raw16_status.setStyleSheet(f"color: {Colors.text_secondary}; padding: 4px 8px;")
        finally:
            self._loading_config = False
