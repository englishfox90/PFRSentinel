"""
ASCOM Camera Adapter

Provides camera interface for ASCOM-compatible astronomy cameras.
ASCOM (Astronomy Common Object Model) is an industry standard for astronomy
equipment interoperability, supporting both Windows COM and network Alpaca APIs.

Requirements:
    - alpyca (recommended): pip install alpyca
      For ASCOM Alpaca (network/REST API) - cross-platform
    - OR comtypes: pip install comtypes
      For Windows COM interface - Windows only
    
Supported Features:
    - Camera detection via Alpaca discovery or ASCOM Chooser
    - Full exposure control (exposure, gain, offset, binning)
    - Temperature monitoring and cooler control
    - Software auto-exposure (matching ZWO implementation)
    - Color/Bayer camera support with debayering
    - Continuous capture with callbacks
"""
import os
import time
import threading
import numpy as np
from enum import IntEnum
from typing import Optional, Dict, Any, List, Callable, Tuple
from datetime import datetime
from PIL import Image

from ..interface import (
    CameraInterface,
    CameraCapabilities,
    CameraInfo,
    CameraState,
    CaptureResult,
)
from ..utils import (
    calculate_brightness,
    check_clipping,
    apply_white_balance,
    calculate_image_stats,
)


# =============================================================================
# ASCOM Sensor Types (from ASCOM ICameraV4)
# =============================================================================
class SensorType(IntEnum):
    """ASCOM SensorType enumeration"""
    Monochrome = 0
    Color = 1
    RGGB = 2
    CMYG = 3
    CMYG2 = 4
    LRGB = 5


# Map ASCOM SensorType to our Bayer patterns
SENSOR_TO_BAYER = {
    SensorType.RGGB: 'RGGB',
    SensorType.CMYG: None,  # Not standard Bayer
    SensorType.CMYG2: None,
    SensorType.LRGB: None,
}


# =============================================================================
# ASCOM Camera States (from ASCOM ICameraV4)
# =============================================================================
class ASCOMCameraState(IntEnum):
    """ASCOM CameraStates enumeration"""
    Idle = 0
    Waiting = 1
    Exposing = 2
    Reading = 3
    Download = 4
    Error = 5


# =============================================================================
# ASCOM Availability Detection
# =============================================================================
ASCOM_AVAILABLE = False
ASCOM_BACKEND = None
ASCOM_VERSION = None
ASCOM_ERROR = None

# Try alpaca (alpyca package) first (cross-platform, modern)
try:
    from alpaca.camera import Camera as AlpacaCamera
    from alpaca.discovery import search_ipv4
    import alpaca
    ASCOM_AVAILABLE = True
    ASCOM_BACKEND = "alpaca"
    # Get version from importlib.metadata since alpaca doesn't expose __version__
    try:
        from importlib.metadata import version
        ASCOM_VERSION = version('alpyca')
    except Exception:
        ASCOM_VERSION = 'installed'
except ImportError:
    pass

# Fall back to comtypes for Windows COM (Windows only)
if not ASCOM_AVAILABLE:
    try:
        import comtypes
        import comtypes.client
        ASCOM_AVAILABLE = True
        ASCOM_BACKEND = "comtypes"
        ASCOM_VERSION = getattr(comtypes, '__version__', 'unknown')
    except ImportError:
        ASCOM_ERROR = (
            "ASCOM libraries not found. Install: "
            "pip install alpyca (recommended) or pip install comtypes (Windows)"
        )


