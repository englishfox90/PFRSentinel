"""
Shared Capture Settings Widgets

Reusable UI components for camera capture settings that are shared
between ZWO and ASCOM camera modes.
"""
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout
from PySide6.QtCore import Qt, Signal, QTime
from qfluentwidgets import (
    BodyLabel, CaptionLabel, ComboBox, SpinBox, DoubleSpinBox,
    FluentIcon, TimePicker
)

from .cards import CollapsibleCard, FormRow, SwitchRow, ClickSlider
from ..theme.tokens import Colors, Spacing


class AutoExposureCard(CollapsibleCard):
    """
    Auto-exposure settings card.
    
    Signals:
        enabled_changed(bool): Auto-exposure toggled
        target_changed(int): Target brightness changed (20-200)
        max_exposure_changed(float): Max exposure changed (seconds)
    """
    enabled_changed = Signal(bool)
    target_changed = Signal(int)
    max_exposure_changed = Signal(float)
    
    def __init__(self, parent=None):
        super().__init__("Auto Exposure", FluentIcon.BRIGHTNESS, parent)
        self._loading = True
        self._init_controls()
        self._loading = False
    
    def _init_controls(self):
        # Enable switch
        self.enable_switch = SwitchRow(
            "Enable Auto Exposure",
            "Automatically adjust exposure based on image brightness"
        )
        self.enable_switch.toggled.connect(self._on_enabled_changed)
        self.add_widget(self.enable_switch)
        
        # Settings container (shown when enabled)
        self.settings_widget = QWidget()
        settings_layout = QVBoxLayout(self.settings_widget)
        settings_layout.setContentsMargins(0, 0, 0, 0)
        settings_layout.setSpacing(Spacing.input_gap)
        
        # Target brightness slider
        self.target_slider = ClickSlider(Qt.Horizontal)
        self.target_slider.setRange(20, 200)
        self.target_slider.setValue(100)
        self.target_slider.setToolTip("Target image brightness: 100")
        self.target_slider.valueChanged.connect(self._on_target_changed)
        self.target_slider.valueChanged.connect(
            lambda v: self.target_slider.setToolTip(f"Target image brightness: {v}")
        )
        target_row = FormRow("Target Brightness", self.target_slider, "20=dark, 200=bright")
        settings_layout.addWidget(target_row)
        
        # Max exposure
        self.max_exposure_spin = DoubleSpinBox()
        self.max_exposure_spin.setRange(0.1, 3600.0)
        self.max_exposure_spin.setDecimals(1)
        self.max_exposure_spin.setSuffix(" s")
        self.max_exposure_spin.setValue(30.0)
        self.max_exposure_spin.setToolTip("Maximum exposure time for auto-exposure")
        self.max_exposure_spin.valueChanged.connect(self._on_max_exposure_changed)
        max_row = FormRow("Max Exposure", self.max_exposure_spin, "Upper limit for auto exposure")
        settings_layout.addWidget(max_row)
        
        self.settings_widget.hide()
        self.add_widget(self.settings_widget)
    
    def _on_enabled_changed(self, checked: bool):
        self.settings_widget.setVisible(checked)
        if not self._loading:
            self.enabled_changed.emit(checked)
    
    def _on_target_changed(self, value: int):
        if not self._loading:
            self.target_changed.emit(value)
    
    def _on_max_exposure_changed(self, value: float):
        if not self._loading:
            self.max_exposure_changed.emit(value)
    
    def set_values(self, enabled: bool, target: int, max_exposure: float):
        """Set all values without emitting signals"""
        self._loading = True
        self.enable_switch.set_checked(enabled)
        self.settings_widget.setVisible(enabled)
        self.target_slider.setValue(target)
        self.max_exposure_spin.setValue(max_exposure)
        self._loading = False


