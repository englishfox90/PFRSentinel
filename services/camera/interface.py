"""
Camera Interface Abstraction Layer

Defines the abstract base class and data structures for camera backends.
All camera implementations (ZWO, ASCOM, File) must implement this interface.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Optional, Dict, Any, List, Callable, Tuple
from PIL import Image
import numpy as np


class CameraState(Enum):
    """Camera connection and capture state"""
    DISCONNECTED = auto()      # Not connected to any camera
    CONNECTING = auto()        # Connection in progress
    CONNECTED = auto()         # Connected but not capturing
    CAPTURING = auto()         # Actively capturing frames
    CALIBRATING = auto()       # Running auto-exposure calibration
    ERROR = auto()             # Error state (requires reconnection)
    SCHEDULED_IDLE = auto()    # Connected but paused (outside scheduled window)


@dataclass
class CameraCapabilities:
    """
    Describes what features a camera backend supports.
    
    PFR Sentinel uses this to adapt its UI and behavior based on
    what the connected camera can do. Features not supported by
    a backend will be disabled/hidden in the UI.
    """
    # === Identity ===
    backend_name: str                    # "ZWO", "ASCOM", "File"
    backend_version: str = "1.0.0"       # Backend implementation version
    
    # === Connection ===
    supports_hot_plug: bool = False      # Can detect camera connect/disconnect
    supports_multiple_cameras: bool = False  # Can list and select from multiple cameras
    requires_sdk_path: bool = False      # Needs path to SDK/DLL
    
    # === Exposure Control ===
    supports_exposure_control: bool = True   # Can set exposure time
    min_exposure_ms: float = 0.001           # Minimum exposure (milliseconds)
    max_exposure_ms: float = 3600000.0       # Maximum exposure (milliseconds, 1 hour)
    supports_auto_exposure: bool = False     # Has auto-exposure algorithm
    
    # === Gain Control ===
    supports_gain_control: bool = True       # Can set gain
    min_gain: int = 0
    max_gain: int = 600
    supports_auto_gain: bool = False         # Has auto-gain
    
    # === White Balance ===
    supports_white_balance: bool = False     # Can adjust WB R/B channels
    supports_auto_white_balance: bool = False  # Has auto white balance
    min_wb_value: int = 1
    max_wb_value: int = 99
    
    # === Image Settings ===
    supports_binning: bool = False           # Hardware binning
    max_binning: int = 1
    supports_roi: bool = False               # Region of interest
    supports_flip: bool = False              # Image flip (H/V/Both)
    supports_offset: bool = False            # Brightness offset
    
    # === Bit Depth ===
    supports_raw8: bool = True               # 8-bit capture
    supports_raw16: bool = False             # 16-bit capture
    native_bit_depth: int = 8                # Camera's ADC bit depth
    
    # === Color/Bayer ===
    is_color_camera: bool = True             # Color vs mono
    bayer_pattern: Optional[str] = None      # "RGGB", "BGGR", "GRBG", "GBRG", or None for mono
    supports_debayering: bool = True         # Backend handles debayer
    
    # === Temperature ===
    supports_temperature_reading: bool = False   # Can read sensor temp
    supports_cooling: bool = False               # Has active cooling
    supports_cooler_control: bool = False        # Can set target temp
    
    # === Scheduling ===
    supports_scheduled_capture: bool = True      # Can schedule capture windows
    
    # === Metadata ===
    provides_metadata: bool = True               # Provides EXIF-like metadata
    metadata_fields: List[str] = field(default_factory=lambda: [
        'CAMERA', 'EXPOSURE', 'GAIN', 'TEMP', 'RES', 'DATETIME'
    ])
    
    # === Performance ===
    supports_streaming: bool = False         # Video/streaming mode
    max_fps: float = 1.0                     # Max frames per second
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert capabilities to dictionary for serialization"""
        return {
            'backend_name': self.backend_name,
            'backend_version': self.backend_version,
            'supports_hot_plug': self.supports_hot_plug,
            'supports_multiple_cameras': self.supports_multiple_cameras,
            'requires_sdk_path': self.requires_sdk_path,
            'supports_exposure_control': self.supports_exposure_control,
            'min_exposure_ms': self.min_exposure_ms,
            'max_exposure_ms': self.max_exposure_ms,
            'supports_auto_exposure': self.supports_auto_exposure,
            'supports_gain_control': self.supports_gain_control,
            'min_gain': self.min_gain,
            'max_gain': self.max_gain,
            'supports_auto_gain': self.supports_auto_gain,
            'supports_white_balance': self.supports_white_balance,
            'supports_auto_white_balance': self.supports_auto_white_balance,
            'min_wb_value': self.min_wb_value,
            'max_wb_value': self.max_wb_value,
            'supports_binning': self.supports_binning,
            'max_binning': self.max_binning,
            'supports_roi': self.supports_roi,
            'supports_flip': self.supports_flip,
            'supports_offset': self.supports_offset,
            'supports_raw8': self.supports_raw8,
            'supports_raw16': self.supports_raw16,
            'native_bit_depth': self.native_bit_depth,
            'is_color_camera': self.is_color_camera,
            'bayer_pattern': self.bayer_pattern,
            'supports_debayering': self.supports_debayering,
            'supports_temperature_reading': self.supports_temperature_reading,
            'supports_cooling': self.supports_cooling,
            'supports_cooler_control': self.supports_cooler_control,
            'supports_scheduled_capture': self.supports_scheduled_capture,
            'provides_metadata': self.provides_metadata,
            'metadata_fields': self.metadata_fields,
            'supports_streaming': self.supports_streaming,
            'max_fps': self.max_fps,
        }