# =============================================================================
# ASCOM Camera Adapter
# =============================================================================
class ASCOMCameraAdapter(CameraInterface):
    """
    Adapter for ASCOM-compatible astronomy cameras.
    
    Supports both ASCOM Alpaca (network API) and ASCOM COM (Windows).
    Provides feature parity with ZWO adapter including:
    - Full exposure control
    - Software auto-exposure
    - Temperature/cooling management
    - Color camera debayering
    - Continuous capture with callbacks
    
    Configuration Options:
        alpaca_host: Alpaca server hostname (default: localhost)
        alpaca_port: Alpaca server port (default: 11111)
        alpaca_device_number: Camera device number (default: 0)
        ascom_device_id: COM ProgID for Windows COM interface
        use_alpaca: Force Alpaca mode (default: auto-detect)
    """
    
    # Base capabilities (refined per-device after connection)
    _BASE_CAPABILITIES = CameraCapabilities(
        backend_name="ASCOM",
        backend_version="1.0.0",
        
        # Connection
        supports_hot_plug=False,
        supports_multiple_cameras=True,
        requires_sdk_path=False,
        
        # Exposure
        supports_exposure_control=True,
        min_exposure_ms=0.001,
        max_exposure_ms=3600000.0,
        supports_auto_exposure=True,
        
        # Gain
        supports_gain_control=True,
        min_gain=0,
        max_gain=100,
        supports_auto_gain=False,
        
        # White Balance (software)
        supports_white_balance=True,
        supports_auto_white_balance=False,
        min_wb_value=1,
        max_wb_value=99,
        
        # Image Settings
        supports_binning=True,
        max_binning=4,
        supports_roi=True,
        supports_flip=False,
        supports_offset=True,
        
        # Bit Depth
        supports_raw8=True,
        supports_raw16=True,
        native_bit_depth=16,
        
        # Color/Bayer
        is_color_camera=False,
        bayer_pattern=None,
        supports_debayering=True,
        
        # Temperature
        supports_temperature_reading=True,
        supports_cooling=True,
        supports_cooler_control=True,
        
        # Scheduling
        supports_scheduled_capture=True,
        
        # Metadata
        provides_metadata=True,
        metadata_fields=[
            'CAMERA', 'EXPOSURE', 'GAIN', 'TEMP', 'TEMP_C', 'TEMP_F',
            'RES', 'DATETIME', 'FILENAME', 'SESSION', 'OFFSET',
            'BRIGHTNESS', 'MEAN', 'MEDIAN', 'MIN', 'MAX', 'STD_DEV',
            'COOLER_POWER', 'COOLER_SETPOINT', 'SENSOR_TYPE',
        ],
        
        # Performance
        supports_streaming=False,
        max_fps=1.0,
    )
    
    def __init__(
        self,
        config: Optional[Dict[str, Any]] = None,
        logger: Optional[Callable[[str], None]] = None,
        config_callback: Optional[Callable[[str, Any], None]] = None,
    ):
        """Initialize ASCOM adapter."""
        self._config = config or {}
        self._logger = logger
        self._config_callback = config_callback
        
        # Connection state
        self._state = CameraState.DISCONNECTED
        self._camera_info: Optional[CameraInfo] = None
        self._capabilities = self._BASE_CAPABILITIES
        
        # ASCOM camera object
        self._camera = None
        self._using_alpaca = False
        
        # Detected cameras cache
        self._detected_cameras: List[CameraInfo] = []
        
        # Callbacks
        self._on_frame: Optional[Callable[[Image.Image, Dict[str, Any]], None]] = None
        self._on_error: Optional[Callable[[str], None]] = None
        self._state_callback: Optional[Callable[[CameraState], None]] = None
        self._calibration_callback: Optional[Callable[[bool], None]] = None
        
        # Capture thread
        self._capture_thread: Optional[threading.Thread] = None
        self._capture_running = False
        self._capture_interval = 5.0
        self._stop_event = threading.Event()
        
        # Current settings
        self._exposure_ms = 1000.0
        self._gain = 0
        self._offset = 0
        self._bin_x = 1
        self._bin_y = 1
        self._start_x = 0
        self._start_y = 0
        self._num_x = 0
        self._num_y = 0
        
        # White balance (software)
        self._wb_r = 50
        self._wb_b = 50
        self._wb_mode = 'manual'  # 'manual' or 'gray_world'
        self._wb_gw_low = 5
        self._wb_gw_high = 95
        
        # Auto-exposure settings
        self._auto_exposure = False
        self._target_brightness = 100
        self._max_exposure_ms = 30000.0
        self._min_exposure_ms = 1.0
        
        # Cooling settings
        self._cooler_on = False
        self._cooler_setpoint = -10.0
        
        # Scheduling
        self._scheduled_enabled = False
        self._schedule_start = "17:00"
        self._schedule_end = "09:00"
        
        # Bayer/color info
        self._sensor_type = SensorType.Monochrome
        self._bayer_pattern: Optional[str] = None
        self._bayer_offset_x = 0
        self._bayer_offset_y = 0
        
        # Image state
        self._last_image: Optional[Image.Image] = None
        self._last_metadata: Optional[Dict[str, Any]] = None
        self._image_lock = threading.Lock()
        
        # Calibration state
        self._is_calibrating = False
        
        # Dynamic capabilities
        self._dynamic_caps = dict(self._BASE_CAPABILITIES.to_dict())
    
    def _log(self, message: str) -> None:
        """Log message via callback or print"""
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
        if self._camera is None:
            return False
        try:
            return self._camera.Connected
        except Exception:
            return False
    
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
        
        self._log(f"ASCOM backend: {ASCOM_BACKEND} v{ASCOM_VERSION}")
        return True
    
    def detect_cameras(self) -> List[CameraInfo]:
        """Detect available ASCOM cameras."""
        if not ASCOM_AVAILABLE:
            self._log("Cannot detect: ASCOM not available")
            return []
        
        self._detected_cameras = []
        
        if ASCOM_BACKEND == "alpaca":
            self._detected_cameras = self._detect_alpaca_cameras()
        elif ASCOM_BACKEND == "comtypes":
            self._detected_cameras = self._detect_com_cameras()
        
        self._log(f"Detected {len(self._detected_cameras)} ASCOM camera(s)")
        return self._detected_cameras
    
    def _detect_alpaca_cameras(self) -> List[CameraInfo]:
        """
        Detect cameras via Alpaca Management API (non-intrusive).
        
        Uses the Alpaca Management API to list configured devices WITHOUT
        connecting to them. This ensures we don't disconnect cameras that
        may be in use by other software.
        """
        import requests
        cameras = []
        host = self._config.get('alpaca_host', 'localhost')
        port = self._config.get('alpaca_port', 11111)
        
        try:
            self._log(f"Querying Alpaca server at {host}:{port} (non-intrusive)...")
            
            # Use Management API to get configured devices without connecting
            # This is the proper way to enumerate Alpaca devices
            mgmt_url = f"http://{host}:{port}/management/v1/configureddevices"
            
            try:
                response = requests.get(mgmt_url, timeout=5)
                if response.status_code == 200:
                    data = response.json()
                    devices = data.get('Value', [])
                    
                    for device in devices:
                        if device.get('DeviceType', '').lower() == 'camera':
                            device_num = device.get('DeviceNumber', 0)
                            name = device.get('DeviceName', f'Camera {device_num}')
                            
                            info = CameraInfo(
                                index=device_num,
                                name=name,
                                backend="ASCOM/Alpaca",
                                device_id=f"alpaca://{host}:{port}/camera/{device_num}",
                            )
                            cameras.append(info)
                            self._log(f"  Found: {name} (device {device_num})")
                    
                    if not cameras:
                        self._log("  No cameras configured on Alpaca server")
                else:
                    self._log(f"  Management API returned {response.status_code}")
                    # Fall back to probing if management API not available
                    cameras = self._detect_alpaca_cameras_probe()
                    
            except requests.exceptions.ConnectionError:
                self._log(f"  Cannot connect to Alpaca server at {host}:{port}")
            except requests.exceptions.Timeout:
                self._log(f"  Timeout connecting to Alpaca server")
            except Exception as e:
                self._log(f"  Management API error: {e}, trying probe method...")
                cameras = self._detect_alpaca_cameras_probe()
                
        except Exception as e:
            self._log(f"Error detecting Alpaca cameras: {e}")
        
        # Network discovery (optional - find other Alpaca servers on LAN)
        if self._config.get('alpaca_discovery', False):  # Disabled by default
            try:
                self._log("Running Alpaca network discovery...")
                servers = search_ipv4(timeout=2)
                for server in servers:
                    try:
                        srv_host = server.get('host', None) if isinstance(server, dict) else getattr(server, 'host', None)
                        srv_port = server.get('port', None) if isinstance(server, dict) else getattr(server, 'port', None)
                        if srv_host and srv_port and (srv_host != host or srv_port != port):
                            self._log(f"  Found server: {srv_host}:{srv_port}")
                    except Exception:
                        pass
            except Exception as e:
                self._log(f"Alpaca discovery error: {e}")
        
        return cameras
    
    def _detect_alpaca_cameras_probe(self) -> List[CameraInfo]:
        """
        Fallback: Probe for cameras by connecting (intrusive).
        
        Only used if the Management API is not available.
        WARNING: This connects to each camera which may disconnect other software!
        """
        cameras = []
        host = self._config.get('alpaca_host', 'localhost')
        port = self._config.get('alpaca_port', 11111)
        
        self._log("  WARNING: Using probe method (may disconnect other software)")
        
        for device_num in range(10):
            try:
                cam = AlpacaCamera(f"{host}:{port}", device_num)
                # Must connect to get camera name via Alpaca
                cam.Connected = True
                try:
                    name = cam.Name
                    info = CameraInfo(
                        index=device_num,
                        name=name,
                        backend="ASCOM/Alpaca",
                        device_id=f"alpaca://{host}:{port}/camera/{device_num}",
                    )
                    cameras.append(info)
                    self._log(f"  Found: {name} (device {device_num})")
                finally:
                    # Always disconnect after probing
                    try:
                        cam.Connected = False
                    except Exception:
                        pass
            except Exception as e:
                # Stop when we get "not configured" error (no more cameras)
                err_str = str(e).lower()
                if 'not configured' in err_str or '0x190' in err_str:
                    break
                # Log other errors but continue trying
                self._log(f"  Device {device_num} error: {e}")
        
        return cameras
    
    def _detect_com_cameras(self) -> List[CameraInfo]:
        """Detect cameras via Windows COM/ASCOM Profile"""
        cameras = []
        
        if ASCOM_BACKEND != "comtypes":
            return cameras
        
        try:
            profile = comtypes.client.CreateObject("ASCOM.Utilities.Profile")
            profile.DeviceType = "Camera"
            
            drivers = profile.RegisteredDeviceTypes
            for driver_id in drivers:
                try:
                    name = profile.GetValue(driver_id, "")
                    info = CameraInfo(
                        index=len(cameras),
                        name=name or driver_id,
                        backend="ASCOM/COM",
                        device_id=driver_id,
                    )
                    cameras.append(info)
                    self._log(f"  Found: {name} ({driver_id})")
                except Exception:
                    pass
        except Exception as e:
            self._log(f"Error accessing ASCOM Profile: {e}")
        
        return cameras
    
    # =========================================================================
    # Connection Management
    # =========================================================================
    
    def connect(self, camera_index: int = 0, settings: Optional[Dict[str, Any]] = None) -> bool:
        """Connect to ASCOM camera."""
        if not ASCOM_AVAILABLE:
            self._log(f"Cannot connect: {ASCOM_ERROR}")
            return False
        
        self._set_state(CameraState.CONNECTING)
        
        try:
            if ASCOM_BACKEND == "alpaca":
                success = self._connect_alpaca(camera_index)
            else:
                success = self._connect_com(camera_index)
            
            if success:
                self._read_camera_properties()
                if settings:
                    self.configure(settings)
                self._set_state(CameraState.CONNECTED)
                name = self._camera_info.name if self._camera_info else 'camera'
                self._log(f"Connected to {name}")
                return True
            else:
                self._set_state(CameraState.ERROR)
                return False
                
        except Exception as e:
            self._log(f"Connection error: {e}")
            self._set_state(CameraState.ERROR)
            return False
    
    def _connect_alpaca(self, camera_index: int) -> bool:
        """Connect via Alpaca"""
        host = self._config.get('alpaca_host', 'localhost')
        port = self._config.get('alpaca_port', 11111)
        device_num = self._config.get('alpaca_device_number', camera_index)
        
        self._log(f"Connecting to Alpaca camera at {host}:{port}/camera/{device_num}")
        
        try:
            self._camera = AlpacaCamera(f"{host}:{port}", device_num)
            self._camera.Connected = True
            self._using_alpaca = True
            return True
        except Exception as e:
            self._log(f"Alpaca connection failed: {e}")
            return False
    
    def _connect_com(self, camera_index: int) -> bool:
        """Connect via Windows COM"""
        device_id = self._config.get('ascom_device_id', '')
        
        if not device_id and camera_index < len(self._detected_cameras):
            device_id = self._detected_cameras[camera_index].device_id
        
        if not device_id:
            self._log("No ASCOM device ID. Set 'ascom_device_id' in config")
            return False
        
        self._log(f"Connecting to COM camera: {device_id}")
        
        try:
            self._camera = comtypes.client.CreateObject(device_id)
            self._camera.Connected = True
            self._using_alpaca = False
            return True
        except Exception as e:
            self._log(f"COM connection failed: {e}")
            return False
    
    def _read_camera_properties(self) -> None:
        """Read camera properties after connection"""
        if not self._camera:
            return
        
        try:
            name = self._safe_get('Name', 'ASCOM Camera')
            width = self._safe_get('CameraXSize', 0)
            height = self._safe_get('CameraYSize', 0)
            pixel_size = self._safe_get('PixelSizeX', 0)
            
            exp_min = self._safe_get('ExposureMin', 0.001) * 1000
            exp_max = self._safe_get('ExposureMax', 3600) * 1000
            gain_min = self._safe_get('GainMin', 0)
            gain_max = self._safe_get('GainMax', 100)
            
            max_bin_x = self._safe_get('MaxBinX', 1)
            max_bin_y = self._safe_get('MaxBinY', 1)
            
            sensor_type_val = self._safe_get('SensorType', 0)
            self._sensor_type = SensorType(sensor_type_val)
            is_color = self._sensor_type != SensorType.Monochrome
            
            if is_color and self._sensor_type in SENSOR_TO_BAYER:
                self._bayer_pattern = SENSOR_TO_BAYER[self._sensor_type]
                self._bayer_offset_x = self._safe_get('BayerOffsetX', 0)
                self._bayer_offset_y = self._safe_get('BayerOffsetY', 0)
            
            can_set_temp = self._safe_get('CanSetCCDTemperature', False)
            max_adu = self._safe_get('MaxADU', 65535)
            bit_depth = 16 if max_adu > 4095 else 12 if max_adu > 255 else 8
            
            self._camera_info = CameraInfo(
                index=0,
                name=name,
                backend="ASCOM",
                device_id=self._config.get('ascom_device_id', ''),
                max_width=width,
                max_height=height,
                pixel_size_um=pixel_size,
                is_color=is_color,
                bit_depth=bit_depth,
            )
            
            self._dynamic_caps.update({
                'min_exposure_ms': exp_min,
                'max_exposure_ms': exp_max,
                'min_gain': gain_min,
                'max_gain': gain_max,
                'max_binning': max(max_bin_x, max_bin_y),
                'native_bit_depth': bit_depth,
                'is_color_camera': is_color,
                'bayer_pattern': self._bayer_pattern,
                'supports_cooling': can_set_temp,
                'supports_cooler_control': can_set_temp,
            })
            
            self._num_x = width // self._bin_x
            self._num_y = height // self._bin_y
            
            self._log(f"Camera: {name} ({width}x{height}, {bit_depth}-bit)")
            self._log(f"Exposure: {exp_min:.3f}ms - {exp_max:.1f}ms")
            self._log(f"Gain: {gain_min} - {gain_max}")
            if is_color:
                self._log(f"Sensor: {self._sensor_type.name}, Bayer: {self._bayer_pattern}")
            
        except Exception as e:
            self._log(f"Error reading camera properties: {e}")
    
    def _safe_get(self, prop: str, default: Any) -> Any:
        """Safely get camera property with default"""
        try:
            return getattr(self._camera, prop)
        except Exception:
            return default
    
    def disconnect(self) -> None:
        """Disconnect from camera"""
        if self._capture_running:
            self.stop_capture()
        
        if self._camera:
            try:
                self._camera.Connected = False
            except Exception as e:
                self._log(f"Error disconnecting: {e}")
            self._camera = None
        
        self._camera_info = None
        self._set_state(CameraState.DISCONNECTED)
        self._log("Disconnected from ASCOM camera")
    
    def reconnect(self) -> bool:
        """Attempt to reconnect"""
        self.disconnect()
        return self.connect(0)
    
    # =========================================================================
    # Configuration
    # =========================================================================
    
    def configure(self, settings: Dict[str, Any]) -> None:
        """Configure camera settings."""
        if 'exposure_ms' in settings:
            self._exposure_ms = float(settings['exposure_ms'])
        if 'exposure' in settings:
            self._exposure_ms = float(settings['exposure'])
        if 'gain' in settings:
            self._gain = int(settings['gain'])
        if 'offset' in settings:
            self._offset = int(settings['offset'])
        if 'bin_x' in settings:
            self._bin_x = int(settings['bin_x'])
        if 'bin_y' in settings:
            self._bin_y = int(settings['bin_y'])
        if 'wb_r' in settings:
            self._wb_r = int(settings['wb_r'])
        if 'wb_b' in settings:
            self._wb_b = int(settings['wb_b'])
        if 'wb_mode' in settings:
            self._wb_mode = settings['wb_mode']
        if 'wb_gw_low' in settings:
            self._wb_gw_low = int(settings['wb_gw_low'])
        if 'wb_gw_high' in settings:
            self._wb_gw_high = int(settings['wb_gw_high'])
        if 'auto_exposure' in settings:
            self._auto_exposure = bool(settings['auto_exposure'])
        if 'target_brightness' in settings:
            self._target_brightness = int(settings['target_brightness'])
        if 'max_exposure_ms' in settings:
            self._max_exposure_ms = float(settings['max_exposure_ms'])
        if 'cooler_on' in settings:
            self._cooler_on = bool(settings['cooler_on'])
        if 'cooler_setpoint' in settings:
            self._cooler_setpoint = float(settings['cooler_setpoint'])
        if 'scheduled_enabled' in settings:
            self._scheduled_enabled = bool(settings['scheduled_enabled'])
        if 'schedule_start' in settings:
            self._schedule_start = settings['schedule_start']
        if 'schedule_end' in settings:
            self._schedule_end = settings['schedule_end']
        
        if self._camera:
            self._apply_settings()
    
    def _apply_settings(self) -> None:
        """Apply settings to connected camera"""
        if not self._camera:
            return
        
        try:
            self._camera.BinX = self._bin_x
            self._camera.BinY = self._bin_y
            
            if self._num_x == 0:
                self._num_x = self._camera.CameraXSize // self._bin_x
            if self._num_y == 0:
                self._num_y = self._camera.CameraYSize // self._bin_y
            
            self._camera.StartX = self._start_x
            self._camera.StartY = self._start_y
            self._camera.NumX = self._num_x
            self._camera.NumY = self._num_y
            
            try:
                self._camera.Gain = self._gain
            except Exception:
                pass
            
            try:
                self._camera.Offset = self._offset
            except Exception:
                pass
            
            if self._cooler_on:
                try:
                    self._camera.CoolerOn = True
                    self._camera.SetCCDTemperature = self._cooler_setpoint
                except Exception:
                    pass
            
        except Exception as e:
            self._log(f"Error applying settings: {e}")
    
    def get_current_settings(self) -> Dict[str, Any]:
        """Get current camera settings"""
        return {
            'exposure_ms': self._exposure_ms,
            'gain': self._gain,
            'offset': self._offset,
            'bin_x': self._bin_x,
            'bin_y': self._bin_y,
            'wb_r': self._wb_r,
            'wb_b': self._wb_b,
            'auto_exposure': self._auto_exposure,
            'target_brightness': self._target_brightness,
            'max_exposure_ms': self._max_exposure_ms,
            'cooler_on': self._cooler_on,
            'cooler_setpoint': self._cooler_setpoint,
        }
    
    # =========================================================================
    # Capture Operations
    # =========================================================================
    
    def capture_frame(self) -> CaptureResult:
        """Capture a single frame from ASCOM camera."""
        if not self._camera:
            return CaptureResult(success=False, error="Camera not connected")
        
        try:
            exposure_sec = self._exposure_ms / 1000.0
            self._camera.StartExposure(exposure_sec, True)
            
            start_time = time.time()
            while not self._camera.ImageReady:
                if self._stop_event.is_set():
                    try:
                        self._camera.AbortExposure()
                    except Exception:
                        pass
                    return CaptureResult(success=False, error="Capture cancelled")
                
                try:
                    cam_state = self._camera.CameraState
                    if cam_state == ASCOMCameraState.Error:
                        return CaptureResult(success=False, error="Camera in error state")
                except Exception:
                    pass
                
                time.sleep(0.1)
                
                if time.time() - start_time > exposure_sec + 30:
                    return CaptureResult(success=False, error="Exposure timeout")
            
            image_array = self._camera.ImageArray
            img_data = self._ascom_array_to_numpy(image_array)
            
            # Debayer color sensors - ASCOM gives us numpy array, not raw bytes
            if self._sensor_type != SensorType.Monochrome and self._bayer_pattern:
                img_data = self._debayer_ascom_array(img_data, self._bayer_pattern)
                # Apply white balance using config dict format
                wb_config = {
                    'mode': self._wb_mode,
                    'apply_software_gains': self._wb_mode == 'manual',
                    'manual_red_gain': self._wb_r / 50.0,
                    'manual_blue_gain': self._wb_b / 50.0,
                    'gray_world_low_pct': self._wb_gw_low,
                    'gray_world_high_pct': self._wb_gw_high,
                }
                img_data = apply_white_balance(img_data, wb_config)
            
            # Convert to 8-bit for stats/brightness calculation
            if img_data.dtype != np.uint8:
                img_8bit = (img_data / 256).astype(np.uint8)
            else:
                img_8bit = img_data
            
            # Calculate stats on 8-bit data for consistent 0-255 range
            stats = calculate_image_stats(img_8bit)
            brightness = calculate_brightness(img_8bit)
            
            # Log brightness for debugging dark images
            if brightness < 5:
                self._log(f"Very dark image: brightness={brightness:.1f}, dtype={img_data.dtype}, max={img_data.max()}, mean={np.mean(img_data):.1f}")
            
            if len(img_8bit.shape) == 2:
                pil_image = Image.fromarray(img_8bit, mode='L')
            else:
                pil_image = Image.fromarray(img_8bit, mode='RGB')
            
            temp_info = self.get_temperature()
            temp_c = temp_info.get('sensor_temp', 0) if temp_info else 0
            temp_f = temp_c * 9/5 + 32
            
            metadata = {
                'CAMERA': self._camera_info.name if self._camera_info else 'ASCOM',
                'EXPOSURE': f"{self._exposure_ms}ms",
                'EXPOSURE_MS': self._exposure_ms,  # Numeric for calculations
                'GAIN': str(self._gain),
                'OFFSET': str(self._offset),
                'TEMP': f"{temp_c:.1f}°C",
                'TEMP_C': temp_c,
                'TEMP_F': temp_f,
                'RES': f"{img_data.shape[1]}x{img_data.shape[0]}",
                'DATETIME': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'FILENAME': f"ascom_{datetime.now().strftime('%H%M%S')}.jpg",
                'BRIGHTNESS': brightness,
                'SENSOR_TYPE': self._sensor_type.name,
                'AUTO_EXPOSURE': self._auto_exposure,
                'TARGET_BRIGHTNESS': self._target_brightness,
                'MAX_EXPOSURE_MS': self._max_exposure_ms,
                **stats,
            }
            
            if temp_info:
                metadata.update({
                    'COOLER_ON': temp_info.get('cooler_on', False),
                    'COOLER_POWER': temp_info.get('cooler_power', 0),
                    'COOLER_SETPOINT': temp_info.get('cooler_setpoint', 0),
                })
            
            return CaptureResult(
                success=True,
                image=pil_image,
                metadata=metadata,
                raw_rgb_no_wb=img_data  # Pre-WB RGB data for dev mode
            )
            
        except Exception as e:
            self._log(f"Capture error: {e}")
            return CaptureResult(success=False, error=str(e))
    
    def _ascom_array_to_numpy(self, ascom_array) -> np.ndarray:
        """Convert ASCOM ImageArray to numpy array."""
        arr = np.array(ascom_array)
        if arr.ndim == 2:
            arr = arr.T
        elif arr.ndim == 3:
            arr = np.transpose(arr, (1, 0, 2))
        return arr
    
    def _debayer_ascom_array(self, img_array: np.ndarray, bayer_pattern: str) -> np.ndarray:
        """
        Debayer an ASCOM numpy array (not raw bytes).
        
        Unlike debayer_raw_image() which expects raw byte buffer,
        ASCOM's ImageArray is already a 2D numpy array.
        
        Args:
            img_array: 2D numpy array from ASCOM ImageArray
            bayer_pattern: Bayer pattern string (RGGB, BGGR, GRBG, GBRG)
            
        Returns:
            3D RGB numpy array (uint8)
        """
        try:
            import cv2
            bayer_map = {
                'RGGB': cv2.COLOR_BayerRG2RGB,
                'BGGR': cv2.COLOR_BayerBG2RGB,
                'GRBG': cv2.COLOR_BayerGR2RGB,
                'GBRG': cv2.COLOR_BayerGB2RGB,
            }
            bayer_code = bayer_map.get(bayer_pattern, cv2.COLOR_BayerBG2RGB)
            
            # Ensure proper dtype for cv2.cvtColor
            if img_array.dtype == np.uint16:
                img_rgb = cv2.cvtColor(img_array, bayer_code)
                # Scale 16-bit to 8-bit for processing pipeline
                img_rgb = (img_rgb / 257).astype(np.uint8)
            elif img_array.dtype == np.uint8:
                img_rgb = cv2.cvtColor(img_array, bayer_code)
            else:
                # Convert to appropriate dtype first
                if img_array.max() > 255:
                    img_16 = img_array.astype(np.uint16)
                    img_rgb = cv2.cvtColor(img_16, bayer_code)
                    img_rgb = (img_rgb / 257).astype(np.uint8)
                else:
                    img_8 = img_array.astype(np.uint8)
                    img_rgb = cv2.cvtColor(img_8, bayer_code)
            
            return img_rgb
            
        except ImportError:
            self._log("OpenCV not available for debayering")
            # Return grayscale as fallback
            if img_array.dtype != np.uint8:
                if img_array.max() > 255:
                    img_array = (img_array / 257).astype(np.uint8)
                else:
                    img_array = img_array.astype(np.uint8)
            return np.stack([img_array] * 3, axis=-1)
    
    def start_capture(
        self,
        on_frame: Callable[[Image.Image, Dict[str, Any]], None],
        on_error: Optional[Callable[[str], None]] = None,
        interval_sec: float = 5.0
    ) -> bool:
        """Start continuous capture loop with callbacks"""
        if not self._camera:
            self._log("Cannot start capture: camera not connected")
            return False
        
        if self._capture_running:
            self._log("Capture already running")
            return False
        
        self._on_frame = on_frame
        self._on_error = on_error
        self._capture_interval = interval_sec
        self._capture_running = True
        self._stop_event.clear()
        
        self._capture_thread = threading.Thread(
            target=self._capture_loop,
            daemon=True,
            name="ASCOM-Capture"
        )
        self._capture_thread.start()
        
        self._set_state(CameraState.CAPTURING)
        self._log(f"Capture started: interval={interval_sec}s, exposure={self._exposure_ms}ms, auto_exp={self._auto_exposure}")
        return True
    
    def stop_capture(self) -> None:
        """Stop continuous capture"""
        self._capture_running = False
        self._stop_event.set()
        
        if self._capture_thread:
            self._capture_thread.join(timeout=10.0)
            self._capture_thread = None
        
        if self._camera:
            try:
                if self._safe_get('CanAbortExposure', False):
                    self._camera.AbortExposure()
            except Exception:
                pass
            self._set_state(CameraState.CONNECTED)
        else:
            self._set_state(CameraState.DISCONNECTED)
        
        self._log("Capture stopped")
    
    def _capture_loop(self) -> None:
        """Background capture loop with automatic stop on repeated errors"""
        self._log("Capture loop started")
        
        # Run initial calibration if auto-exposure is enabled
        if self._auto_exposure:
            self._log("Auto-exposure enabled - running initial calibration")
            try:
                self.run_calibration()
            except Exception as e:
                self._log(f"Initial calibration failed: {e}. Continuing with current settings.")
        
        consecutive_errors = 0
        max_consecutive_errors = 3  # Stop after this many failures in a row
        
        # Recalibration rate limiting
        last_recalibration_time = 0
        recalibration_cooldown_sec = 60  # Minimum 60 seconds between recalibrations
        recalibration_count = 0
        recalibration_window_start = time.time()
        max_recalibrations_per_window = 3  # Max 3 recalibrations per 10-minute window
        recalibration_window_sec = 600  # 10-minute window
        
        while self._capture_running and not self._stop_event.is_set():
            try:
                if self._scheduled_enabled:
                    from ..utils import is_within_scheduled_window
                    if not is_within_scheduled_window(
                        self._schedule_start,
                        self._schedule_end
                    ):
                        self._log("Outside scheduled capture window, waiting...")
                        time.sleep(60)
                        continue
                
                if self._auto_exposure and self._last_metadata:
                    self._adjust_auto_exposure()
                
                result = self.capture_frame()
                
                if result.success and result.image:
                    # Reset error counter on success
                    consecutive_errors = 0
                    
                    with self._image_lock:
                        self._last_image = result.image.copy()
                        self._last_metadata = result.metadata.copy() if result.metadata else {}
                    
                    # Check for drastic scene change requiring recalibration
                    if self._auto_exposure and result.metadata:
                        brightness = result.metadata.get('BRIGHTNESS', 0)
                        brightness_deviation = abs(brightness - self._target_brightness)
                        
                        # If brightness is way off (>100 from target), consider recalibration
                        if brightness_deviation > 100:
                            current_time = time.time()
                            
                            # Reset recalibration window if expired
                            if current_time - recalibration_window_start > recalibration_window_sec:
                                recalibration_count = 0
                                recalibration_window_start = current_time
                            
                            # Check rate limits
                            time_since_last = current_time - last_recalibration_time
                            can_recalibrate = (
                                time_since_last >= recalibration_cooldown_sec and
                                recalibration_count < max_recalibrations_per_window
                            )
                            
                            if can_recalibrate:
                                self._log(f"Drastic scene change detected (brightness={brightness:.0f}, target={self._target_brightness}) - running recalibration")
                                
                                if self._calibration_callback:
                                    self._calibration_callback(True)
                                
                                try:
                                    self.run_calibration()
                                    last_recalibration_time = time.time()
                                    recalibration_count += 1
                                except Exception as e:
                                    self._log(f"Recalibration failed: {e}")
                                
                                if self._calibration_callback:
                                    self._calibration_callback(False)
                                
                                # Skip this frame - next iteration will use calibrated exposure
                                continue
                            else:
                                if time_since_last < recalibration_cooldown_sec:
                                    wait_time = int(recalibration_cooldown_sec - time_since_last)
                                    self._log(f"Scene change detected but recalibration on cooldown ({wait_time}s remaining)")
                                else:
                                    self._log(f"Scene change detected but max recalibrations reached this window")
                    
                    if self._on_frame:
                        self._on_frame(result.image, result.metadata or {})
                        
                elif not result.success:
                    consecutive_errors += 1
                    error_msg = result.error or "Unknown capture error"
                    self._log(f"Capture failed ({consecutive_errors}/{max_consecutive_errors}): {error_msg}")
                    
                    if self._on_error:
                        self._on_error(error_msg)
                    
                    # Stop capture after too many consecutive errors
                    if consecutive_errors >= max_consecutive_errors:
                        self._log(f"Stopping capture after {consecutive_errors} consecutive errors")
                        self._capture_running = False
                        self._set_state(CameraState.ERROR)
                        break
                
                wait_time = max(0.1, self._capture_interval - self._exposure_ms/1000)
                self._stop_event.wait(wait_time)
                
            except Exception as e:
                consecutive_errors += 1
                self._log(f"Capture loop error ({consecutive_errors}/{max_consecutive_errors}): {e}")
                
                if self._on_error:
                    self._on_error(str(e))
                
                # Stop capture after too many consecutive errors
                if consecutive_errors >= max_consecutive_errors:
                    self._log(f"Stopping capture after {consecutive_errors} consecutive errors")
                    self._capture_running = False
                    self._set_state(CameraState.ERROR)
                    break
                    
                time.sleep(1)
        
        self._log("Capture loop ended")
    
    # =========================================================================
    # Auto-Exposure
    # =========================================================================
    
    def set_auto_exposure(
        self,
        enabled: bool,
        target_brightness: int = 100,
        max_exposure_ms: float = 30000.0
    ) -> bool:
        """Enable/disable software auto-exposure"""
        self._auto_exposure = enabled
        self._target_brightness = target_brightness
        self._max_exposure_ms = max_exposure_ms
        
        self._log(f"Auto-exposure: {'enabled' if enabled else 'disabled'}")
        if enabled:
            self._log(f"  Target brightness: {target_brightness}, Max: {max_exposure_ms}ms")
        
        return True
    
    def _adjust_auto_exposure(self) -> None:
        """Adjust exposure based on last captured image brightness"""
        if not self._last_metadata:
            self._log("Auto-exposure: No metadata available yet")
            return
        
        brightness = self._last_metadata.get('BRIGHTNESS', 0)
        if brightness == 0:
            self._log("Auto-exposure: Brightness is 0, cannot adjust")
            return
        
        target = self._target_brightness
        tolerance = 20
        
        if abs(brightness - target) <= tolerance:
            return
        
        ratio = target / max(1, brightness)
        ratio = max(0.5, min(2.0, ratio))
        
        new_exposure = self._exposure_ms * ratio
        new_exposure = max(self._min_exposure_ms, min(self._max_exposure_ms, new_exposure))
        
        if abs(new_exposure - self._exposure_ms) > 1:
            old_exp = self._exposure_ms
            self._exposure_ms = new_exposure
            self._log(f"Auto-exposure: {old_exp:.1f}ms -> {new_exposure:.1f}ms (brightness: {brightness:.0f}, target: {target})")
    
    def run_calibration(self) -> bool:
        """Run initial exposure calibration"""
        if not self._camera:
            return False
        
        self._is_calibrating = True
        if self._calibration_callback:
            self._calibration_callback(True)
        
        self._log("Starting exposure calibration...")
        
        try:
            self._exposure_ms = 1000.0
            prev_exposure = 0.0
            
            for iteration in range(10):
                result = self.capture_frame()
                if not result.success:
                    continue
                
                brightness = result.metadata.get('BRIGHTNESS', 0) if result.metadata else 0
                self._log(f"Calibration {iteration+1}: {self._exposure_ms:.1f}ms, brightness={brightness:.0f}")
                
                if abs(brightness - self._target_brightness) <= 20:
                    self._log("Calibration complete - target brightness achieved")
                    break
                
                # Check if we're stuck at max/min exposure
                at_limit = (
                    (self._exposure_ms >= self._max_exposure_ms and brightness < self._target_brightness) or
                    (self._exposure_ms <= self._min_exposure_ms and brightness > self._target_brightness)
                )
                if at_limit:
                    self._log(f"Calibration stopped - at exposure limit ({self._exposure_ms:.1f}ms), brightness={brightness:.0f}")
                    break
                
                # Check if exposure hasn't changed (stuck)
                if abs(self._exposure_ms - prev_exposure) < 1:
                    self._log(f"Calibration stopped - no further adjustment possible")
                    break
                prev_exposure = self._exposure_ms
                
                ratio = self._target_brightness / max(1, brightness)
                ratio = max(0.3, min(3.0, ratio))
                self._exposure_ms = max(
                    self._min_exposure_ms,
                    min(self._max_exposure_ms, self._exposure_ms * ratio)
                )
            
            return True
            
        finally:
            self._is_calibrating = False
            if self._calibration_callback:
                self._calibration_callback(False)
    
    # =========================================================================
    # Temperature & Cooling
    # =========================================================================
    
    def get_temperature(self) -> Optional[Dict[str, Any]]:
        """Get camera temperature info."""
        if not self._camera:
            return None
        
        try:
            return {
                'sensor_temp': self._safe_get('CCDTemperature', 0),
                'cooler_on': self._safe_get('CoolerOn', False),
                'cooler_setpoint': self._safe_get('SetCCDTemperature', 0),
                'cooler_power': self._safe_get('CoolerPower', 0),
            }
        except Exception:
            return None
    
    def set_cooler(self, enabled: bool, setpoint: Optional[float] = None) -> bool:
        """Control camera cooler."""
        if not self._camera:
            return False
        
        try:
            if setpoint is not None:
                self._camera.SetCCDTemperature = setpoint
                self._cooler_setpoint = setpoint
            
            self._camera.CoolerOn = enabled
            self._cooler_on = enabled
            
            msg = f"Cooler {'enabled' if enabled else 'disabled'}"
            if setpoint:
                msg += f" at {setpoint}°C"
            self._log(msg)
            return True
            
        except Exception as e:
            self._log(f"Error setting cooler: {e}")
            return False
    
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
        """Set calibration status callback"""
        self._calibration_callback = callback
    
    # =========================================================================
    # Progress & Status
    # =========================================================================
    
    def get_exposure_progress(self) -> Tuple[float, float]:
        """Get current exposure progress."""
        if not self._camera:
            return (0.0, 0.0)
        
        total_sec = self._exposure_ms / 1000.0
        
        try:
            percent = self._safe_get('PercentCompleted', 0)
            elapsed = (percent / 100.0) * total_sec
            return (elapsed, total_sec)
        except Exception:
            return (0.0, total_sec)
    
    def get_last_image(self) -> Tuple[Optional[Image.Image], Optional[Dict[str, Any]]]:
        """Get last captured image and metadata"""
        with self._image_lock:
            return self._last_image, self._last_metadata


# =============================================================================
# Module-level Functions
# =============================================================================

def check_ascom_availability() -> Dict[str, Any]:
    """Check ASCOM availability and return status info."""
    return {
        'available': ASCOM_AVAILABLE,
        'backend': ASCOM_BACKEND,
        'version': ASCOM_VERSION,
        'error': ASCOM_ERROR,
    }


def launch_ascom_chooser() -> Optional[str]:
    """Launch ASCOM Chooser dialog (Windows COM only)."""
    if ASCOM_BACKEND != "comtypes":
        return None
    
    try:
        chooser = comtypes.client.CreateObject("ASCOM.Utilities.Chooser")
        chooser.DeviceType = "Camera"
        device_id = chooser.Choose("")
        return device_id if device_id else None
    except Exception as e:
        print(f"Error launching ASCOM Chooser: {e}")
        return None