class ScheduledCaptureCard(CollapsibleCard):
    """
    Scheduled capture settings card.
    
    Signals:
        enabled_changed(bool): Schedule toggled
        times_changed(str, str): Start and end times changed (HH:MM format)
    """
    enabled_changed = Signal(bool)
    times_changed = Signal(str, str)
    
    def __init__(self, parent=None):
        super().__init__("Scheduled Capture", FluentIcon.CALENDAR, parent)
        self._loading = True
        self._init_controls()
        self._loading = False
    
    def _init_controls(self):
        # Enable switch
        self.enable_switch = SwitchRow(
            "Enable Scheduled Capture",
            "Only capture during specified time window"
        )
        self.enable_switch.toggled.connect(self._on_enabled_changed)
        self.add_widget(self.enable_switch)
        
        # Time settings container
        self.time_widget = QWidget()
        time_row = QHBoxLayout(self.time_widget)
        time_row.setContentsMargins(0, 0, 0, 0)
        time_row.setSpacing(Spacing.md)
        
        label_start = BodyLabel("Active:")
        label_start.setStyleSheet(f"color: {Colors.text_secondary};")
        time_row.addWidget(label_start)
        
        self.start_time = TimePicker()
        self.start_time.setTime(QTime(17, 0))
        self.start_time.setToolTip("Start time (24hr format)")
        self.start_time.timeChanged.connect(self._on_time_changed)
        time_row.addWidget(self.start_time)
        
        label_to = BodyLabel("to")
        label_to.setStyleSheet(f"color: {Colors.text_muted};")
        time_row.addWidget(label_to)
        
        self.end_time = TimePicker()
        self.end_time.setTime(QTime(9, 0))
        self.end_time.setToolTip("End time (24hr format, can span midnight)")
        self.end_time.timeChanged.connect(self._on_time_changed)
        time_row.addWidget(self.end_time)
        
        time_row.addStretch()
        
        self.time_widget.hide()
        self.add_widget(self.time_widget)
    
    def _on_enabled_changed(self, checked: bool):
        self.time_widget.setVisible(checked)
        if not self._loading:
            self.enabled_changed.emit(checked)
    
    def _on_time_changed(self):
        if not self._loading:
            start = self.start_time.time().toString("HH:mm")
            end = self.end_time.time().toString("HH:mm")
            self.times_changed.emit(start, end)
    
    def set_values(self, enabled: bool, start_time: str, end_time: str):
        """Set all values without emitting signals"""
        self._loading = True
        self.enable_switch.set_checked(enabled)
        self.time_widget.setVisible(enabled)
        if start_time:
            t = QTime.fromString(start_time, "HH:mm")
            if t.isValid():
                self.start_time.setTime(t)
        if end_time:
            t = QTime.fromString(end_time, "HH:mm")
            if t.isValid():
                self.end_time.setTime(t)
        self._loading = False


