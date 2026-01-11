"""
ASCOM Camera Adapter

Provides camera interface for ASCOM-compatible astronomy cameras.
ASCOM (Astronomy Common Object Model) is a Windows COM-based standard
for astronomy equipment interoperability.

Requirements:
    - Windows OS
    - ASCOM Platform 6.6 or later
    - Camera-specific ASCOM driver installed
    
This is a STUB implementation. Full ASCOM support requires:
1. Installing alpyca or comtypes for COM interface
2. Testing with physical ASCOM devices
3. Implementing device-specific quirks
"""
import os
import time
import threading
from typing import Optional, Dict, Any, List, Callable, Tuple
from datetime import datetime
from PIL import Image

from .interface import (
    CameraInterface,
    CameraCapabilities,
    CameraInfo,
    CameraState,
    CaptureResult,
)

# ASCOM availability check
ASCOM_AVAILABLE = False
ASCOM_ERROR = None

try:
    # Try alpyca first (pure Python ASCOM Alpaca client)
    import alpyca
    from alpyca.camera import Camera as AlpacaCamera
    ASCOM_AVAILABLE = True
    ASCOM_BACKEND = "alpyca"
except ImportError:
    try:
        # Fall back to comtypes for COM interface
        import comtypes
        import comtypes.client
        ASCOM_AVAILABLE = True
        ASCOM_BACKEND = "comtypes"
    except ImportError:
        ASCOM_ERROR = "Neither alpyca nor comtypes available. Install: pip install alpyca"
        ASCOM_BACKEND = None


