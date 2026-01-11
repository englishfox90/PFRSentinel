"""
Backward compatibility shim for camera_utils.

This file has been moved to services/camera/utils.py
All imports are re-exported here for backward compatibility.
"""
# Re-export everything from new location
from .camera.utils import (
    simple_debayer_rggb,
    is_within_scheduled_window,
    calculate_brightness,
    check_clipping,
    debayer_raw_image,
    apply_white_balance,
    calculate_image_stats,
)

__all__ = [
    'simple_debayer_rggb',
    'is_within_scheduled_window',
    'calculate_brightness',
    'check_clipping',
    'debayer_raw_image',
    'apply_white_balance',
    'calculate_image_stats',
]
