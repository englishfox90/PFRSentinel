"""
ZWO ASI Camera Adapter

Wraps the existing ZWOCamera implementation to conform to the CameraInterface.
This adapter provides the bridge between the abstraction layer and the ZWO-specific code.
"""
import threading
from typing import Optional, Dict, Any, List, Callable, Tuple
from PIL import Image

from .interface import (
    CameraInterface,
    CameraCapabilities,
    CameraInfo,
    CameraState,
    CaptureResult,
)
from ..zwo_camera import ZWOCamera


class ZWOCameraAdapter(CameraInterface):
    """
    Adapter for ZWO ASI cameras.
    
    Wraps the existing ZWOCamera class to provide the standard CameraInterface.
    All ZWO-specific functionality is delegated to the underlying ZWOCamera instance.
    """
    
    # ZWO-specific capabilities (static)
    _CAPABILITIES = CameraCapabilities(
        backend_name="ZWO",
        backend_version="1.0.0",
        
        # Connection
        supports_hot_plug=False,  # SDK doesn't auto-detect hot plug
        supports_multiple_cameras=True,
        requires_sdk_path=True,  # Needs ASICamera2.dll
        
        # Exposure
        supports_exposure_control=True,
        min_exposure_ms=0.032,  # 32 microseconds
        max_exposure_ms=3600000.0,  # 1 hour
        supports_auto_exposure=True,  # PFR Sentinel's auto-exposure algorithm
        
        # Gain
        supports_gain_control=True,
        min_gain=0,
        max_gain=600,
        supports_auto_gain=False,  # Not implemented
        
        # White Balance
        supports_white_balance=True,
        supports_auto_white_balance=True,  # ASI auto WB
        min_wb_value=1,
        max_wb_value=99,
        
        # Image Settings
        supports_binning=True,
        max_binning=4,
        supports_roi=True,
        supports_flip=True,
        supports_offset=True,
        
        # Bit Depth
        supports_raw8=True,
        supports_raw16=True,  # Camera-dependent, checked at runtime
        native_bit_depth=12,  # Most ZWO cameras are 12-bit ADC
        
        # Color/Bayer
        is_color_camera=True,  # Camera-dependent
        bayer_pattern="BGGR",  # ASI676MC default, camera-dependent
        supports_debayering=True,
        
        # Temperature
        supports_temperature_reading=True,
        supports_cooling=False,  # Camera-dependent (cooled models only)
        supports_cooler_control=False,
        
        # Scheduling
        supports_scheduled_capture=True,
        
        # Metadata
        provides_metadata=True,
        metadata_fields=[
            'CAMERA', 'EXPOSURE', 'GAIN', 'TEMP', 'TEMP_C', 'TEMP_F',
            'RES', 'DATETIME', 'FILENAME', 'SESSION',
            'BRIGHTNESS', 'MEAN', 'MEDIAN', 'MIN', 'MAX', 'STD_DEV',
            'P25', 'P75', 'P95',
            'CAMERA_BIT_DEPTH', 'IMAGE_BIT_DEPTH', 'BAYER_PATTERN',
            'PIXEL_SIZE', 'ELEC_PER_ADU'
        ],
        
        # Performance
        supports_streaming=False,  # Snapshot mode only
        max_fps=10.0,
    )
    
    def __init__(
        self,
        config: Optional[Dict[str, Any]] = None,
        logger: Optional[Callable[[str], None]] = None,
        sdk_path: Optional[str] = None,
        config_callback: Optional[Callable[[str, Any], None]] = None,
    ):
        """
        Initialize ZWO adapter.
        
        Args:
            config: Config dict with sdk_path, camera settings, etc.
            logger: Callback for log messages
            sdk_path: Path to ASICamera2.dll (overrides config)
            config_callback: Callback to save config values
        """
        self._config = config or {}
        self._sdk_path = sdk_path or self._config.get('sdk_path')
        self._config_callback = config_callback
        self._logger = logger
        
        # Internal state
        self._state = CameraState.DISCONNECTED
        self._camera_info: Optional[CameraInfo] = None
        self._detected_cameras: List[CameraInfo] = []
        
        # Underlying ZWO camera instance
        self._zwo: Optional[ZWOCamera] = None
        
        # Callbacks
        self._on_frame: Optional[Callable[[Image.Image, Dict[str, Any]], None]] = None
        self._on_error: Optional[Callable[[str], None]] = None
        self._state_callback: Optional[Callable[[CameraState], None]] = None
        self._calibration_callback: Optional[Callable[[bool], None]] = None
        
        # Settings cache
        self._current_settings: Dict[str, Any] = {}
        
        # Dynamic capabilities (updated after camera connection)
        self._dynamic_caps = dict(self._CAPABILITIES.to_dict())
    
    def _log(self, message: str) -> None:
        """Log message via callback"""
        if self._logger:
            self._logger(message)
        else:
            print(f"[ZWO] {message}")
    
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
        # Return capabilities with dynamic values
        caps_dict = self._dynamic_caps.copy()
        return CameraCapabilities(**{
            k: v for k, v in caps_dict.items() 
            if k in CameraCapabilities.__dataclass_fields__
        })
    
    @property
    def camera_info(self) -> Optional[CameraInfo]:
        return self._camera_info
    
    @property
    def is_connected(self) -> bool:
        return self._zwo is not None and self._zwo.camera is not None
    
    @property
    def is_capturing(self) -> bool:
        return self._zwo is not None and self._zwo.is_capturing
    
    # =========================================================================
    # Initialization & Detection
    # =========================================================================
    
    def initialize(self) -> bool:
        """Initialize ZWO SDK"""
        self._log("Initializing ZWO backend...")
        
        try:
            # Create ZWO camera instance (doesn't connect yet)
            self._zwo = ZWOCamera(sdk_path=self._sdk_path)
            self._zwo.on_log_callback = self._log
            self._zwo.config_callback = self._config_callback
            
            # Initialize SDK
            if not self._zwo.initialize_sdk():
                self._log("Failed to initialize ZWO SDK")
                return False
            
            self._log("ZWO SDK initialized successfully")
            return True
            
        except Exception as e:
            self._log(f"Error initializing ZWO backend: {e}")
            return False
    
    def detect_cameras(self) -> List[CameraInfo]:
        """Detect connected ZWO cameras"""
        if not self._zwo:
            self._log("Cannot detect cameras: backend not initialized")
            return []
        
        self._detected_cameras = []
        
        try:
            cameras = self._zwo.detect_cameras()
            
            for cam in cameras:
                info = CameraInfo(
                    index=cam['index'],
                    name=cam['name'],
                    backend="ZWO",
                    device_id=cam['name'],  # ZWO uses name as identifier
                )
                self._detected_cameras.append(info)
            
            self._log(f"Detected {len(self._detected_cameras)} ZWO camera(s)")
            return self._detected_cameras
            
        except Exception as e:
            self._log(f"Error detecting cameras: {e}")
            return []
    
    # =========================================================================
    # Connection Management
    # =========================================================================
    
    def connect(self, camera_index: int = 0, settings: Optional[Dict[str, Any]] = None) -> bool:
        """Connect to a ZWO camera"""
        if not self._zwo:
            self._log("Cannot connect: backend not initialized")
            return False
        
        self._set_state(CameraState.CONNECTING)
        
        try:
            # Convert settings to ZWO format
            zwo_settings = self._convert_settings_to_zwo(settings) if settings else None
            
            # Apply settings to ZWO instance before connecting
            if zwo_settings:
                self._apply_settings_to_zwo(zwo_settings)
            
            # Connect
            if not self._zwo.connect_camera(camera_index):
                self._set_state(CameraState.ERROR)
                return False
            
            # Update camera info
            zwo_info = self._zwo.camera_info
            self._camera_info = CameraInfo(
                index=camera_index,
                name=self._zwo.camera_name or "Unknown ZWO Camera",
                backend="ZWO",
                device_id=self._zwo.camera_name,
                max_width=zwo_info.get('MaxWidth'),
                max_height=zwo_info.get('MaxHeight'),
                pixel_size_um=zwo_info.get('PixelSize'),
                is_color=True,  # Assume color, could check camera type
                bit_depth=zwo_info.get('BitDepth', 8),
            )
            
            # Update dynamic capabilities based on connected camera
            self._update_dynamic_capabilities(zwo_info)
            
            self._set_state(CameraState.CONNECTED)
            self._log(f"Connected to {self._camera_info.name}")
            return True
            
        except Exception as e:
            self._log(f"Error connecting to camera: {e}")
            self._set_state(CameraState.ERROR)
            return False
    
    def disconnect(self) -> None:
        """Disconnect from camera"""
        if self._zwo:
            if self._zwo.is_capturing:
                self._zwo.stop_capture()
            self._zwo.disconnect_camera()
        
        self._camera_info = None
        self._set_state(CameraState.DISCONNECTED)
        self._log("Disconnected from camera")
    
    def reconnect(self) -> bool:
        """Attempt to reconnect"""
        if not self._zwo:
            return False
        
        self._set_state(CameraState.CONNECTING)
        
        try:
            if self._zwo.reconnect_camera_safe():
                self._set_state(CameraState.CONNECTED)
                return True
            else:
                self._set_state(CameraState.ERROR)
                return False
        except Exception as e:
            self._log(f"Reconnection failed: {e}")
            self._set_state(CameraState.ERROR)
            return False
    
    # =========================================================================
    # Configuration
    # =========================================================================
    
    def configure(self, settings: Dict[str, Any]) -> None:
        """Apply camera settings"""
        if not self._zwo or not self.is_connected:
            self._log("Cannot configure: camera not connected")
            return
        
        # Convert to ZWO format and apply
        zwo_settings = self._convert_settings_to_zwo(settings)
        self._apply_settings_to_zwo(zwo_settings)
        
        # Reconfigure camera
        self._zwo._configure_camera()
        
        # Cache settings
        self._current_settings.update(settings)
    
    def get_current_settings(self) -> Dict[str, Any]:
        """Get current settings"""
        if not self._zwo:
            return {}
        
        return {
            'exposure_ms': self._zwo.exposure_seconds * 1000,
            'gain': self._zwo.gain,
            'wb_r': self._zwo.white_balance_r,
            'wb_b': self._zwo.white_balance_b,
            'wb_mode': self._zwo.wb_mode,
            'offset': self._zwo.offset,
            'flip': self._zwo.flip,
            'bayer_pattern': self._zwo.bayer_pattern,
            'use_raw16': self._zwo.use_raw16,
            'auto_exposure': self._zwo.auto_exposure,
            'max_exposure_ms': self._zwo.max_exposure * 1000,
            'target_brightness': self._zwo.target_brightness,
        }
    
    def _convert_settings_to_zwo(self, settings: Dict[str, Any]) -> Dict[str, Any]:
        """Convert standard settings to ZWO-specific format"""
        zwo_settings = {}
        
        if 'exposure_ms' in settings:
            zwo_settings['exposure_sec'] = settings['exposure_ms'] / 1000.0
        if 'gain' in settings:
            zwo_settings['gain'] = settings['gain']
        if 'wb_r' in settings:
            zwo_settings['wb_r'] = settings['wb_r']
        if 'wb_b' in settings:
            zwo_settings['wb_b'] = settings['wb_b']
        if 'wb_mode' in settings:
            zwo_settings['wb_mode'] = settings['wb_mode']
        if 'offset' in settings:
            zwo_settings['offset'] = settings['offset']
        if 'flip' in settings:
            zwo_settings['flip'] = settings['flip']
        if 'use_raw16' in settings:
            zwo_settings['use_raw16'] = settings['use_raw16']
        
        return zwo_settings
    
    def _apply_settings_to_zwo(self, settings: Dict[str, Any]) -> None:
        """Apply settings to ZWO instance"""
        if not self._zwo:
            return
        
        if 'exposure_sec' in settings:
            self._zwo.exposure_seconds = settings['exposure_sec']
        if 'gain' in settings:
            self._zwo.gain = settings['gain']
        if 'wb_r' in settings:
            self._zwo.white_balance_r = settings['wb_r']
        if 'wb_b' in settings:
            self._zwo.white_balance_b = settings['wb_b']
        if 'wb_mode' in settings:
            self._zwo.wb_mode = settings['wb_mode']
        if 'offset' in settings:
            self._zwo.offset = settings['offset']
        if 'flip' in settings:
            self._zwo.flip = settings['flip']
        if 'use_raw16' in settings:
            self._zwo.use_raw16 = settings['use_raw16']
    
    def _update_dynamic_capabilities(self, zwo_info: dict) -> None:
        """Update capabilities based on connected camera"""
        # Update RAW16 support
        self._dynamic_caps['supports_raw16'] = self._zwo.supports_raw16
        self._dynamic_caps['native_bit_depth'] = zwo_info.get('BitDepth', 8)
        
        # Check for cooled cameras
        # (ZWO cooled cameras have 'ASI_COOLER_ON' control)
        if self._zwo.camera:
            try:
                controls = self._zwo.camera.get_controls()
                has_cooler = any(c.get('Name') == 'CoolerOn' for c in controls.values())
                self._dynamic_caps['supports_cooling'] = has_cooler
                self._dynamic_caps['supports_cooler_control'] = has_cooler
            except Exception:
                pass
    
    # =========================================================================
    # Capture Operations
    # =========================================================================
    
    def capture_frame(self) -> CaptureResult:
        """Capture a single frame"""
        if not self._zwo or not self.is_connected:
            return CaptureResult(success=False, error="Camera not connected")
        
        try:
            img, metadata = self._zwo.capture_single_frame()
            
            return CaptureResult(
                success=True,
                image=img,
                metadata=metadata,
                raw_rgb_no_wb=metadata.get('RAW_RGB_NO_WB'),
                raw_rgb_16bit=metadata.get('RAW_RGB_16BIT'),
                exposure_time_ms=self._zwo.exposure_seconds * 1000,
                capture_timestamp=metadata.get('DATETIME'),
            )
            
        except Exception as e:
            return CaptureResult(success=False, error=str(e))
    
    def start_capture(
        self,
        on_frame: Callable[[Image.Image, Dict[str, Any]], None],
        on_error: Optional[Callable[[str], None]] = None,
        interval_sec: float = 5.0
    ) -> bool:
        """Start continuous capture"""
        if not self._zwo or not self.is_connected:
            self._log("Cannot start capture: camera not connected")
            return False
        
        self._on_frame = on_frame
        self._on_error = on_error
        self._zwo.capture_interval = interval_sec
        self._zwo.on_error_callback = on_error
        self._zwo.on_calibration_callback = self._calibration_callback
        
        if self._zwo.start_capture(on_frame, self._log):
            self._set_state(CameraState.CAPTURING)
            return True
        else:
            return False
    
    def stop_capture(self) -> None:
        """Stop continuous capture"""
        if self._zwo:
            self._zwo.stop_capture()
        
        if self.is_connected:
            self._set_state(CameraState.CONNECTED)
        else:
            self._set_state(CameraState.DISCONNECTED)
    
    # =========================================================================
    # Auto-Exposure
    # =========================================================================
    
    def set_auto_exposure(self, enabled: bool, target_brightness: int = 100,
                          max_exposure_ms: float = 30000.0) -> bool:
        """Enable/disable auto-exposure"""
        if not self._zwo:
            return False
        
        self._zwo.auto_exposure = enabled
        self._zwo.target_brightness = target_brightness
        self._zwo.max_exposure = max_exposure_ms / 1000.0
        
        return True
    
    def run_calibration(self) -> bool:
        """Run rapid auto-exposure calibration"""
        if not self._zwo or not self._zwo.auto_exposure:
            return False
        
        try:
            self._set_state(CameraState.CALIBRATING)
            self._zwo.run_calibration()
            self._set_state(CameraState.CONNECTED if not self.is_capturing else CameraState.CAPTURING)
            return True
        except Exception as e:
            self._log(f"Calibration failed: {e}")
            return False
    
    # =========================================================================
    # Scheduling
    # =========================================================================
    
    def set_schedule(self, enabled: bool, start_time: str = "17:00", 
                     end_time: str = "09:00") -> None:
        """Configure scheduled capture window"""
        if self._zwo:
            self._zwo.scheduled_capture_enabled = enabled
            self._zwo.scheduled_start_time = start_time
            self._zwo.scheduled_end_time = end_time
    
    def is_within_schedule(self) -> bool:
        """Check if within scheduled window"""
        if self._zwo:
            return self._zwo.is_within_scheduled_window()
        return True
    
    # =========================================================================
    # Temperature
    # =========================================================================
    
    def get_temperature(self) -> Optional[Dict[str, Any]]:
        """Get sensor temperature"""
        if not self._zwo or not self.is_connected:
            return None
        
        try:
            return self._zwo._get_temperature()
        except Exception:
            return None
    
    # =========================================================================
    # Callbacks
    # =========================================================================
    
    def set_log_callback(self, callback: Optional[Callable[[str], None]]) -> None:
        """Set log callback"""
        self._logger = callback
        if self._zwo:
            self._zwo.on_log_callback = callback
    
    def set_state_callback(self, callback: Optional[Callable[[CameraState], None]]) -> None:
        """Set state change callback"""
        self._state_callback = callback
    
    def set_calibration_callback(self, callback: Optional[Callable[[bool], None]]) -> None:
        """Set calibration callback"""
        self._calibration_callback = callback
        if self._zwo:
            self._zwo.on_calibration_callback = callback
    
    # =========================================================================
    # Exposure Progress
    # =========================================================================
    
    def get_exposure_progress(self) -> Tuple[float, float]:
        """Get exposure progress"""
        if not self._zwo:
            return (0.0, 0.0)
        
        if self._zwo.exposure_start_time is None:
            return (0.0, 0.0)
        
        import time
        elapsed = time.time() - self._zwo.exposure_start_time
        remaining = self._zwo.exposure_remaining
        
        return (elapsed, remaining)
