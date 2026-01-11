"""
File/Directory watch backend for PFR Sentinel.

Provides camera-like interface for watching directories for new images.
"""

from .adapter import FileWatchAdapter

__all__ = ['FileWatchAdapter']
