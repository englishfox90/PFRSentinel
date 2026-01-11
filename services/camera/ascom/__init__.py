"""
ASCOM Camera backend for PFR Sentinel (stub).

Provides ASCOM-compatible camera support via alpyca or comtypes.
This is a stub implementation - full ASCOM support is in development.
"""

from .adapter import ASCOMCameraAdapter, check_ascom_availability

__all__ = ['ASCOMCameraAdapter', 'check_ascom_availability']
