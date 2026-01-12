"""
ASCOM Camera backend for PFR Sentinel.

Provides ASCOM-compatible camera support via:
- alpyca (recommended): Cross-platform ASCOM Alpaca REST API client
- comtypes: Windows COM interface for local ASCOM drivers

Features:
- Camera detection via Alpaca discovery or ASCOM Chooser
- Full exposure control (exposure, gain, offset, binning)
- Temperature monitoring and cooler control
- Software auto-exposure
- Color/Bayer camera debayering
- Continuous capture with callbacks
"""

from .adapter import (
    ASCOMCameraAdapter,
    check_ascom_availability,
    launch_ascom_chooser,
    ASCOM_AVAILABLE,
    ASCOM_BACKEND,
    ASCOM_VERSION,
    ASCOM_ERROR,
    SensorType,
    ASCOMCameraState,
)

__all__ = [
    'ASCOMCameraAdapter',
    'check_ascom_availability',
    'launch_ascom_chooser',
    'ASCOM_AVAILABLE',
    'ASCOM_BACKEND',
    'ASCOM_VERSION',
    'ASCOM_ERROR',
    'SensorType',
    'ASCOMCameraState',
]
