"""
Backward compatibility shim for camera_connection.

This file has been moved to services/camera/zwo/connection.py
All imports are re-exported here for backward compatibility.
"""
# Re-export from new location
from .camera.zwo.connection import CameraConnection

__all__ = ['CameraConnection']