@dataclass
class CameraInfo:
    """Information about a detected/connected camera"""
    index: int                           # Camera index in backend's list
    name: str                            # Display name (e.g., "ZWO ASI676MC")
    backend: str                         # Backend type ("ZWO", "ASCOM", "File")
    device_id: Optional[str] = None      # Unique identifier (serial, path, etc.)
    
    # Hardware info (may be None if not available)
    max_width: Optional[int] = None
    max_height: Optional[int] = None
    pixel_size_um: Optional[float] = None
    is_color: bool = True
    bit_depth: int = 8
    
    # ASCOM-specific
    driver_id: Optional[str] = None      # ASCOM ProgID (e.g., "ASCOM.ASICamera2.Camera")
    
    def __str__(self) -> str:
        return f"{self.name} ({self.backend})"


@dataclass 
class CaptureResult:
    """Result of a frame capture operation"""
    success: bool
    image: Optional[Image.Image] = None      # PIL Image (RGB)
    metadata: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    
    # Raw data for advanced use (e.g., dev mode, FITS saving)
    raw_rgb_no_wb: Optional[np.ndarray] = None   # Pre-white-balance RGB uint8
    raw_rgb_16bit: Optional[np.ndarray] = None   # Full uint16 RGB (RAW16 mode only)
    
    # Timing info
    exposure_time_ms: float = 0.0
    capture_timestamp: Optional[str] = None


