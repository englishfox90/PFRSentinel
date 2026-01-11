"""
Camera Factory

Factory module for creating camera adapters based on configuration.
Provides a unified way to instantiate the correct camera backend.
"""
from typing import Optional, Dict, Any, List, Callable, Type

from .interface import CameraInterface, CameraInfo


# Available backends registry
_BACKENDS: Dict[str, Type[CameraInterface]] = {}


def _register_backends():
    """Register available camera backends (lazy loading)"""
    global _BACKENDS
    
    if _BACKENDS:
        return  # Already registered
    
    # ZWO ASI cameras
    try:
        from .zwo import ZWOCameraAdapter
        _BACKENDS['zwo'] = ZWOCameraAdapter
        _BACKENDS['asi'] = ZWOCameraAdapter  # Alias
    except ImportError:
        pass
    
    # File/Directory watch mode
    try:
        from .file import FileWatchAdapter
        _BACKENDS['file'] = FileWatchAdapter
        _BACKENDS['watch'] = FileWatchAdapter  # Alias
        _BACKENDS['directory'] = FileWatchAdapter  # Alias
    except ImportError:
        pass
    
    # ASCOM cameras
    try:
        from .ascom import ASCOMCameraAdapter
        from .ascom.adapter import ASCOM_AVAILABLE
        if ASCOM_AVAILABLE:
            _BACKENDS['ascom'] = ASCOMCameraAdapter
            _BACKENDS['alpaca'] = ASCOMCameraAdapter  # Alias
    except ImportError:
        pass


def get_available_backends() -> List[str]:
    """
    Get list of available camera backend names.
    
    Returns:
        List of backend names that can be used with create_camera()
    """
    _register_backends()
    
    # Return unique backend names (no aliases)
    unique = set()
    seen_classes = set()
    
    for name, cls in _BACKENDS.items():
        if cls not in seen_classes:
            seen_classes.add(cls)
            unique.add(name)
    
    return sorted(unique)


def get_backend_info() -> Dict[str, Dict[str, Any]]:
    """
    Get detailed information about available backends.
    
    Returns:
        Dict mapping backend name to info dict with:
            - name: Display name
            - description: Short description
            - available: Whether backend is functional
            - aliases: Alternative names for this backend
    """
    _register_backends()
    
    info = {}
    
    # ZWO
    if 'zwo' in _BACKENDS:
        info['zwo'] = {
            'name': 'ZWO ASI',
            'description': 'ZWO ASI astronomy cameras via native SDK',
            'available': True,
            'aliases': ['asi'],
        }
    
    # File watch
    if 'file' in _BACKENDS:
        info['file'] = {
            'name': 'Directory Watch',
            'description': 'Watch directory for new images (no hardware)',
            'available': True,
            'aliases': ['watch', 'directory'],
        }
    
    # ASCOM
    if 'ascom' in _BACKENDS:
        from .ascom import check_ascom_availability
        ascom_status = check_ascom_availability()
        info['ascom'] = {
            'name': 'ASCOM',
            'description': 'ASCOM-compatible cameras via Alpaca or COM',
            'available': ascom_status['available'],
            'aliases': ['alpaca'],
            'backend': ascom_status.get('backend'),
            'error': ascom_status.get('error'),
        }
    else:
        info['ascom'] = {
            'name': 'ASCOM',
            'description': 'ASCOM-compatible cameras (not installed)',
            'available': False,
            'aliases': ['alpaca'],
            'error': 'Install alpyca or comtypes for ASCOM support',
        }
    
    return info


def create_camera(
    backend: str,
    config: Optional[Dict[str, Any]] = None,
    logger: Optional[Callable[[str], None]] = None,
) -> CameraInterface:
    """
    Create a camera adapter for the specified backend.
    
    Args:
        backend: Backend name ('zwo', 'file', 'ascom', etc.)
        config: Configuration dict to pass to adapter
        logger: Log callback function
        
    Returns:
        CameraInterface implementation for the requested backend
        
    Raises:
        ValueError: If backend is not available or not recognized
    """
    _register_backends()
    
    backend_lower = backend.lower().strip()
    
    if backend_lower not in _BACKENDS:
        available = get_available_backends()
        raise ValueError(
            f"Unknown camera backend: '{backend}'. "
            f"Available backends: {', '.join(available)}"
        )
    
    adapter_class = _BACKENDS[backend_lower]
    return adapter_class(config=config, logger=logger)


def create_camera_from_config(
    config: Dict[str, Any],
    logger: Optional[Callable[[str], None]] = None,
) -> CameraInterface:
    """
    Create camera adapter based on application config.
    
    Reads 'capture_mode' from config to determine backend:
        - 'watch' -> FileWatchAdapter
        - 'camera' -> ZWOCameraAdapter (default for hardware)
        - 'ascom' -> ASCOMCameraAdapter
        
    Args:
        config: Application config dict
        logger: Log callback function
        
    Returns:
        Appropriate CameraInterface for the configured mode
    """
    capture_mode = config.get('capture_mode', 'watch')
    
    # Map capture mode to backend
    if capture_mode == 'watch':
        return create_camera('file', config, logger)
    elif capture_mode == 'camera':
        # Check for ASCOM override
        if config.get('camera_backend') == 'ascom':
            return create_camera('ascom', config, logger)
        # Default to ZWO for camera mode
        return create_camera('zwo', config, logger)
    elif capture_mode == 'ascom':
        return create_camera('ascom', config, logger)
    else:
        # Fall back to file mode for unknown
        return create_camera('file', config, logger)


def detect_all_cameras(
    logger: Optional[Callable[[str], None]] = None,
) -> Dict[str, List[CameraInfo]]:
    """
    Detect cameras from all available backends.
    
    Args:
        logger: Log callback function
        
    Returns:
        Dict mapping backend name to list of detected CameraInfo
    """
    _register_backends()
    
    results = {}
    
    for backend_name in get_available_backends():
        try:
            adapter = create_camera(backend_name, logger=logger)
            
            if adapter.initialize():
                cameras = adapter.detect_cameras()
                if cameras:
                    results[backend_name] = cameras
        except Exception as e:
            if logger:
                logger(f"Error detecting {backend_name} cameras: {e}")
    
    return results
