"""
Backward compatibility shim for zwo_camera.

This file has been moved to services/camera/zwo/camera.py
All imports are re-exported here for backward compatibility.
"""
# Re-export from new location
from .camera.zwo.camera import ZWOCamera

__all__ = ['ZWOCamera']
