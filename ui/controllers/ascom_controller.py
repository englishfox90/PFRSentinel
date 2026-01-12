"""
ASCOM Camera Controller for Qt UI
Adapter between PySide6 UI and ASCOM camera adapter.

Provides the same interface as CameraControllerQt but uses ASCOM cameras.
"""
from PySide6.QtCore import QObject, Signal
from datetime import datetime

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from services.logger import app_logger
from services.camera.ascom import ASCOMCameraAdapter, check_ascom_availability


class ASCOMControllerQt(QObject):
    """
    Qt-compatible ASCOM camera controller.
    
    Uses ASCOMCameraAdapter.start_capture() with callbacks.
    All capture logic is handled by ASCOMCameraAdapter.
    """
    
    cameras_detected = Signal(list)  # List of camera names
    capture_started = Signal()
    capture_stopped = Signal()
    frame_ready = Signal(object, dict)  # PIL Image, metadata
    error = Signal(str)
    calibration_status = Signal(bool)  # True=calibrating, False=complete
    
    def __init__(self, main_window, parent=None):
        super().__init__(parent)
        self.main_window = main_window
        self.config = main_window.config
        
        self.ascom_adapter: ASCOMCameraAdapter = None
        self.is_connected = False
        self.is_capturing = False
    
    def detect_cameras(self):
        """Detect connected ASCOM cameras"""
        app_logger.info("Detecting ASCOM cameras...")
        
        info = check_ascom_availability()
        if not info['available']:
            self.error.emit(f"ASCOM not available: {info['error']}")
            return
        
        try:
            adapter = ASCOMCameraAdapter(config={
                'alpaca_host': self.config.get('ascom_host', 'localhost'),
                'alpaca_port': self.config.get('ascom_port', 11111),
            })
            adapter.initialize()
            cameras = adapter.detect_cameras()
            
            camera_list = [f"{cam.name} ({cam.backend})" for cam in cameras]
            self.cameras_detected.emit(camera_list)
            app_logger.info(f"Detected {len(camera_list)} ASCOM camera(s)")
            
        except Exception as e:
            self.error.emit(f"Detection failed: {e}")
            app_logger.error(f"ASCOM camera detection failed: {e}")
    
    def start_capture(self):
        """Start ASCOM camera capture"""
        if self.is_capturing:
            return
        
        try:
            info = check_ascom_availability()
            if not info['available']:
                raise Exception(f"ASCOM not available: {info['error']}")
            
            camera_index = self.config.get('ascom_selected_camera', 0)
            exposure_ms = self.config.get('ascom_exposure_ms', 1000.0)
            gain = self.config.get('ascom_gain', 0)
            interval = self.config.get('ascom_interval', 5.0)
            
            # Auto-exposure settings
            auto_exposure = self.config.get('ascom_auto_exposure', False)
            target_brightness = self.config.get('ascom_target_brightness', 100)
            max_exposure_ms = self.config.get('ascom_max_exposure_ms', 30000.0)
            
            # Scheduled capture settings
            scheduled_enabled = self.config.get('ascom_scheduled_enabled', False)
            scheduled_start = self.config.get('ascom_scheduled_start', '17:00')
            scheduled_end = self.config.get('ascom_scheduled_end', '09:00')
            
            # White balance settings
            wb_mode = self.config.get('ascom_wb_mode', 'manual')
            wb_r = self.config.get('ascom_wb_r', 50)
            wb_b = self.config.get('ascom_wb_b', 50)
            wb_gw_low = self.config.get('ascom_wb_gw_low', 5)
            wb_gw_high = self.config.get('ascom_wb_gw_high', 95)
            
            app_logger.info(f"Starting ASCOM capture: camera={camera_index}, exposure={exposure_ms}ms, auto_exp={auto_exposure}")
            
            # Clean up existing adapter
            if self.ascom_adapter is not None:
                try:
                    self.ascom_adapter.disconnect()
                except Exception:
                    pass
                self.ascom_adapter = None
            
            # Create new adapter with config
            self.ascom_adapter = ASCOMCameraAdapter(
                config={
                    'alpaca_host': self.config.get('ascom_host', 'localhost'),
                    'alpaca_port': self.config.get('ascom_port', 11111),
                    'alpaca_device_number': camera_index,
                    'ascom_device_id': self.config.get('ascom_device_id', ''),
                },
                logger=lambda msg: app_logger.debug(f"[ASCOM] {msg}")
            )
            
            # Initialize and connect
            if not self.ascom_adapter.initialize():
                raise Exception("Failed to initialize ASCOM adapter")
            
            if not self.ascom_adapter.connect(camera_index):
                raise Exception("Failed to connect to ASCOM camera")
            
            self.is_connected = True
            
            # Configure all settings including scheduled capture and white balance
            self.ascom_adapter.configure({
                'exposure_ms': exposure_ms,
                'gain': gain,
                'scheduled_enabled': scheduled_enabled,
                'schedule_start': scheduled_start,
                'schedule_end': scheduled_end,
                'wb_mode': wb_mode,
                'wb_r': wb_r,
                'wb_b': wb_b,
                'wb_gw_low': wb_gw_low,
                'wb_gw_high': wb_gw_high,
            })
            
            # Configure auto-exposure
            self.ascom_adapter.set_auto_exposure(
                enabled=auto_exposure,
                target_brightness=target_brightness,
                max_exposure_ms=max_exposure_ms
            )
            
            # Store white balance settings for reference
            self._wb_config = {
                'mode': wb_mode,
                'wb_r': wb_r,
                'wb_b': wb_b,
                'gw_low': wb_gw_low,
                'gw_high': wb_gw_high,
            }
            
            # Store scheduled capture settings
            self._scheduled_config = {
                'enabled': scheduled_enabled,
                'start': scheduled_start,
                'end': scheduled_end,
            }
            
            # Set callbacks
            self.ascom_adapter.set_calibration_callback(self._on_calibration_status)
            
            # Start capture with frame callback
            if not self.ascom_adapter.start_capture(
                on_frame=self._on_frame,
                on_error=self._on_error,
                interval_sec=interval
            ):
                raise Exception("Failed to start capture")
            
            self.is_capturing = True
            self.capture_started.emit()
            app_logger.info("ASCOM capture started successfully")
            
        except Exception as e:
            self.error.emit(str(e))
            app_logger.error(f"Failed to start ASCOM capture: {e}")
            self.is_capturing = False
            self.is_connected = False
    
    def stop_capture(self):
        """Stop ASCOM camera capture"""
        if not self.is_capturing:
            return
        
        try:
            if self.ascom_adapter:
                self.ascom_adapter.stop_capture()
                self.ascom_adapter.disconnect()
                self.ascom_adapter = None
            
            self.is_capturing = False
            self.is_connected = False
            self.capture_stopped.emit()
            app_logger.info("ASCOM capture stopped")
            
        except Exception as e:
            app_logger.error(f"Error stopping ASCOM capture: {e}")
    
    def _on_frame(self, image, metadata):
        """Handle captured frame from ASCOM adapter"""
        try:
            # Add UI-specific metadata fields (like ZWO controller does)
            if metadata is None:
                metadata = {}
            metadata['filename'] = f"capture_{datetime.now().strftime('%H%M%S')}.jpg"
            metadata['timestamp'] = datetime.now().strftime('%H:%M:%S')
            
            # Add numeric exposure for UI (parse from EXPOSURE string like "1000ms")
            exposure_str = metadata.get('EXPOSURE', '')
            if exposure_str and isinstance(exposure_str, str):
                try:
                    if exposure_str.endswith('ms'):
                        # Convert ms to seconds for UI
                        metadata['exposure'] = float(exposure_str[:-2]) / 1000.0
                    elif exposure_str.endswith('s'):
                        metadata['exposure'] = float(exposure_str[:-1])
                except (ValueError, TypeError):
                    pass
            
            # Process image through the main window's image pipeline
            if self.main_window:
                self.main_window.on_image_captured(image, metadata)
            
            self.frame_ready.emit(image, metadata)
            
        except Exception as e:
            app_logger.error(f"Error handling ASCOM frame: {e}")
    
    def _on_error(self, error_msg):
        """Handle error from ASCOM adapter"""
        app_logger.error(f"ASCOM error: {error_msg}")
        self.error.emit(error_msg)
    
    def _on_calibration_status(self, is_calibrating):
        """Handle calibration status change"""
        self.calibration_status.emit(is_calibrating)
