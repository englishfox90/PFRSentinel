"""
Camera abstraction layer for PFR Sentinel.

Provides a unified interface for different camera backends:
- ZWO ASI cameras (via zwoasi SDK)
- File/Directory watch mode
- ASCOM cameras (stub, requires alpyca/comtypes)

Package structure:
    services/camera/
    ├── __init__.py         # This file - exports
    ├── interface.py        # CameraInterface ABC
    ├── factory.py          # create_camera(), get_available_backends()
    ├── utils.py            # Shared utilities (debayer, brightness, etc.)
    ├── zwo/                # ZWO ASI backend
    │   ├── camera.py       # ZWOCamera class
    │   ├── connection.py   # SDK init, detection
    │   ├── calibration.py  # Auto-exposure algorithms
    │   └── adapter.py      # ZWOCameraAdapter
    ├── file/               # File watch backend
    │   └── adapter.py      # FileWatchAdapter
    └── ascom/              # ASCOM backend (stub)
        └── adapter.py      # ASCOMCameraAdapter

Usage:
    from services.camera import create_camera, CameraInterface
    
    # Create camera from backend name
    camera = create_camera('zwo', config=app_config)
    camera.initialize()
    cameras = camera.detect_cameras()
    camera.connect(0)
    result = camera.capture_frame()
    
    # Or create from app config
    from services.camera import create_camera_from_config
    camera = create_camera_from_config(app_config)
"""

from .interface import (
    CameraInterface,
    CameraCapabilities,
    CameraInfo,
    CameraState,
    CaptureResult,
)

from .factory import (
    create_camera,
    create_camera_from_config,
    get_available_backends,
    get_backend_info,
    detect_all_cameras,
)

# Adapter imports from subpackages
from .zwo import ZWOCameraAdapter
from .file import FileWatchAdapter
from .ascom import ASCOMCameraAdapter, check_ascom_availability

# Also export shared utilities
from .utils import (
    calculate_brightness,
    check_clipping,
    debayer_raw_image,
    apply_white_balance,
    calculate_image_stats,
    is_within_scheduled_window,
)

__all__ = [
    # Interface and data classes
    'CameraInterface',
    'CameraCapabilities',
    'CameraInfo',
    'CameraState',
    'CaptureResult',
    # Factory functions
    'create_camera',
    'create_camera_from_config',
    'get_available_backends',
    'get_backend_info',
    'detect_all_cameras',
    # Adapters
    'ZWOCameraAdapter',
    'FileWatchAdapter',
    'ASCOMCameraAdapter',
    'check_ascom_availability',
    # Utilities
    'calculate_brightness',
    'check_clipping',
    'debayer_raw_image',
    'apply_white_balance',
    'calculate_image_stats',
    'is_within_scheduled_window',
]
