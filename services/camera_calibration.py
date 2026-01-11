"""
Backward compatibility shim for camera_calibration.

This file has been moved to services/camera/zwo/calibration.py
All imports are re-exported here for backward compatibility.
"""
# Re-export from new location
from .camera.zwo.calibration import CameraCalibration

__all__ = ['CameraCalibration']
