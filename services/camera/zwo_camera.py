"""
ZWO ASI Camera capture module
Provides interface to capture images from ZWO cameras using the ASI SDK

This is the main public interface. Connection management is delegated to
camera_connection.py, calibration to camera_calibration.py, and the
backend-neutral lifecycle (schedule, capture thread, auto-exposure wrappers) to
capture_session_base.py.
"""
from .camera_connection import CameraConnection
from .camera_white_balance import configure_white_balance
from .capabilities import CameraCapabilities, zwo_capabilities
from .capture_session_base import CaptureSessionBase
from . import zwo_capture_worker


class ZWOCamera(CaptureSessionBase):
    """Interface to ZWO ASI camera using zwoasi library"""

    backend = 'zwo'

    def __init__(self, sdk_path=None, camera_index=0, exposure_sec=1.0, gain=100,
                 white_balance_r=75, white_balance_b=99, offset=20, flip=0,
                 auto_exposure=False, max_exposure_sec=30.0, auto_wb=False,
                 wb_mode='asi_auto', wb_config=None, bayer_pattern='BGGR',
                 scheduled_capture_enabled=False, scheduled_start_time="17:00",
                 scheduled_end_time="09:00", scheduled_capture_mode=None,
                 scheduled_window_interval=5.0,
                 status_callback=None, camera_name=None,
                 config_callback=None, camera_serial=None):
        # Sets the log callback FIRST (before CameraConnection uses self.log)
        super().__init__(
            exposure_sec=exposure_sec, gain=gain, auto_exposure=auto_exposure,
            max_exposure_sec=max_exposure_sec, auto_wb=auto_wb, wb_mode=wb_mode,
            wb_config=wb_config, bayer_pattern=bayer_pattern,
            scheduled_capture_enabled=scheduled_capture_enabled,
            scheduled_start_time=scheduled_start_time,
            scheduled_end_time=scheduled_end_time,
            scheduled_capture_mode=scheduled_capture_mode,
            scheduled_window_interval=scheduled_window_interval,
            status_callback=status_callback,
        )

        # Initialize connection manager (delegates SDK/connection logic)
        self._connection = CameraConnection(sdk_path=sdk_path, logger=self.log)
        self._connection.config_callback = config_callback
        self._connection.camera_name = camera_name
        self._connection.camera_index = camera_index
        self._connection.camera_serial = camera_serial

        # Legacy attribute aliases (for backward compatibility)
        self.sdk_path = sdk_path
        self.camera_index = camera_index
        self.camera_name = camera_name
        self.camera_serial = camera_serial  # Stable hardware identity (authoritative)
        self.config_callback = config_callback

        self.white_balance_r = white_balance_r
        self.white_balance_b = white_balance_b
        self.wb_mode = wb_mode  # 'asi_auto', 'manual', or 'gray_world'
        self.flip = flip  # 0=none, 1=horizontal, 2=vertical, 3=both
        self.offset = offset
        self.use_raw16 = False  # Use RAW16 mode for full bit depth (set by dev mode)
    
    # =========================================================================
    # Property aliases for backward compatibility (delegate to connection manager)
    # =========================================================================
    
    @property
    def camera(self):
        """Camera instance (delegated to connection manager)"""
        return self._connection.camera
    
    @camera.setter
    def camera(self, value):
        """Set camera instance"""
        self._connection.camera = value
    
    @property
    def asi(self):
        """ASI SDK instance (delegated to connection manager)"""
        return self._connection.asi
    
    @asi.setter
    def asi(self, value):
        """Set ASI SDK instance"""
        self._connection.asi = value
    
    @property
    def cameras(self):
        """List of detected cameras"""
        return self._connection.cameras
    
    @cameras.setter
    def cameras(self, value):
        """Set cameras list"""
        self._connection.cameras = value
    
    @property
    def supports_raw16(self) -> bool:
        """Whether camera supports RAW16 mode (delegated to connection manager)"""
        return self._connection.supports_raw16
    
    @property
    def sensor_bit_depth(self) -> int:
        """Camera's native ADC bit depth (delegated to connection manager)"""
        return self._connection.bit_depth
    
    @property
    def camera_info(self) -> dict:
        """Camera properties dict (delegated to connection manager)"""
        return self._connection.camera_info
    
    @property
    def current_bit_depth(self) -> int:
        """Current capture bit depth (8 for RAW8, 16 for RAW16)"""
        return self._connection.current_bit_depth

    @property
    def auto_recovery_enabled(self) -> bool:
        """Config 'camera_auto_recovery' — set by whoever builds the camera."""
        return self._connection.auto_recovery_enabled

    @auto_recovery_enabled.setter
    def auto_recovery_enabled(self, value):
        self._connection.auto_recovery_enabled = bool(value)

    @property
    def capabilities(self) -> CameraCapabilities:
        """ZWO capabilities, refined by the open connection."""
        return zwo_capabilities(self._connection)

    def __del__(self):
        """Destructor to ensure camera is disconnected when object is destroyed"""
        try:
            self.disconnect_camera()
        except Exception:
            pass  # Ignore errors during cleanup in destructor
    
    def __enter__(self):
        """Context manager entry - allows use with 'with' statement"""
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit - ensures cleanup even if exception occurs"""
        self.disconnect_camera()
        return False  # Don't suppress exceptions
        
    def initialize_sdk(self):
        """Initialize the ZWO ASI SDK (delegates to connection manager)"""
        # Update connection manager's logger to use our log method
        self._connection._logger = self.log
        return self._connection.initialize_sdk()
    
    def reset_sdk_completely(self):
        """
        Completely reset the SDK state (nuclear option).
        Delegates to connection manager.
        """
        return self._connection.reset_sdk_completely()
    
    def detect_cameras(self):
        """Detect connected ZWO cameras (delegates to connection manager)"""
        return self._connection.detect_cameras()
    
    def reconnect_camera_safe(self):
        """
        Safely reconnect to camera by re-detecting available cameras first.
        Delegates to connection manager, but handles calibration manager init.
        
        IMPORTANT: This passes current camera settings to ensure ROI and other
        settings are properly restored after reconnection. Without this, the
        camera may capture at wrong resolution causing reshape errors.
        """
        # Build settings dict from current properties to restore after reconnection
        settings = {
            'gain': self.gain,
            'exposure_sec': self.exposure_seconds,
            'wb_r': self.white_balance_r,
            'wb_b': self.white_balance_b,
            'wb_mode': self.wb_mode,
            'offset': self.offset,
            'flip': self.flip,
            'use_raw16': self.use_raw16,  # Preserve RAW16 mode after reconnection
        }
        
        success = self._connection.reconnect_safe(
            target_camera_name=self.camera_name,
            settings=settings,
            target_camera_serial=self.camera_serial
        )

        if success:
            # Sync identity from connection manager
            self.camera_name = self._connection.camera_name
            self.camera_index = self._connection.camera_index
            self.camera_serial = self._connection.camera_serial

            # Initialize calibration manager for the reconnected camera
            self._init_calibration_manager()
        
        return success
    
    def connect_camera(self, camera_index=0):
        """
        Connect to a specific camera.
        Delegates connection to connection manager, then initializes calibration.
        """
        # Build settings dict from our properties
        settings = {
            'gain': self.gain,
            'exposure_sec': self.exposure_seconds,
            'wb_r': self.white_balance_r,
            'wb_b': self.white_balance_b,
            'wb_mode': self.wb_mode,
            'offset': self.offset,
            'flip': self.flip,
            'use_raw16': self.use_raw16,  # RAW16 mode for full bit depth
        }
        
        # Delegate connection to connection manager. Pass expected name AND
        # serial so connect() rejects the wrong physical camera (a shifted
        # index can point the saved selection at a different body).
        success = self._connection.connect(
            camera_index, settings, expected_camera_name=self.camera_name,
            expected_camera_serial=self.camera_serial
        )

        # The saved index may point at the wrong body after a hot-plug; the
        # serial gate above then fails the open. Fall back to the serial-first
        # reconnect path, which probes serials to locate the correct index
        # rather than reconfiguring whichever camera the stale index hit.
        if not success and (self.camera_serial or self.camera_name):
            self.log("Direct connect failed — resolving camera by identity...")
            return self.reconnect_camera_safe()

        if success:
            # Sync identity from connection manager
            self.camera_name = self._connection.camera_name
            self.camera_index = self._connection.camera_index
            self.camera_serial = self._connection.camera_serial

            # Initialize calibration manager
            self._init_calibration_manager()

            self.log(f"✓ Camera connection successful")
            if self.scheduled_capture_enabled:
                self.log(f"Scheduled capture enabled: {self.scheduled_window_label()}")

        return success
    
    def _configure_camera(self):
        """Configure camera settings (delegates to connection manager)"""
        if not self.camera:
            return
        
        settings = {
            'gain': self.gain,
            'exposure_sec': self.exposure_seconds,
            'wb_r': self.white_balance_r,
            'wb_b': self.white_balance_b,
            'wb_mode': self.wb_mode,
            'offset': self.offset,
            'flip': self.flip,
            'use_raw16': self.use_raw16,  # RAW16 mode for full bit depth
        }
        self._connection.configure(settings)
    
    def set_raw16_mode(self, enabled: bool) -> bool:
        """
        Change RAW mode (RAW8/RAW16) during live capture.
        
        Args:
            enabled: True for RAW16, False for RAW8
            
        Returns:
            True if mode changed successfully
        """
        if not self.camera:
            self.log("Cannot change RAW mode: camera not connected")
            return False
        
        if enabled and not self.supports_raw16:
            self.log("Camera does not support RAW16 mode")
            return False
        
        try:
            with self._connection.sdk_lock:
                if not self.camera:
                    raise Exception("Camera disconnected before RAW mode change")

                # Verify we're talking to the right camera before writing settings
                if not self._connection.verify_identity():
                    raise Exception("Camera identity mismatch — aborting RAW mode change")

                # Update our setting
                self.use_raw16 = enabled

                # Get camera info for ROI
                camera_info = self.camera.get_camera_property()
                width = camera_info['MaxWidth']
                height = camera_info['MaxHeight']

                # Set new image type
                image_type = self.asi.ASI_IMG_RAW16 if enabled else self.asi.ASI_IMG_RAW8
                self.camera.set_roi(start_x=0, start_y=0, width=width, height=height,
                                    bins=1, image_type=image_type)
                self.camera.set_image_type(image_type)

                # Update connection manager state
                self._connection.current_image_type = image_type
                self._connection.current_bit_depth = 16 if enabled else 8

            # Update calibration manager bit depth (outside lock — no SDK calls)
            if self.calibration_manager:
                self.calibration_manager.bit_depth = self.current_bit_depth

            mode_str = "RAW16" if enabled else "RAW8"
            self.log(f"Switched to {mode_str} mode ({self.current_bit_depth}-bit capture)")
            return True
            
        except Exception as e:
            self.log(f"Error changing RAW mode: {e}")
            return False

    def disconnect_camera(self):
        """Disconnect from camera gracefully (idempotent - safe to call multiple times)"""
        # Stop capture first if active
        if self.is_capturing:
            self.log("Stopping active capture before disconnect...")
            self.stop_capture()
        
        # Clear exposure tracking before disconnect. The authoritative
        # stop_exposure() runs under sdk_lock inside CameraConnection.disconnect();
        # issuing one here (before that lock) would be an unsynchronized SDK call
        # racing the capture worker, which can corrupt the ZWO DLL. So only reset
        # the UI-facing tracking state here.
        def stop_exposure_callback():
            if self.exposure_start_time is not None:
                self.log("Clearing in-progress exposure tracking before disconnect...")
                self.exposure_start_time = None
                self.exposure_remaining = 0.0
        
        # Delegate to connection manager
        self._connection.disconnect(stop_exposure_callback=stop_exposure_callback)
        
        # Clear exposure tracking
        self.exposure_start_time = None
        self.exposure_remaining = 0.0
    
    def capture_single_frame(self):
        return zwo_capture_worker.capture_single_frame(self)

    def _set_control_live(self, control, value, success_msg=None) -> bool:
        """Apply a single control value to the live camera under sdk_lock."""
        return self._run_live(lambda cam: cam.set_control_value(control, value), success_msg)

    def _run_live(self, write, success_msg=None) -> bool:
        """Run ``write(camera)`` against the live camera under sdk_lock.

        Live setting writes originate on the UI thread while the capture worker
        is running; both touch the SDK, so they must serialize on sdk_lock or
        concurrent access can corrupt the ZWO DLL. A short timed acquire keeps
        the caller responsive if the worker is momentarily holding the lock.
        """
        if not self.camera or not self.asi:
            return False
        lock = self._connection.sdk_lock
        if not lock.acquire(timeout=2.0):
            self.log("⚠ Live setting skipped — SDK busy (capture worker holds lock)")
            return False
        try:
            if not self.camera:
                return False
            write(self.camera)
            if success_msg:
                self.log(success_msg)
            return True
        except Exception as e:
            self.log(f"Failed to apply live setting: {e}")
            return False
        finally:
            lock.release()

    def set_white_balance_live(self, mode, wb_r, wb_b):
        """Store the white balance and write it to the live camera.

        Every reconnect re-applies the stored values, so they must follow the
        profile — a stale R/B here came back on the next reconnect.
        """
        if (mode, wb_r, wb_b) == (self.wb_mode, self.white_balance_r, self.white_balance_b):
            return
        self.wb_mode, self.white_balance_r, self.white_balance_b = mode, wb_r, wb_b
        settings = {'wb_mode': mode, 'wb_r': wb_r, 'wb_b': wb_b}
        self._run_live(lambda cam: configure_white_balance(
            cam, self.asi, settings, self.log, controls=cam.get_controls()))

    def set_offset_live(self, offset):
        """Set brightness/offset, applying live to the camera under sdk_lock."""
        self.offset = offset
        if self.camera and self.asi:
            self._set_control_live(self.asi.ASI_BRIGHTNESS, offset)

    def set_flip_live(self, flip):
        """Set flip, applying live to the camera under sdk_lock."""
        self.flip = flip
        if self.camera and self.asi:
            self._set_control_live(self.asi.ASI_FLIP, flip)

    def update_exposure(self, exposure_seconds):
        """Update exposure setting and apply immediately to camera if connected"""
        self.exposure_seconds = exposure_seconds

        # If camera is connected and not in auto exposure mode, apply immediately
        # (sdk_lock-guarded — a live write racing the capture worker's SDK calls
        # can corrupt the ZWO DLL).
        if self.camera and not self.auto_exposure:
            self._set_control_live(
                self.asi.ASI_EXPOSURE, int(exposure_seconds * 1000000),
                f"Exposure updated to {exposure_seconds*1000:.2f}ms",
            )