class WhiteBalanceCard(CollapsibleCard):
    """
    White balance settings card.
    
    Args:
        show_asi_mode: If True, includes 'asi_auto' in mode options (ZWO only)
    
    Signals:
        mode_changed(str): Mode changed ('manual', 'gray_world', or 'asi_auto')
        manual_values_changed(int, int): Red and blue values changed (1-99)
        gray_world_changed(int, int): Low and high percentile changed
    """
    mode_changed = Signal(str)
    manual_values_changed = Signal(int, int)
    gray_world_changed = Signal(int, int)
    
    def __init__(self, show_asi_mode: bool = True, parent=None):
        super().__init__("White Balance", FluentIcon.PALETTE, parent)
        self._loading = True
        self._show_asi_mode = show_asi_mode
        self._init_controls()
        self._loading = False
    
    def _init_controls(self):
        # Mode selector
        self.mode_combo = ComboBox()
        if self._show_asi_mode:
            self.mode_combo.addItems(["asi_auto", "manual", "gray_world"])
            self.mode_combo.setToolTip(
                "ASI Auto: SDK auto WB | Manual: Use R/B values | Gray World: Software algorithm"
            )
        else:
            self.mode_combo.addItems(["manual", "gray_world"])
            self.mode_combo.setToolTip(
                "Manual: Use R/B values | Gray World: Software algorithm"
            )
        self.mode_combo.currentTextChanged.connect(self._on_mode_changed)
        self.add_row("Mode", self.mode_combo)
        
        # Manual settings container
        self.manual_widget = QWidget()
        manual_layout = QVBoxLayout(self.manual_widget)
        manual_layout.setContentsMargins(0, 0, 0, 0)
        manual_layout.setSpacing(Spacing.input_gap)
        
        # Red slider
        self.wb_r_slider = ClickSlider(Qt.Horizontal)
        self.wb_r_slider.setRange(1, 99)
        self.wb_r_slider.setValue(75)
        self.wb_r_slider.setToolTip("Red channel white balance: 75")
        self.wb_r_slider.valueChanged.connect(self._on_manual_changed)
        self.wb_r_slider.valueChanged.connect(
            lambda v: self.wb_r_slider.setToolTip(f"Red channel white balance: {v}")
        )
        manual_layout.addWidget(FormRow("Red", self.wb_r_slider))
        
        # Blue slider
        self.wb_b_slider = ClickSlider(Qt.Horizontal)
        self.wb_b_slider.setRange(1, 99)
        self.wb_b_slider.setValue(99)
        self.wb_b_slider.setToolTip("Blue channel white balance: 99")
        self.wb_b_slider.valueChanged.connect(self._on_manual_changed)
        self.wb_b_slider.valueChanged.connect(
            lambda v: self.wb_b_slider.setToolTip(f"Blue channel white balance: {v}")
        )
        manual_layout.addWidget(FormRow("Blue", self.wb_b_slider))
        
        self.manual_widget.hide()
        self.add_widget(self.manual_widget)
        
        # Gray world settings container
        self.gray_world_widget = QWidget()
        gw_layout = QVBoxLayout(self.gray_world_widget)
        gw_layout.setContentsMargins(0, 0, 0, 0)
        gw_layout.setSpacing(Spacing.input_gap)
        
        self.gw_low_spin = SpinBox()
        self.gw_low_spin.setRange(0, 49)
        self.gw_low_spin.setValue(5)
        self.gw_low_spin.setToolTip("Mask dark pixels below this percentile")
        self.gw_low_spin.valueChanged.connect(self._on_gray_world_changed)
        gw_layout.addWidget(FormRow("Low %", self.gw_low_spin, "Mask dark pixels"))
        
        self.gw_high_spin = SpinBox()
        self.gw_high_spin.setRange(51, 100)
        self.gw_high_spin.setValue(95)
        self.gw_high_spin.setToolTip("Mask bright pixels above this percentile")
        self.gw_high_spin.valueChanged.connect(self._on_gray_world_changed)
        gw_layout.addWidget(FormRow("High %", self.gw_high_spin, "Mask bright pixels"))
        
        info = CaptionLabel("Gray World uses mid-tones to balance colors.\nBest for scenes with mixed colors.")
        info.setStyleSheet(f"color: {Colors.text_muted};")
        info.setWordWrap(True)
        gw_layout.addWidget(info)
        
        self.gray_world_widget.hide()
        self.add_widget(self.gray_world_widget)
    
    def _on_mode_changed(self, mode: str):
        # Show/hide appropriate settings
        self.manual_widget.setVisible(mode == "manual")
        self.gray_world_widget.setVisible(mode == "gray_world")
        if not self._loading:
            self.mode_changed.emit(mode)
    
    def _on_manual_changed(self):
        if not self._loading:
            self.manual_values_changed.emit(
                self.wb_r_slider.value(),
                self.wb_b_slider.value()
            )
    
    def _on_gray_world_changed(self):
        if not self._loading:
            self.gray_world_changed.emit(
                self.gw_low_spin.value(),
                self.gw_high_spin.value()
            )
    
    def set_values(self, mode: str, red: int = 75, blue: int = 99,
                   gw_low: int = 5, gw_high: int = 95):
        """Set all values without emitting signals"""
        self._loading = True
        
        # Set mode (handles visibility)
        idx = self.mode_combo.findText(mode)
        if idx >= 0:
            self.mode_combo.setCurrentIndex(idx)
        self._on_mode_changed(mode)  # Update visibility
        
        # Set values
        self.wb_r_slider.setValue(red)
        self.wb_b_slider.setValue(blue)
        self.gw_low_spin.setValue(gw_low)
        self.gw_high_spin.setValue(gw_high)
        
        self._loading = False
