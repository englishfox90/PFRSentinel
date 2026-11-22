"""The hardware-facing contract shared by the capture loop and auto-exposure.

``ExposureHandle`` is the subset of zwoasi's ``Camera`` that the backend-neutral
code calls (``capture_loop``, ``CaptureSessionBase.stop_capture`` and
``CameraCalibration``). ``zwoasi.Camera`` satisfies it unchanged; a network
backend wraps its native API in a thin facade with the same method names.
``SdkConstants`` is the namespace of ``ASI_*`` values those callers compare
against — the ``zwoasi`` module itself, or a stand-in with identical numbers.

Units follow zwoasi: ``ASI_EXPOSURE`` is written in microseconds and
``ASI_TEMPERATURE`` reads in tenths of a degree Celsius. Everything else in
the session layer is in seconds; convert at the facade, nowhere else.

Typing only — nothing here runs at capture time.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable


class SdkConstants(Protocol):
    ASI_EXP_IDLE: int
    ASI_EXP_WORKING: int
    ASI_EXP_SUCCESS: int
    ASI_EXP_FAILED: int
    ASI_GAIN: int
    ASI_EXPOSURE: int
    ASI_BRIGHTNESS: int
    ASI_TEMPERATURE: int
    ASI_FLIP: int
    ASI_IMG_RAW8: int
    ASI_IMG_RAW16: int


SDK_CONSTANT_NAMES = (
    'ASI_EXP_IDLE', 'ASI_EXP_WORKING', 'ASI_EXP_SUCCESS', 'ASI_EXP_FAILED',
    'ASI_GAIN', 'ASI_EXPOSURE', 'ASI_BRIGHTNESS', 'ASI_TEMPERATURE', 'ASI_FLIP',
    'ASI_IMG_RAW8', 'ASI_IMG_RAW16',
)


@runtime_checkable
class ExposureHandle(Protocol):
    def start_exposure(self, is_dark: bool = False) -> None: ...

    def stop_exposure(self) -> None: ...

    def get_exposure_status(self) -> int:
        """One of the ``ASI_EXP_*`` values."""
        ...

    def get_data_after_exposure(self, buffer_=None) -> bytes | bytearray:
        """Row-major H*W pixels, uint8 or little-endian uint16."""
        ...

    def get_camera_property(self) -> dict:
        """At least ``Name``, ``MaxWidth`` and ``MaxHeight``; optionally
        ``BitDepth``, ``PixelSize``, ``ElecPerADU``, ``IsColorCam``,
        ``SupportedVideoFormat``."""
        ...

    def set_control_value(self, control_type: int, value: int, auto: bool = False) -> None: ...

    def get_control_value(self, control_type: int) -> tuple[int, bool]: ...

    def get_roi_format(self) -> tuple[int, int, int, int]:
        """``(width, height, bins, image_type)``."""
        ...

    def get_dropped_frames(self) -> int: ...

    def close(self) -> None: ...


HANDLE_METHOD_NAMES = (
    'start_exposure', 'stop_exposure', 'get_exposure_status',
    'get_data_after_exposure', 'get_camera_property', 'set_control_value',
    'get_control_value', 'get_roi_format', 'get_dropped_frames', 'close',
)
