"""
File/Directory Watch Camera Adapter

Provides a camera-like interface for directory watching mode.
Instead of capturing from hardware, this adapter watches a directory
for new image files and processes them as if they were camera captures.
"""
import os
import time
import threading
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
from ...watcher import FileWatcher
from ...processor import parse_sidecar_file, derive_metadata


class FileWatchAdapter(CameraInterface):
    """
    Adapter for directory watch mode.
    
    This adapter simulates a camera by watching a directory for new images.
    It conforms to the CameraInterface so the rest of PFR Sentinel can treat
    it uniformly with real cameras.
    
    Key differences from real cameras:
    - No exposure/gain control (images come from external source)
    - Metadata comes from sidecar files (.txt) not camera
    - "Capture" is triggered by file system events, not explicit calls
    """
    
    # File adapter capabilities (static)
    _CAPABILITIES = CameraCapabilities(
        backend_name="File",
        backend_version="1.0.0",
        
        # Connection
        supports_hot_plug=False,
        supports_multiple_cameras=False,  # Single directory at a time
        requires_sdk_path=False,
        
        # Exposure - NOT supported (images from files)
        supports_exposure_control=False,
        min_exposure_ms=0,
        max_exposure_ms=0,
        supports_auto_exposure=False,
        
        # Gain - NOT supported
        supports_gain_control=False,
        min_gain=0,
        max_gain=0,
        supports_auto_gain=False,
        
        # White Balance - NOT supported
        supports_white_balance=False,
        supports_auto_white_balance=False,
        min_wb_value=0,
        max_wb_value=0,
        
        # Image Settings - NOT supported
        supports_binning=False,
        max_binning=1,
        supports_roi=False,
        supports_flip=False,
        supports_offset=False,
        
        # Bit Depth - from source files
        supports_raw8=True,
        supports_raw16=True,  # Depends on source file
        native_bit_depth=8,
        
        # Color/Bayer - files are already debayered
        is_color_camera=True,
        bayer_pattern=None,  # Already processed
        supports_debayering=False,  # Not needed
        
        # Temperature - NOT supported
        supports_temperature_reading=False,
        supports_cooling=False,
        supports_cooler_control=False,
        
        # Scheduling - supported (can schedule processing)
        supports_scheduled_capture=False,
        
        # Metadata - from sidecar files
        provides_metadata=True,
        metadata_fields=[
            'CAMERA', 'EXPOSURE', 'GAIN', 'TEMP', 'RES',
            'DATETIME', 'FILENAME', 'SESSION'
        ],
        
        # Performance
        supports_streaming=False,
        max_fps=0,  # Event-driven, not fps-based
    )
    
    def __init__(
        self,
        config: Optional[Dict[str, Any]] = None,
        logger: Optional[Callable[[str], None]] = None,
    ):
        """
        Initialize File Watch adapter.
        
        Args:
            config: Config dict with watch_directory, watch_recursive, etc.
            logger: Callback for log messages
        """
        self._config = config or {}
        self._logger = logger
        
        # Internal state
        self._state = CameraState.DISCONNECTED
        self._camera_info: Optional[CameraInfo] = None
        
        # Watcher instance
        self._watcher: Optional[FileWatcher] = None
        self._watch_directory: str = ""
        
        # Callbacks
        self._on_frame: Optional[Callable[[Image.Image, Dict[str, Any]], None]] = None
        self._on_error: Optional[Callable[[str], None]] = None
        self._state_callback: Optional[Callable[[CameraState], None]] = None
        
        # Processing queue for captured images
        self._last_image: Optional[Image.Image] = None
        self._last_metadata: Optional[Dict[str, Any]] = None
        self._image_lock = threading.Lock()
    
    def _log(self, message: str) -> None:
        """Log message via callback"""
        if self._logger:
            self._logger(message)
        else:
            print(f"[File] {message}")
    
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
        return self._CAPABILITIES
    
    @property
    def camera_info(self) -> Optional[CameraInfo]:
        return self._camera_info
    
    @property
    def is_connected(self) -> bool:
        return self._watcher is not None
    
    @property
    def is_capturing(self) -> bool:
        return self._watcher is not None and self._watcher.is_running()
    
    # =========================================================================
    # Initialization & Detection
    # =========================================================================
    
    def initialize(self) -> bool:
        """Initialize file backend (no-op, just validates config)"""
        self._log("Initializing File Watch backend...")
        
        watch_dir = self._config.get('watch_directory', '')
        if not watch_dir:
            self._log("No watch directory configured")
            return True  # Still return True - directory can be set later
        
        if not os.path.exists(watch_dir):
            self._log(f"Watch directory does not exist: {watch_dir}")
            return True  # Directory might be created later
        
        self._log(f"File Watch backend ready (directory: {watch_dir})")
        return True
    
    def detect_cameras(self) -> List[CameraInfo]:
        """
        'Detect' the watch directory as a camera source.
        
        Returns a single CameraInfo representing the configured directory.
        """
        watch_dir = self._config.get('watch_directory', '')
        
        if not watch_dir:
            self._log("No watch directory configured")
            return []
        
        if not os.path.exists(watch_dir):
            self._log(f"Watch directory not found: {watch_dir}")
            return []
        
        # Create camera info for the directory
        dir_name = os.path.basename(watch_dir) or watch_dir
        self._camera_info = CameraInfo(
            index=0,
            name=f"Directory: {dir_name}",
            backend="File",
            device_id=watch_dir,
        )
        
        self._log(f"Found watch directory: {watch_dir}")
        return [self._camera_info]
    
    # =========================================================================
    # Connection Management
    # =========================================================================
    
    def connect(self, camera_index: int = 0, settings: Optional[Dict[str, Any]] = None) -> bool:
        """
        'Connect' to the watch directory.
        
        This prepares the watcher but doesn't start it yet (use start_capture).
        """
        watch_dir = self._config.get('watch_directory', '')
        
        if not watch_dir:
            self._log("Cannot connect: no watch directory configured")
            return False
        
        if not os.path.exists(watch_dir):
            self._log(f"Cannot connect: directory does not exist: {watch_dir}")
            return False
        
        self._set_state(CameraState.CONNECTING)
        
        try:
            self._watch_directory = watch_dir
            
            # Update camera info
            dir_name = os.path.basename(watch_dir) or watch_dir
            self._camera_info = CameraInfo(
                index=0,
                name=f"Directory: {dir_name}",
                backend="File",
                device_id=watch_dir,
            )
            
            self._set_state(CameraState.CONNECTED)
            self._log(f"Connected to watch directory: {watch_dir}")
            return True
            
        except Exception as e:
            self._log(f"Error connecting to directory: {e}")
            self._set_state(CameraState.ERROR)
            return False
    
    def disconnect(self) -> None:
        """Disconnect (stop watcher if running)"""
        if self._watcher and self._watcher.is_running():
            self._watcher.stop()
        
        self._watcher = None
        self._watch_directory = ""
        self._camera_info = None
        self._set_state(CameraState.DISCONNECTED)
        self._log("Disconnected from watch directory")
    
    def reconnect(self) -> bool:
        """Reconnect to watch directory"""
        watch_dir = self._watch_directory or self._config.get('watch_directory', '')
        
        if not watch_dir or not os.path.exists(watch_dir):
            return False
        
        self._config['watch_directory'] = watch_dir
        return self.connect(0)
    
    # =========================================================================
    # Configuration
    # =========================================================================
    
    def configure(self, settings: Dict[str, Any]) -> None:
        """
        Configure watch settings.
        
        Supported settings:
            - watch_directory: Directory path to watch
            - watch_recursive: Whether to watch subdirectories
        """
        if 'watch_directory' in settings:
            self._config['watch_directory'] = settings['watch_directory']
        if 'watch_recursive' in settings:
            self._config['watch_recursive'] = settings['watch_recursive']
    
    def get_current_settings(self) -> Dict[str, Any]:
        """Get current watch settings"""
        return {
            'watch_directory': self._config.get('watch_directory', ''),
            'watch_recursive': self._config.get('watch_recursive', True),
        }
    
    # =========================================================================
    # Capture Operations
    # =========================================================================
    
    def capture_frame(self) -> CaptureResult:
        """
        Get the most recently processed image.
        
        Note: For file watch mode, images arrive asynchronously via file events.
        This method returns the last image that was processed.
        """
        with self._image_lock:
            if self._last_image is None:
                return CaptureResult(success=False, error="No image available yet")
            
            return CaptureResult(
                success=True,
                image=self._last_image.copy(),
                metadata=self._last_metadata.copy() if self._last_metadata else {},
                capture_timestamp=self._last_metadata.get('DATETIME') if self._last_metadata else None,
            )
    
    def start_capture(
        self,
        on_frame: Callable[[Image.Image, Dict[str, Any]], None],
        on_error: Optional[Callable[[str], None]] = None,
        interval_sec: float = 5.0  # Ignored for file watch
    ) -> bool:
        """Start watching directory for new images"""
        if not self._watch_directory or not os.path.exists(self._watch_directory):
            self._log("Cannot start: watch directory not configured or doesn't exist")
            return False
        
        self._on_frame = on_frame
        self._on_error = on_error
        
        try:
            # Create and start watcher
            self._watcher = FileWatcher(
                config=self._config,
                on_image_processed=self._on_image_processed
            )
            self._watcher.start()
            
            self._set_state(CameraState.CAPTURING)
            self._log(f"Started watching: {self._watch_directory}")
            return True
            
        except Exception as e:
            self._log(f"Error starting watcher: {e}")
            if self._on_error:
                self._on_error(str(e))
            return False
    
    def stop_capture(self) -> None:
        """Stop watching directory"""
        if self._watcher:
            self._watcher.stop()
            self._watcher = None
        
        if self._watch_directory:
            self._set_state(CameraState.CONNECTED)
        else:
            self._set_state(CameraState.DISCONNECTED)
        
        self._log("Stopped watching directory")
    
    def _on_image_processed(self, output_path: str, processed_img: Optional[Image.Image]) -> None:
        """Callback from watcher when an image is processed"""
        try:
            if processed_img is None:
                # Load from output path
                processed_img = Image.open(output_path)
            
            # Try to get metadata from sidecar file
            metadata = self._extract_metadata(output_path)
            
            # Store for capture_frame()
            with self._image_lock:
                self._last_image = processed_img.copy()
                self._last_metadata = metadata
            
            # Notify callback
            if self._on_frame:
                self._on_frame(processed_img, metadata)
                
        except Exception as e:
            self._log(f"Error processing image: {e}")
            if self._on_error:
                self._on_error(str(e))
    
    def _extract_metadata(self, image_path: str) -> Dict[str, Any]:
        """Extract metadata from sidecar file or derive from filename"""
        metadata = {}
        
        # Try sidecar file (same name with .txt extension)
        base_path = os.path.splitext(image_path)[0]
        sidecar_path = base_path + ".txt"
        
        if os.path.exists(sidecar_path):
            metadata = parse_sidecar_file(sidecar_path)
        
        # Derive additional metadata
        filename = os.path.basename(image_path)
        session = os.path.basename(os.path.dirname(image_path))
        metadata = derive_metadata(metadata, filename, session)
        
        return metadata
    
    # =========================================================================
    # Unsupported Operations (no-op implementations)
    # =========================================================================
    
    def set_auto_exposure(self, enabled: bool, target_brightness: int = 100,
                          max_exposure_ms: float = 30000.0) -> bool:
        """Not supported for file watch mode"""
        return False
    
    def run_calibration(self) -> bool:
        """Not supported for file watch mode"""
        return False
    
    def get_temperature(self) -> Optional[Dict[str, Any]]:
        """Not supported for file watch mode"""
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
        """Not used for file watch mode"""
        pass
    
    # =========================================================================
    # Exposure Progress
    # =========================================================================
    
    def get_exposure_progress(self) -> Tuple[float, float]:
        """Not applicable for file watch mode"""
        return (0.0, 0.0)
