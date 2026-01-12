"""
Capture Settings Panel - Event Handlers (Part 2)

This file contains the event handler methods for the CaptureSettingsPanel.
It's imported and methods are added to the class.
"""


class CaptureSettingsHandlers:
    """Mixin class with event handler methods"""
    
    # =========================================================================
    # WATCH MODE HANDLERS
    # =========================================================================
    def _on_watch_dir_changed(self, text):
        self._save_config('watch_directory', text)
    
    def _on_recursive_changed(self, checked):
        self._save_config('watch_recursive', checked)
    
    # =========================================================================
    # ZWO HANDLERS
    # =========================================================================
    def _on_sdk_path_changed(self, text):
        self._save_config('zwo_sdk_path', text)
    
    def _on_camera_selected(self, index):
        if self._loading_config or not self.main_window:
            return
        name = self.camera_combo.currentText()
        actual_idx = index
        if '(Index: ' in name:
            try:
                actual_idx = int(name.split('(Index: ')[1].rstrip(')'))
            except (IndexError, ValueError):
                pass
        self.main_window.config.set('zwo_selected_camera', actual_idx)
        self.main_window.config.set('zwo_selected_camera_name', name)
        self.settings_changed.emit()
    
    def _on_zwo_exposure_changed(self, value):
        self._save_zwo_profile(exposure_ms=value * 1000)
    
    def _on_zwo_gain_changed(self, value):
        self._save_zwo_profile(gain=value)
    
    def _on_zwo_interval_changed(self, value):
        self._save_config('zwo_interval', value)
    
    def _on_zwo_auto_exp_enabled(self, checked):
        self._save_zwo_profile(auto_exposure=checked)
    
    def _on_zwo_target_brightness(self, value):
        self._save_zwo_profile(target_brightness=value)
    
    def _on_zwo_max_exposure(self, value):
        self._save_zwo_profile(max_exposure_ms=value * 1000)
    
    def _on_schedule_enabled(self, checked):
        self._save_config('scheduled_capture_enabled', checked)
    
    def _on_schedule_times(self, start: str, end: str):
        if self._loading_config or not self.main_window:
            return
        self.main_window.config.set('scheduled_start_time', start)
        self.main_window.config.set('scheduled_end_time', end)
        self.settings_changed.emit()
    
    def _on_wb_mode_changed(self, mode: str):
        if self._loading_config or not self.main_window:
            return
        wb = self.main_window.config.get('white_balance', {})
        wb['mode'] = mode
        self.main_window.config.set('white_balance', wb)
        self.settings_changed.emit()
    
    def _on_wb_manual_changed(self, red: int, blue: int):
        self._save_zwo_profile(wb_r=red, wb_b=blue)
    
    def _on_wb_gray_world_changed(self, low: int, high: int):
        if self._loading_config or not self.main_window:
            return
        wb = self.main_window.config.get('white_balance', {})
        wb['gray_world_low_pct'] = low
        wb['gray_world_high_pct'] = high
        self.main_window.config.set('white_balance', wb)
        self.settings_changed.emit()
    
    def _on_offset_changed(self, value):
        self._save_zwo_profile(offset=value)
    
    def _on_flip_changed(self, index):
        self._save_zwo_profile(flip=index)
    
    def _on_bayer_changed(self, index):
        patterns = ["BGGR", "RGGB", "GRBG", "GBRG"]
        self._save_zwo_profile(bayer_pattern=patterns[index])
    
    def _on_raw16_changed(self, checked):
        if self._loading_config or not self.main_window:
            return
        dev_mode = self.main_window.config.get('dev_mode', {})
        dev_mode['use_raw16'] = checked
        self.main_window.config.set('dev_mode', dev_mode)
        self.main_window.config.save()
        self.settings_changed.emit()
        self.raw16_mode_changed.emit(checked)
    
    # =========================================================================
    # ASCOM HANDLERS
    # =========================================================================
    def _on_ascom_host_changed(self, text):
        self._save_config('ascom_host', text)
    
    def _on_ascom_port_changed(self, value):
        self._save_config('ascom_port', value)
    
    def _on_ascom_detect_cameras(self):
        from services.camera.ascom import ASCOMCameraAdapter, check_ascom_availability
        from qfluentwidgets import InfoBar, InfoBarPosition
        
        if not check_ascom_availability()['available']:
            InfoBar.error(title="ASCOM Not Available", content="Install alpyca",
                          parent=self, position=InfoBarPosition.TOP, duration=5000)
            return
        
        self.ascom_detect_btn.setEnabled(False)
        self.ascom_detect_btn.setText("Detecting...")
        try:
            adapter = ASCOMCameraAdapter(config={
                'alpaca_host': self.ascom_host_input.text() or 'localhost',
                'alpaca_port': self.ascom_port_spin.value(),
            })
            adapter.initialize()
            cameras = adapter.detect_cameras()
            
            self.ascom_camera_combo.clear()
            if cameras:
                for cam in cameras:
                    self.ascom_camera_combo.addItem(cam.name, cam.device_id)
                InfoBar.success(title="Found", content=f"{len(cameras)} camera(s)",
                                parent=self, position=InfoBarPosition.TOP, duration=3000)
            else:
                self.ascom_camera_combo.setPlaceholderText("No cameras found")
        except Exception as e:
            InfoBar.error(title="Error", content=str(e), parent=self,
                          position=InfoBarPosition.TOP, duration=5000)
        finally:
            self.ascom_detect_btn.setEnabled(True)
            self.ascom_detect_btn.setText("Detect")
    
    def _on_ascom_camera_selected(self, index):
        if self._loading_config or index < 0 or not self.main_window:
            return
        device_id = self.ascom_camera_combo.itemData(index)
        self.main_window.config.set('ascom_device_id', device_id)
        self.main_window.config.set('ascom_selected_camera', index)
        self.settings_changed.emit()
    
    def _on_ascom_exposure_changed(self, value):
        self._save_config('ascom_exposure_ms', value * 1000)
    
    def _on_ascom_gain_changed(self, value):
        self._save_config('ascom_gain', value)
    
    def _on_ascom_interval_changed(self, value):
        self._save_config('ascom_interval', value)
    
    def _on_ascom_auto_exp_enabled(self, checked):
        self._save_config('ascom_auto_exposure', checked)
    
    def _on_ascom_target_brightness(self, value):
        self._save_config('ascom_target_brightness', value)
    
    def _on_ascom_max_exposure(self, value):
        self._save_config('ascom_max_exposure_ms', value * 1000)
    
    def _on_ascom_schedule_enabled(self, checked):
        self._save_config('ascom_scheduled_enabled', checked)
    
    def _on_ascom_schedule_times(self, start: str, end: str):
        if self._loading_config or not self.main_window:
            return
        self.main_window.config.set('ascom_scheduled_start', start)
        self.main_window.config.set('ascom_scheduled_end', end)
        self.settings_changed.emit()
    
    def _on_ascom_wb_mode_changed(self, mode: str):
        self._save_config('ascom_wb_mode', mode)
    
    def _on_ascom_wb_manual_changed(self, red: int, blue: int):
        if self._loading_config or not self.main_window:
            return
        self.main_window.config.set('ascom_wb_r', red)
        self.main_window.config.set('ascom_wb_b', blue)
        self.settings_changed.emit()
    
    def _on_ascom_wb_gray_world_changed(self, low: int, high: int):
        if self._loading_config or not self.main_window:
            return
        self.main_window.config.set('ascom_wb_gw_low', low)
        self.main_window.config.set('ascom_wb_gw_high', high)
        self.settings_changed.emit()
    
    # =========================================================================
    # PUBLIC METHODS
    # =========================================================================
    def set_cameras(self, camera_list: list):
        self.camera_combo.blockSignals(True)
        self.camera_combo.clear()
        if camera_list:
            self.camera_combo.addItems(camera_list)
        else:
            self.camera_combo.setPlaceholderText("No cameras detected")
        self.camera_combo.blockSignals(False)
    
    def set_detecting(self, is_detecting: bool):
        self.detect_btn.setEnabled(not is_detecting)
        self.detect_btn.setText("Detecting..." if is_detecting else "Detect")
    
    def set_detection_error(self, error: str):
        from qfluentwidgets import InfoBar, InfoBarPosition
        InfoBar.error(title="Detection Failed", content=error, parent=self,
                      position=InfoBarPosition.TOP, duration=5000)
    
    def update_camera_capabilities(self, supports_raw16: bool, bit_depth: int):
        from ..theme.tokens import Colors
        self._loading_config = True
        try:
            if supports_raw16:
                self.raw16_switch.setEnabled(True)
                self.raw16_status.setText(f"✓ Camera supports RAW16 ({bit_depth}-bit ADC)")
                self.raw16_status.setStyleSheet(f"color: {Colors.success_text}; padding: 4px 8px;")
                if self.main_window:
                    dev = self.main_window.config.get('dev_mode', {})
                    self.raw16_switch.set_checked(dev.get('use_raw16', False))
            else:
                self.raw16_switch.setEnabled(False)
                self.raw16_switch.set_checked(False)
                self.raw16_status.setText(f"✗ No RAW16 ({bit_depth}-bit, RAW8 only)")
                self.raw16_status.setStyleSheet(f"color: {Colors.text_secondary}; padding: 4px 8px;")
        finally:
            self._loading_config = False
    
    def reset_camera_capabilities(self):
        from ..theme.tokens import Colors
        self._loading_config = True
        try:
            self.raw16_switch.setEnabled(False)
            self.raw16_switch.set_checked(False)
            self.raw16_status.setText("Connect camera to check RAW16 support")
            self.raw16_status.setStyleSheet(f"color: {Colors.text_secondary}; padding: 4px 8px;")
        finally:
            self._loading_config = False
    
    def load_from_config(self, config):
        """Load settings from config"""
        self._loading_config = True
        try:
            # Mode
            mode = config.get('capture_mode', 'camera')
            self.mode_selector.setCurrentItem(mode)
            self.settings_stack.setCurrentIndex({'watch': 0, 'camera': 1, 'ascom': 2}.get(mode, 1))
            
            # Watch
            self.watch_dir_input.setText(config.get('watch_directory', ''))
            self.recursive_switch.set_checked(config.get('watch_recursive', True))
            
            # ZWO
            self.sdk_path_input.setText(config.get('zwo_sdk_path', ''))
            self.exposure_spin.setValue(config.get('zwo_exposure_ms', 100.0) / 1000.0)
            self.gain_spin.setValue(config.get('zwo_gain', 100))
            self.interval_spin.setValue(config.get('zwo_interval', 5.0))
            
            # ZWO Auto Exposure
            self.zwo_auto_exp.set_values(
                config.get('zwo_auto_exposure', False),
                config.get('zwo_target_brightness', 100),
                config.get('zwo_max_exposure_ms', 30000.0) / 1000.0
            )
            
            # ZWO Schedule
            self.zwo_schedule.set_values(
                config.get('scheduled_capture_enabled', False),
                config.get('scheduled_start_time', '17:00'),
                config.get('scheduled_end_time', '09:00')
            )
            
            # ZWO White Balance
            wb = config.get('white_balance', {})
            self.zwo_wb.set_values(
                wb.get('mode', 'asi_auto'),
                config.get('zwo_wb_r', 75),
                config.get('zwo_wb_b', 99),
                wb.get('gray_world_low_pct', 5),
                wb.get('gray_world_high_pct', 95)
            )
            
            # ZWO Advanced
            self.offset_spin.setValue(config.get('zwo_offset', 20))
            self.flip_combo.setCurrentIndex(config.get('zwo_flip', 0))
            bayer = config.get('zwo_bayer_pattern', 'BGGR')
            patterns = ["BGGR", "RGGB", "GRBG", "GBRG"]
            if bayer in patterns:
                self.bayer_combo.setCurrentIndex(patterns.index(bayer))
            
            # ASCOM Connection
            self.ascom_host_input.setText(config.get('ascom_host', 'localhost'))
            self.ascom_port_spin.setValue(config.get('ascom_port', 11111))
            self.ascom_exposure_spin.setValue(config.get('ascom_exposure_ms', 1000.0) / 1000.0)
            self.ascom_gain_spin.setValue(config.get('ascom_gain', 100))
            self.ascom_interval_spin.setValue(config.get('ascom_interval', 5.0))
            
            # ASCOM Auto Exposure
            self.ascom_auto_exp.set_values(
                config.get('ascom_auto_exposure', False),
                config.get('ascom_target_brightness', 100),
                config.get('ascom_max_exposure_ms', 30000.0) / 1000.0
            )
            
            # ASCOM Schedule
            self.ascom_schedule.set_values(
                config.get('ascom_scheduled_enabled', False),
                config.get('ascom_scheduled_start', '17:00'),
                config.get('ascom_scheduled_end', '09:00')
            )
            
            # ASCOM White Balance
            self.ascom_wb.set_values(
                config.get('ascom_wb_mode', 'manual'),
                config.get('ascom_wb_r', 50),
                config.get('ascom_wb_b', 50),
                config.get('ascom_wb_gw_low', 5),
                config.get('ascom_wb_gw_high', 95)
            )
        finally:
            self._loading_config = False