class ASCOMCameraAdapter(CameraInterface):
    """
    Adapter for ASCOM-compatible cameras.
    
    This adapter supports cameras via:
    1. ASCOM Alpaca (network/REST API) - preferred
    2. ASCOM COM (Windows COM interface) - fallback
    
    ASCOM provides a standardized interface for astronomy cameras,
    supporting features like cooling, filter wheels, and more.
    
    STATUS: STUB IMPLEMENTATION
    This adapter defines the interface but is not fully functional.
    See docs/ASCOM_SUPPORT.md for implementation roadmap.
    """
    
    # Base ASCOM capabilities (will be refined per-device)
    _BASE_CAPABILITIES = CameraCapabilities(
        backend_name="ASCOM",
        backend_version="0.1.0",  # Stub version
        
        # Connection
        supports_hot_plug=False,  # ASCOM doesn't support hot plug
        supports_multiple_cameras=True,
        requires_sdk_path=False,  # Uses system ASCOM platform
        
        # Exposure - Generally supported
        supports_exposure_control=True,
        min_exposure_ms=1,  # 1ms typical minimum
        max_exposure_ms=3600000,  # 1 hour typical max
        supports_auto_exposure=False,  # Rarely in ASCOM
        
        # Gain - Device dependent
        supports_gain_control=True,  # Most ASCOM cameras
        min_gain=0,
        max_gain=100,  # Will be updated per camera
        supports_auto_gain=False,
        
        # White Balance - Rarely available in ASCOM
        supports_white_balance=False,
        supports_auto_white_balance=False,
        min_wb_value=0,
        max_wb_value=0,
        
        # Image Settings
        supports_binning=True,
        max_binning=4,
        supports_roi=True,  # Subframe
        supports_flip=False,  # Not standard in ASCOM
        supports_offset=True,  # Some cameras
        
        # Bit Depth
        supports_raw8=True,
        supports_raw16=True,
        native_bit_depth=16,  # Most astro cameras are 16-bit
        
        # Color/Bayer - Device dependent
        is_color_camera=False,  # Set per camera
        bayer_pattern=None,
        supports_debayering=False,  # Usually done in software
        
        # Temperature - Common in astro cameras
        supports_temperature_reading=True,
        supports_cooling=True,
        supports_cooler_control=True,
        
        # Scheduling
        supports_scheduled_capture=False,
        
        # Metadata
        provides_metadata=True,
        metadata_fields=['CAMERA', 'EXPOSURE', 'GAIN', 'TEMP', 'RES', 'DATETIME'],
        
        # Performance
        supports_streaming=False,  # ASCOM is typically single-shot
        max_fps=1,  # Limited by readout and exposure
    )
    
    def __init__(
        self,
        config: Optional[Dict[str, Any]] = None,
        logger: Optional[Callable[[str], None]] = None,
    ):
        """
        Initialize ASCOM adapter.
        
        Args:
            config: Config dict with ASCOM-specific settings
            logger: Callback for log messages
        """
        self._config = config or {}
        self._logger = logger
        
        # Internal state
        self._state = CameraState.DISCONNECTED
        self._camera_info: Optional[CameraInfo] = None
        self._capabilities = self._BASE_CAPABILITIES
        
        # ASCOM objects (set when connected)
        self._camera = None
        self._device_id: Optional[str] = None
        
        # Callbacks
        self._on_frame: Optional[Callable[[Image.Image, Dict[str, Any]], None]] = None
        self._on_error: Optional[Callable[[str], None]] = None
        self._state_callback: Optional[Callable[[CameraState], None]] = None
        self._calibration_callback: Optional[Callable[[bool], None]] = None
        
        # Capture state
        self._capture_thread: Optional[threading.Thread] = None
        self._capture_running = False
        self._capture_interval = 5.0
        
        # Current settings
        self._exposure_ms = 1000.0
        self._gain = 0
        self._bin_x = 1
        self._bin_y = 1
        
        # Last image storage
        self._last_image: Optional[Image.Image] = None
        self._last_metadata: Optional[Dict[str, Any]] = None
        self._image_lock = threading.Lock()
    
    def _log(self, message: str) -> None:
        """Log message via callback"""
        if self._logger:
            self._logger(message)
        else:
            print(f"[ASCOM] {message}")
    
    def _set_state(self, state: CameraState) -> None:
        """Update state and notify callback"""
        self._state = state
        if self._state_callback:
            self._state_callback(state)
    
    # =========================================================================
    # Properties
    # =========================================================================
    
    @property
    def state(self) -> CameraState:
        return self._state
    
    @property
    def capabilities(self) -> CameraCapabilities:
        return self._capabilities
    
    @property
    def camera_info(self) -> Optional[CameraInfo]:
        return self._camera_info
    
    @property
    def is_connected(self) -> bool:
        return self._camera is not None
    
    @property
    def is_capturing(self) -> bool:
        return self._capture_running
    
    # =========================================================================
    # Initialization & Detection
    # =========================================================================
    
    def initialize(self) -> bool:
        """Initialize ASCOM subsystem"""
        self._log("Initializing ASCOM backend...")
        
        if not ASCOM_AVAILABLE:
            self._log(f"ASCOM not available: {ASCOM_ERROR}")
            return False
        
        self._log(f"ASCOM backend: {ASCOM_BACKEND}")
        return True
    
    def detect_cameras(self) -> List[CameraInfo]:
        """
        Detect available ASCOM cameras.
        
        Note: ASCOM camera enumeration varies by backend:
        - Alpaca: Query Alpaca server for camera devices
        - COM: Use ASCOM Chooser or enumerate registered drivers
        
        STUB: Returns empty list. Full implementation requires
        actual ASCOM device enumeration.
        """
        if not ASCOM_AVAILABLE:
            self._log("Cannot detect: ASCOM not available")
            return []
        
        self._log("ASCOM camera detection not yet implemented")
        self._log("Use 'ascom_device_id' config to specify camera manually")
        
        # TODO: Implement actual ASCOM device enumeration
        # For Alpaca:
        #   - Query server at config['alpaca_host']:config['alpaca_port']
        #   - List all Camera devices (0, 1, 2, ...)
        # For COM:
        #   - Use ASCOM Profile to list registered Camera drivers
        #   - Or launch ASCOM Chooser dialog
        
        return []
    
    # =========================================================================
    # Connection Management
    # =========================================================================
    
    def connect(self, camera_index: int = 0, settings: Optional[Dict[str, Any]] = None) -> bool:
        """
        Connect to ASCOM camera.
        
        Args:
            camera_index: Index from detect_cameras() or config device ID
            settings: Optional initial settings
            
        STUB: Not yet implemented.
        """
        if not ASCOM_AVAILABLE:
            self._log(f"Cannot connect: {ASCOM_ERROR}")
            return False
        
        self._set_state(CameraState.CONNECTING)
        
        device_id = self._config.get('ascom_device_id', '')
        if not device_id:
            self._log("No ASCOM device ID configured")
            self._log("Set 'ascom_device_id' in config or use ASCOM Chooser")
            self._set_state(CameraState.ERROR)
            return False
        
        # TODO: Implement actual ASCOM connection
        # For Alpaca:
        #   self._camera = AlpacaCamera(host, port, camera_index)
        #   self._camera.Connected = True
        # For COM:
        #   self._camera = comtypes.client.CreateObject(device_id)
        #   self._camera.Connected = True
        
        self._log("ASCOM camera connection not yet implemented")
        self._set_state(CameraState.ERROR)
        return False
    
    def disconnect(self) -> None:
        """Disconnect from ASCOM camera"""
        if self._capture_running:
            self.stop_capture()
        
        if self._camera:
            try:
                # self._camera.Connected = False
                pass
            except Exception as e:
                self._log(f"Error disconnecting: {e}")
        
        self._camera = None
        self._camera_info = None
        self._device_id = None
        self._set_state(CameraState.DISCONNECTED)
        self._log("Disconnected from ASCOM camera")
    
    def reconnect(self) -> bool:
        """Attempt to reconnect to last camera"""
        if self._device_id:
            return self.connect(0)
        return False
    
    # =========================================================================
    # Configuration
    # =========================================================================
    
    def configure(self, settings: Dict[str, Any]) -> None:
        """
        Configure camera settings.
        
        Supported settings:
            - exposure: Exposure time in milliseconds
            - gain: Camera gain (0-max)
            - bin_x, bin_y: Binning factors
            - cooler_on: Enable/disable cooler
            - cooler_setpoint: Target temperature (°C)
            - offset: ADC offset
        """
        if 'exposure' in settings:
            self._exposure_ms = float(settings['exposure'])
        if 'gain' in settings:
            self._gain = int(settings['gain'])
        if 'bin_x' in settings:
            self._bin_x = int(settings['bin_x'])
        if 'bin_y' in settings:
            self._bin_y = int(settings['bin_y'])
        
        # Apply to camera if connected
        if self._camera:
            self._apply_settings()
    
    def _apply_settings(self) -> None:
        """Apply current settings to camera"""
        if not self._camera:
            return
        
        # TODO: Apply settings to ASCOM camera
        # self._camera.BinX = self._bin_x
        # self._camera.BinY = self._bin_y
        # self._camera.Gain = self._gain
        pass
    
    def get_current_settings(self) -> Dict[str, Any]:
        """Get current camera settings"""
        return {
            'exposure': self._exposure_ms,
            'gain': self._gain,
            'bin_x': self._bin_x,
            'bin_y': self._bin_y,
        }
    
    # =========================================================================
    # Capture Operations
    # =========================================================================
    
    def capture_frame(self) -> CaptureResult:
        """
        Capture a single frame from ASCOM camera.
        
        STUB: Returns error. Full implementation requires ASCOM exposure.
        """
        if not self._camera:
            return CaptureResult(success=False, error="Camera not connected")
        
        # TODO: Implement ASCOM exposure
        # exposure_sec = self._exposure_ms / 1000.0
        # self._camera.StartExposure(exposure_sec, True)
        # while not self._camera.ImageReady:
        #     time.sleep(0.1)
        # image_data = self._camera.ImageArray
        # ... convert to PIL Image ...
        
        return CaptureResult(
            success=False,
            error="ASCOM capture not yet implemented"
        )
    
    def start_capture(
        self,
        on_frame: Callable[[Image.Image, Dict[str, Any]], None],
        on_error: Optional[Callable[[str], None]] = None,
        interval_sec: float = 5.0
    ) -> bool:
        """Start continuous capture loop"""
        if not self._camera:
            self._log("Cannot start capture: camera not connected")
            return False
        
        self._on_frame = on_frame
        self._on_error = on_error
        self._capture_interval = interval_sec
        self._capture_running = True
        
        self._capture_thread = threading.Thread(
            target=self._capture_loop,
            daemon=True,
            name="ASCOM-Capture"
        )
        self._capture_thread.start()
        
        self._set_state(CameraState.CAPTURING)
        return True
    
    def stop_capture(self) -> None:
        """Stop continuous capture"""
        self._capture_running = False
        
        if self._capture_thread:
            self._capture_thread.join(timeout=5.0)
            self._capture_thread = None
        
        if self._camera:
            self._set_state(CameraState.CONNECTED)
        else:
            self._set_state(CameraState.DISCONNECTED)
    
    def _capture_loop(self) -> None:
        """Background capture loop"""
        while self._capture_running:
            try:
                result = self.capture_frame()
                
                if result.success and result.image and self._on_frame:
                    with self._image_lock:
                        self._last_image = result.image.copy()
                        self._last_metadata = result.metadata.copy() if result.metadata else {}
                    
                    self._on_frame(result.image, result.metadata or {})
                elif not result.success and self._on_error:
                    self._on_error(result.error or "Unknown capture error")
                
                # Wait for next capture
                time.sleep(self._capture_interval)
                
            except Exception as e:
                self._log(f"Capture loop error: {e}")
                if self._on_error:
                    self._on_error(str(e))
                time.sleep(1)
    
    # =========================================================================
    # Auto Exposure & Calibration
    # =========================================================================
    
    def set_auto_exposure(
        self,
        enabled: bool,
        target_brightness: int = 100,
        max_exposure_ms: float = 30000.0
    ) -> bool:
        """
        Enable/disable software auto exposure.
        
        Note: ASCOM doesn't have native auto exposure, so this would need
        to be implemented in software by analyzing captured images.
        
        STUB: Not implemented.
        """
        self._log("Auto exposure for ASCOM not yet implemented")
        return False
    
    def run_calibration(self) -> bool:
        """
        Run exposure calibration.
        
        STUB: Not implemented.
        """
        self._log("Calibration for ASCOM not yet implemented")
        return False
    
    # =========================================================================
    # Temperature & Cooling
    # =========================================================================
    
    def get_temperature(self) -> Optional[Dict[str, Any]]:
        """
        Get camera temperature info.
        
        Returns dict with:
            - sensor_temp: CCD temperature (°C)
            - cooler_on: Whether cooler is active
            - cooler_setpoint: Target temperature
            - cooler_power: Cooler power percentage
        """
        if not self._camera:
            return None
        
        # TODO: Read from ASCOM camera
        # return {
        #     'sensor_temp': self._camera.CCDTemperature,
        #     'cooler_on': self._camera.CoolerOn,
        #     'cooler_setpoint': self._camera.SetCCDTemperature,
        #     'cooler_power': self._camera.CoolerPower,
        # }
        
        return None
    
    # =========================================================================
    # Callbacks
    # =========================================================================
    
    def set_log_callback(self, callback: Optional[Callable[[str], None]]) -> None:
        """Set log callback"""
        self._logger = callback
    
    def set_state_callback(self, callback: Optional[Callable[[CameraState], None]]) -> None:
        """Set state change callback"""
        self._state_callback = callback
    
    def set_calibration_callback(self, callback: Optional[Callable[[bool], None]]) -> None:
        """Set calibration callback"""
        self._calibration_callback = callback
    
    # =========================================================================
    # Exposure Progress
    # =========================================================================
    
    def get_exposure_progress(self) -> Tuple[float, float]:
        """
        Get current exposure progress.
        
        Returns (elapsed_sec, total_sec) tuple.
        
        STUB: Not implemented.
        """
        if not self._camera:
            return (0.0, 0.0)
        
        # TODO: ASCOM cameras report PercentCompleted
        # percent = self._camera.PercentCompleted / 100.0
        # total_sec = self._exposure_ms / 1000.0
        # elapsed = percent * total_sec
        # return (elapsed, total_sec)
        
        return (0.0, 0.0)


def check_ascom_availability() -> Dict[str, Any]:
    """
    Check ASCOM availability and return status.
    
    Returns:
        Dict with:
            - available: Whether ASCOM is available
            - backend: Which backend (alpyca/comtypes/None)
            - error: Error message if not available
            - version: Backend version if available
    """
    return {
        'available': ASCOM_AVAILABLE,
        'backend': ASCOM_BACKEND,
        'error': ASCOM_ERROR,
        'version': None,  # TODO: Get actual version
    }
