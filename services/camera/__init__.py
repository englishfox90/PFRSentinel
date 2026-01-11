"""
Camera abstraction layer for PFR Sentinel.

Provides a unified interface for different camera backends:
- ZWO ASI cameras (via zwoasi SDK)
- File/Directory watch mode
- ASCOM cameras (stub, requires alpyca/comtypes)

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

# Adapter imports for direct access if needed
from .zwo_adapter import ZWOCameraAdapter
from .file_adapter import FileWatchAdapter
from .ascom_adapter import ASCOMCameraAdapter, check_ascom_availability

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
]