class CameraInterface(ABC):
    """
    Abstract base class for camera backends.
    
    All camera implementations must inherit from this class and implement
    the abstract methods. The interface is designed to be:
    
    1. Stateful - tracks connection state and settings
    2. Thread-safe - can be called from UI and worker threads
    3. Recoverable - handles disconnects and errors gracefully
    4. Observable - provides callbacks for state changes
    
    Lifecycle:
        1. __init__() - Create instance with config
        2. initialize() - Initialize SDK/driver
        3. detect_cameras() - List available cameras
        4. connect(index) - Connect to specific camera
        5. configure(settings) - Apply camera settings
        6. start_capture(callback) - Begin continuous capture
        7. stop_capture() - Stop capture
        8. disconnect() - Disconnect from camera
    """
    
    # =========================================================================
    # Abstract Properties
    # =========================================================================
    
    @property
    @abstractmethod
    def state(self) -> CameraState:
        """Current camera state"""
        pass
    
    @property
    @abstractmethod
    def capabilities(self) -> CameraCapabilities:
        """Get backend capabilities (static, doesn't change after init)"""
        pass
    
    @property
    @abstractmethod
    def camera_info(self) -> Optional[CameraInfo]:
        """Info about currently connected camera (None if not connected)"""
        pass
    
    @property
    @abstractmethod
    def is_connected(self) -> bool:
        """Whether camera is currently connected"""
        pass
    
    @property
    @abstractmethod
    def is_capturing(self) -> bool:
        """Whether continuous capture is active"""
        pass
    
    # =========================================================================
    # Initialization & Detection
    # =========================================================================
    
    @abstractmethod
    def initialize(self) -> bool:
        """
        Initialize the camera backend (SDK, driver, etc.)
        
        Returns:
            True if initialization successful
        """
        pass
    
    @abstractmethod
    def detect_cameras(self) -> List[CameraInfo]:
        """
        Detect available cameras.
        
        Returns:
            List of CameraInfo for each detected camera
        """
        pass
    
    # =========================================================================
    # Connection Management
    # =========================================================================
    
    @abstractmethod
    def connect(self, camera_index: int = 0, settings: Optional[Dict[str, Any]] = None) -> bool:
        """
        Connect to a specific camera.
        
        Args:
            camera_index: Index from detect_cameras() list
            settings: Optional initial settings to apply
            
        Returns:
            True if connection successful
        """
        pass
    
    @abstractmethod
    def disconnect(self) -> None:
        """Disconnect from camera gracefully"""
        pass
    
    @abstractmethod
    def reconnect(self) -> bool:
        """
        Attempt to reconnect after disconnect/error.
        
        Returns:
            True if reconnection successful
        """
        pass
    
    # =========================================================================
    # Configuration
    # =========================================================================
    
    @abstractmethod
    def configure(self, settings: Dict[str, Any]) -> None:
        """
        Apply camera settings.
        
        Standard settings keys (check capabilities for support):
            - exposure_ms: Exposure time in milliseconds
            - gain: Gain value
            - wb_r: White balance red (1-99)
            - wb_b: White balance blue (1-99)
            - wb_mode: "manual", "asi_auto", "gray_world"
            - offset: Brightness offset
            - flip: 0=None, 1=H, 2=V, 3=Both
            - binning: Binning factor
            - use_raw16: Use 16-bit mode
            
        Args:
            settings: Dictionary of settings to apply
        """
        pass
    
    @abstractmethod
    def get_current_settings(self) -> Dict[str, Any]:
        """
        Get current camera settings.
        
        Returns:
            Dictionary of current settings
        """
        pass
    
    # =========================================================================
    # Capture Operations
    # =========================================================================
    
    @abstractmethod
    def capture_frame(self) -> CaptureResult:
        """
        Capture a single frame.
        
        Returns:
            CaptureResult with image and metadata
        """
        pass
    
    @abstractmethod
    def start_capture(
        self,
        on_frame: Callable[[Image.Image, Dict[str, Any]], None],
        on_error: Optional[Callable[[str], None]] = None,
        interval_sec: float = 5.0
    ) -> bool:
        """
        Start continuous capture loop.
        
        Args:
            on_frame: Callback for each captured frame (image, metadata)
            on_error: Optional callback for errors
            interval_sec: Seconds between captures
            
        Returns:
            True if capture started successfully
        """
        pass
    
    @abstractmethod
    def stop_capture(self) -> None:
        """Stop continuous capture"""
        pass
    
    # =========================================================================
    # Auto-Exposure (Optional)
    # =========================================================================
    
    def set_auto_exposure(self, enabled: bool, target_brightness: int = 100,
                          max_exposure_ms: float = 30000.0) -> bool:
        """
        Enable/disable auto-exposure.
        
        Args:
            enabled: Whether to enable auto-exposure
            target_brightness: Target mean brightness (0-255)
            max_exposure_ms: Maximum exposure limit
            
        Returns:
            True if setting changed successfully
        """
        # Default implementation - override in backends that support it
        return False
    
    def run_calibration(self) -> bool:
        """
        Run rapid auto-exposure calibration.
        
        Returns:
            True if calibration completed successfully
        """
        # Default implementation - override in backends that support it
        return False
    
    # =========================================================================
    # Scheduling (Optional)
    # =========================================================================
    
    def set_schedule(self, enabled: bool, start_time: str = "17:00", 
                     end_time: str = "09:00") -> None:
        """
        Configure scheduled capture window.
        
        Args:
            enabled: Whether scheduling is enabled
            start_time: Start time "HH:MM"
            end_time: End time "HH:MM"
        """
        # Default implementation - override in backends that support it
        pass
    
    def is_within_schedule(self) -> bool:
        """
        Check if current time is within scheduled capture window.
        
        Returns:
            True if within window (or scheduling disabled)
        """
        # Default: always within window
        return True
    
    # =========================================================================
    # Temperature (Optional)
    # =========================================================================
    
    def get_temperature(self) -> Optional[Dict[str, Any]]:
        """
        Get camera sensor temperature.
        
        Returns:
            Dict with 'celsius', 'fahrenheit', 'display' or None if not supported
        """
        return None
    
    def set_cooler(self, enabled: bool, target_celsius: Optional[float] = None) -> bool:
        """
        Control camera cooler.
        
        Args:
            enabled: Whether to enable cooling
            target_celsius: Target temperature
            
        Returns:
            True if setting changed successfully
        """
        return False
    
    # =========================================================================
    # Callbacks
    # =========================================================================
    
    def set_log_callback(self, callback: Optional[Callable[[str], None]]) -> None:
        """Set callback for log messages"""
        pass
    
    def set_state_callback(self, callback: Optional[Callable[[CameraState], None]]) -> None:
        """Set callback for state changes"""
        pass
    
    def set_calibration_callback(self, callback: Optional[Callable[[bool], None]]) -> None:
        """Set callback for calibration start/end"""
        pass
    
    # =========================================================================
    # Exposure Progress (for long exposures)
    # =========================================================================
    
    def get_exposure_progress(self) -> Tuple[float, float]:
        """
        Get current exposure progress for long exposures.
        
        Returns:
            Tuple of (elapsed_seconds, remaining_seconds)
        """
        return (0.0, 0.0)
