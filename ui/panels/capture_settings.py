"""
Capture Settings Panel (Refactored)

Main capture settings panel that composes mode-specific widgets.
Uses shared capture widgets for common functionality.
"""
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QScrollArea,
    QFileDialog, QStackedWidget
)
from PySide6.QtCore import Qt, Signal, QTime
from qfluentwidgets import (
    CardWidget, SubtitleLabel, CaptionLabel,
    PushButton, ComboBox, LineEdit, SpinBox, DoubleSpinBox,
    SegmentedWidget, FluentIcon, InfoBar, InfoBarPosition
)

from ..theme.tokens import Colors, Spacing
from ..components.cards import SettingsCard, FormRow, SwitchRow, CollapsibleCard, ClickSlider
from ..components.capture_widgets import (
    AutoExposureCard, ScheduledCaptureCard, WhiteBalanceCard
)
from .capture_handlers import CaptureSettingsHandlers


class CaptureSettingsPanel(CaptureSettingsHandlers, QScrollArea):
    """
    Capture settings panel with mode selector and mode-specific settings.
    
    Modes:
    - Directory Watch: Monitor folder for new images
    - ZWO Camera: Direct capture from ZWO ASI cameras
    - ASCOM Camera: Direct capture from ASCOM-compatible cameras
    """
    
    settings_changed = Signal()
    detect_cameras_clicked = Signal()
    raw16_mode_changed = Signal(bool)
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.main_window = parent
        self._loading_config = True
        self._setup_ui()
        self._loading_config = False
    
    def _setup_ui(self):
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setStyleSheet(f"QScrollArea {{ background-color: {Colors.bg_app}; border: none; }}")
        
        content = QWidget()
        self.setWidget(content)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(Spacing.base, Spacing.base, Spacing.base, Spacing.base)
        layout.setSpacing(Spacing.card_gap)
        
        # === MODE SELECTOR ===
        mode_card = CardWidget()
        mode_layout = QVBoxLayout(mode_card)
        mode_layout.setContentsMargins(Spacing.card_padding, Spacing.card_padding,
                                       Spacing.card_padding, Spacing.card_padding)
        mode_layout.setSpacing(Spacing.md)
        
        mode_header = SubtitleLabel("Capture Mode")
        mode_header.setStyleSheet(f"color: {Colors.text_primary};")
        mode_layout.addWidget(mode_header)
        
        mode_desc = CaptionLabel("Choose between monitoring a directory or capturing from camera.")
        mode_desc.setStyleSheet(f"color: {Colors.text_muted};")
        mode_desc.setWordWrap(True)
        mode_layout.addWidget(mode_desc)
        
        self.mode_selector = SegmentedWidget()
        self.mode_selector.addItem('watch', 'Directory Watch', onClick=lambda: self._on_mode_changed('watch'))
        self.mode_selector.addItem('camera', 'ZWO Camera', onClick=lambda: self._on_mode_changed('camera'))
        self.mode_selector.addItem('ascom', 'ASCOM Camera', onClick=lambda: self._on_mode_changed('ascom'))
        self.mode_selector.setCurrentItem('camera')
        mode_layout.addWidget(self.mode_selector)
        layout.addWidget(mode_card)
        
        # === MODE-SPECIFIC SETTINGS STACK ===
        self.settings_stack = QStackedWidget()
        self.settings_stack.addWidget(self._create_watch_settings())
        self.settings_stack.addWidget(self._create_zwo_settings())
        self.settings_stack.addWidget(self._create_ascom_settings())
        self.settings_stack.setCurrentIndex(1)
        layout.addWidget(self.settings_stack, 1)
        layout.addStretch()
    
    def _on_mode_changed(self, mode: str):
        idx = {'watch': 0, 'camera': 1, 'ascom': 2}.get(mode, 1)
        self.settings_stack.setCurrentIndex(idx)
        if not self._loading_config and self.main_window:
            self.main_window.config.set('capture_mode', mode)
            self.settings_changed.emit()
    
    # =========================================================================
    # WATCH SETTINGS
    # =========================================================================
    def _create_watch_settings(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(Spacing.card_gap)
        
        card = SettingsCard("Watch Directory", "Monitor this folder for new images")
        
        dir_row = QHBoxLayout()
        dir_row.setSpacing(Spacing.sm)
        self.watch_dir_input = LineEdit()
        self.watch_dir_input.setPlaceholderText("Select directory to watch...")
        self.watch_dir_input.textChanged.connect(self._on_watch_dir_changed)
        dir_row.addWidget(self.watch_dir_input, 1)
        
        browse_btn = PushButton("Browse")
        browse_btn.setIcon(FluentIcon.FOLDER)
        browse_btn.clicked.connect(lambda: self._browse_dir(self.watch_dir_input))
        dir_row.addWidget(browse_btn)
        
        dir_widget = QWidget()
        dir_widget.setLayout(dir_row)
        card.add_widget(dir_widget)
        
        self.recursive_switch = SwitchRow("Include Subfolders", "Watch subdirectories recursively")
        self.recursive_switch.toggled.connect(self._on_recursive_changed)
        card.add_widget(self.recursive_switch)
        
        layout.addWidget(card)
        layout.addStretch()
        return widget
    
    # =========================================================================
    # ZWO CAMERA SETTINGS
    # =========================================================================
    def _create_zwo_settings(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(Spacing.card_gap)
        
        # --- Connection ---
        conn_card = SettingsCard("Camera Connection", "Connect to your ZWO ASI camera")
        
        sdk_row = QHBoxLayout()
        sdk_row.setSpacing(Spacing.sm)
        self.sdk_path_input = LineEdit()
        self.sdk_path_input.setPlaceholderText(f"Path to {get_zwo_sdk_name()}")
        self.sdk_path_input.textChanged.connect(self._on_sdk_path_changed)
        sdk_row.addWidget(self.sdk_path_input, 1)
        sdk_browse = PushButton("Browse")
        sdk_browse.setIcon(FluentIcon.FOLDER)
        sdk_browse.clicked.connect(self._browse_sdk)
        sdk_row.addWidget(sdk_browse)
        sdk_widget = QWidget()
        sdk_widget.setLayout(sdk_row)
        conn_card.add_row("SDK Path", sdk_widget)
        
        cam_row = QHBoxLayout()
        cam_row.setSpacing(Spacing.sm)
        self.camera_combo = ComboBox()
        self.camera_combo.setPlaceholderText("No cameras detected")
        self.camera_combo.currentIndexChanged.connect(self._on_camera_selected)
        cam_row.addWidget(self.camera_combo, 1)
        self.detect_btn = PushButton("Detect")
        self.detect_btn.setIcon(FluentIcon.SYNC)
        self.detect_btn.clicked.connect(lambda: self.detect_cameras_clicked.emit())
        cam_row.addWidget(self.detect_btn)
        cam_widget = QWidget()
        cam_widget.setLayout(cam_row)
        conn_card.add_row("Camera", cam_widget)
        layout.addWidget(conn_card)
        
        # --- Exposure ---
        exp_card = SettingsCard("Exposure Settings", "Control exposure time and gain")
        self.exposure_spin = DoubleSpinBox()
        self.exposure_spin.setRange(0.001, 3600.0)
        self.exposure_spin.setDecimals(3)
        self.exposure_spin.setSuffix(" s")
        self.exposure_spin.setValue(1.0)
        self.exposure_spin.valueChanged.connect(self._on_zwo_exposure_changed)
        exp_card.add_row("Exposure", self.exposure_spin, "0.001s to 3600s")
        
        self.gain_spin = SpinBox()
        self.gain_spin.setRange(0, 600)
        self.gain_spin.setValue(100)
        self.gain_spin.valueChanged.connect(self._on_zwo_gain_changed)
        exp_card.add_row("Gain", self.gain_spin, "0 to 600")
        
        self.interval_spin = DoubleSpinBox()
        self.interval_spin.setRange(0.1, 3600.0)
        self.interval_spin.setDecimals(1)
        self.interval_spin.setSuffix(" s")
        self.interval_spin.setValue(5.0)
        self.interval_spin.valueChanged.connect(self._on_zwo_interval_changed)
        exp_card.add_row("Interval", self.interval_spin, "Time between captures")
        layout.addWidget(exp_card)
        
        # --- Auto Exposure (shared widget) ---
        self.zwo_auto_exp = AutoExposureCard()
        self.zwo_auto_exp.enabled_changed.connect(self._on_zwo_auto_exp_enabled)
        self.zwo_auto_exp.target_changed.connect(self._on_zwo_target_brightness)
        self.zwo_auto_exp.max_exposure_changed.connect(self._on_zwo_max_exposure)
        layout.addWidget(self.zwo_auto_exp)
        
        # --- Scheduled Capture (shared widget) ---
        self.zwo_schedule = ScheduledCaptureCard()
        self.zwo_schedule.enabled_changed.connect(self._on_schedule_enabled)
        self.zwo_schedule.times_changed.connect(self._on_schedule_times)
        layout.addWidget(self.zwo_schedule)
        
        # --- White Balance (shared widget, with ASI mode) ---
        self.zwo_wb = WhiteBalanceCard(show_asi_mode=True)
        self.zwo_wb.mode_changed.connect(self._on_wb_mode_changed)
        self.zwo_wb.manual_values_changed.connect(self._on_wb_manual_changed)
        self.zwo_wb.gray_world_changed.connect(self._on_wb_gray_world_changed)
        layout.addWidget(self.zwo_wb)
        
        # --- Advanced ---
        adv_card = CollapsibleCard("Advanced Settings", FluentIcon.SETTING)
        self.offset_spin = SpinBox()
        self.offset_spin.setRange(0, 255)
        self.offset_spin.setValue(20)
        self.offset_spin.valueChanged.connect(self._on_offset_changed)
        adv_card.add_row("Offset", self.offset_spin, "Black level (0-255)")
        
        self.flip_combo = ComboBox()
        self.flip_combo.addItems(["None", "Horizontal", "Vertical", "Both"])
        self.flip_combo.currentIndexChanged.connect(self._on_flip_changed)
        adv_card.add_row("Flip", self.flip_combo, "Mirror image orientation")
        
        self.bayer_combo = ComboBox()
        self.bayer_combo.addItems(["BGGR", "RGGB", "GRBG", "GBRG"])
        self.bayer_combo.currentIndexChanged.connect(self._on_bayer_changed)
        adv_card.add_row("Bayer Pattern", self.bayer_combo, "BGGR for most ASI cameras")
        
        self.raw16_switch = SwitchRow("Use RAW16 Mode", "Capture full sensor bit depth")
        self.raw16_switch.toggled.connect(self._on_raw16_changed)
        self.raw16_switch.setEnabled(False)
        adv_card.add_widget(self.raw16_switch)
        
        self.raw16_status = CaptionLabel("Connect camera to check RAW16 support")
        self.raw16_status.setStyleSheet(f"color: {Colors.text_secondary}; padding: 4px 8px;")
        adv_card.add_widget(self.raw16_status)
        layout.addWidget(adv_card)
        
        layout.addStretch()
        return widget
    
    # =========================================================================
    # ASCOM CAMERA SETTINGS
    # =========================================================================
    def _create_ascom_settings(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(Spacing.card_gap)
        
        # --- Connection ---
        conn_card = SettingsCard("ASCOM Camera Connection", "Connect to ASCOM-compatible cameras via Alpaca")
        
        from services.camera.ascom import check_ascom_availability, ASCOM_BACKEND, ASCOM_VERSION
        info = check_ascom_availability()
        
        # Platform-aware status message
        if info['available']:
            status = f"ASCOM: {ASCOM_BACKEND} v{ASCOM_VERSION}"
            status_color = Colors.status_success
        else:
            status = "ASCOM not available - install alpyca: pip install alpyca"
            status_color = Colors.status_error
        
        status_lbl = CaptionLabel(status)
        status_lbl.setStyleSheet(f"color: {status_color};")
        conn_card.add_widget(status_lbl)
        
        # Add cross-platform note for non-Windows users
        if sys.platform != 'win32':
            note_lbl = CaptionLabel("Note: Use ASCOM Alpaca (network API). COM drivers are Windows-only.")
            note_lbl.setStyleSheet(f"color: {Colors.text_muted}; font-style: italic;")
            note_lbl.setWordWrap(True)
            conn_card.add_widget(note_lbl)
        
        server_row = QHBoxLayout()
        server_row.setSpacing(Spacing.sm)
        self.ascom_host_input = LineEdit()
        self.ascom_host_input.setPlaceholderText("localhost")
        self.ascom_host_input.setText("localhost")
        self.ascom_host_input.textChanged.connect(self._on_ascom_host_changed)
        server_row.addWidget(self.ascom_host_input, 3)
        port_lbl = CaptionLabel("Port:")
        port_lbl.setStyleSheet(f"color: {Colors.text_secondary};")
        server_row.addWidget(port_lbl)
        self.ascom_port_spin = SpinBox()
        self.ascom_port_spin.setRange(1, 65535)
        self.ascom_port_spin.setValue(11111)
        self.ascom_port_spin.valueChanged.connect(self._on_ascom_port_changed)
        server_row.addWidget(self.ascom_port_spin, 1)
        srv_widget = QWidget()
        srv_widget.setLayout(server_row)
        conn_card.add_row("Alpaca Server", srv_widget)
        
        cam_row = QHBoxLayout()
        cam_row.setSpacing(Spacing.sm)
        self.ascom_camera_combo = ComboBox()
        self.ascom_camera_combo.setPlaceholderText("No cameras detected")
        self.ascom_camera_combo.currentIndexChanged.connect(self._on_ascom_camera_selected)
        cam_row.addWidget(self.ascom_camera_combo, 1)
        self.ascom_detect_btn = PushButton("Detect")
        self.ascom_detect_btn.setIcon(FluentIcon.SYNC)
        self.ascom_detect_btn.clicked.connect(self._on_ascom_detect_cameras)
        cam_row.addWidget(self.ascom_detect_btn)
        cam_widget = QWidget()
        cam_widget.setLayout(cam_row)
        conn_card.add_row("Camera", cam_widget)
        layout.addWidget(conn_card)
        
        # --- Exposure ---
        exp_card = SettingsCard("Exposure Settings", "Control exposure time and gain")
        self.ascom_exposure_spin = DoubleSpinBox()
        self.ascom_exposure_spin.setRange(0.001, 3600.0)
        self.ascom_exposure_spin.setDecimals(3)
        self.ascom_exposure_spin.setSuffix(" s")
        self.ascom_exposure_spin.setValue(1.0)
        self.ascom_exposure_spin.valueChanged.connect(self._on_ascom_exposure_changed)
        exp_card.add_row("Exposure", self.ascom_exposure_spin, "0.001s to 3600s")
        
        self.ascom_gain_spin = SpinBox()
        self.ascom_gain_spin.setRange(0, 600)
        self.ascom_gain_spin.setValue(100)
        self.ascom_gain_spin.valueChanged.connect(self._on_ascom_gain_changed)
        exp_card.add_row("Gain", self.ascom_gain_spin, "0 to 600")
        
        self.ascom_interval_spin = DoubleSpinBox()
        self.ascom_interval_spin.setRange(0.1, 3600.0)
        self.ascom_interval_spin.setDecimals(1)
        self.ascom_interval_spin.setSuffix(" s")
        self.ascom_interval_spin.setValue(5.0)
        self.ascom_interval_spin.valueChanged.connect(self._on_ascom_interval_changed)
        exp_card.add_row("Interval", self.ascom_interval_spin, "Time between captures")
        layout.addWidget(exp_card)
        
        # --- Auto Exposure (shared widget) ---
        self.ascom_auto_exp = AutoExposureCard()
        self.ascom_auto_exp.enabled_changed.connect(self._on_ascom_auto_exp_enabled)
        self.ascom_auto_exp.target_changed.connect(self._on_ascom_target_brightness)
        self.ascom_auto_exp.max_exposure_changed.connect(self._on_ascom_max_exposure)
        layout.addWidget(self.ascom_auto_exp)
        
        # --- Scheduled Capture (shared widget) ---
        self.ascom_schedule = ScheduledCaptureCard()
        self.ascom_schedule.enabled_changed.connect(self._on_ascom_schedule_enabled)
        self.ascom_schedule.times_changed.connect(self._on_ascom_schedule_times)
        layout.addWidget(self.ascom_schedule)
        
        # --- White Balance (shared widget, NO ASI mode for ASCOM) ---
        self.ascom_wb = WhiteBalanceCard(show_asi_mode=False)
        self.ascom_wb.mode_changed.connect(self._on_ascom_wb_mode_changed)
        self.ascom_wb.manual_values_changed.connect(self._on_ascom_wb_manual_changed)
        self.ascom_wb.gray_world_changed.connect(self._on_ascom_wb_gray_world_changed)
        layout.addWidget(self.ascom_wb)
        
        layout.addStretch()
        return widget
    
    # =========================================================================
    # HELPER METHODS
    # =========================================================================
    def _browse_dir(self, line_edit: LineEdit):
        path = QFileDialog.getExistingDirectory(self, "Select Directory")
        if path:
            line_edit.setText(path)
    
    def _browse_sdk(self):
        # Platform-appropriate file filter for ZWO SDK
        if sys.platform == 'win32':
            file_filter = "DLL Files (*.dll)"
        elif sys.platform == 'darwin':
            file_filter = "Dynamic Libraries (*.dylib)"
        else:
            file_filter = "Shared Libraries (*.so)"
        
        path, _ = QFileDialog.getOpenFileName(
            self, "Select ZWO ASI SDK", "", 
            f"{file_filter};;All Files (*)"
        )
        if path:
            self.sdk_path_input.setText(path)
    
    def _save_config(self, key: str, value):
        if self._loading_config or not self.main_window:
            return
        self.main_window.config.set(key, value)
        self.settings_changed.emit()
    
    def _save_zwo_profile(self, **kwargs):
        """Save to camera profile and global config"""
        if self._loading_config or not self.main_window:
            return
        cam_name = self.main_window.config.get('zwo_selected_camera_name', '')
        if '(Index:' in cam_name:
            cam_name = cam_name.split('(Index:')[0].strip()
        if cam_name:
            self.main_window.config.update_camera_profile(cam_name, **kwargs)
        for k, v in kwargs.items():
            self.main_window.config.set(f'zwo_{k}', v)
        self.settings_changed.emit()
