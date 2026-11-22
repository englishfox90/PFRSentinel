"""The capture-session contract: what the controller, the shared capture loop
and the UI readers of ``controller.zwo_camera`` may reach on a camera session.

``ZWOCamera`` is the reference implementation; every backend builds on
``CaptureSessionBase`` and supplies the hardware half (connect, reconnect,
disconnect, one frame, live control writes). Typing only.

The loop also sets ``_last_frame_time`` and ``long_retry_mode_public`` on the
session as it runs (``LOOP_OWNED_ATTRIBUTES``). They are absent until the loop
starts and the watchdog reads them with ``getattr`` defaults, so a session must
not initialise them itself.
"""
from __future__ import annotations

import threading
from typing import Any, Callable, Protocol, runtime_checkable

from .capabilities import CameraCapabilities

LOOP_OWNED_ATTRIBUTES = ('_last_frame_time', 'long_retry_mode_public')


@runtime_checkable
class CaptureSession(Protocol):
    backend: str
    camera_name: str | None
    camera_serial: str | None

    is_capturing: bool
    capture_thread: threading.Thread | None
    camera: Any
    asi: Any
    _connection: Any
    exposure_seconds: float
    exposure_start_time: float | None
    exposure_remaining: float
    gain: int
    offset: int
    flip: int
    bayer_pattern: str
    wb_config: dict
    wb_mode: str
    use_raw16: bool
    auto_exposure: bool
    max_exposure: float
    target_brightness: int
    capture_interval: float
    calibration_manager: Any
    calibration_mode: bool
    calibration_complete: bool
    auto_recovery_enabled: bool
    scheduled_capture_mode: str
    scheduled_capture_enabled: bool
    scheduled_start_time: str
    scheduled_end_time: str
    scheduled_window_interval: float
    schedule_gate: Callable[[], bool] | None
    _recovery_requested: bool

    on_frame_callback: Callable | None
    on_log_callback: Callable[[str], None] | None
    on_error_callback: Callable | None
    on_calibration_callback: Callable[[bool], None] | None
    status_callback: Callable[[str], None] | None

    @property
    def capabilities(self) -> CameraCapabilities: ...

    @property
    def supports_raw16(self) -> bool: ...

    @property
    def sensor_bit_depth(self) -> int: ...

    @property
    def current_bit_depth(self) -> int: ...

    @property
    def camera_info(self) -> dict: ...

    @property
    def effective_capture_interval(self) -> float: ...

    def initialize_sdk(self) -> bool: ...

    def detect_cameras(self) -> list: ...

    def connect_camera(self, camera_index: int = 0) -> bool: ...

    def reconnect_camera_safe(self) -> bool: ...

    def disconnect_camera(self) -> None: ...

    def start_capture(self, on_frame_callback, on_log_callback=None) -> bool: ...

    def stop_capture(self) -> None: ...

    def wait_for_capture_thread_exit(self, timeout: float = 10.0) -> bool: ...

    def capture_single_frame(self) -> tuple: ...

    def capture_loop(self) -> None: ...

    def run_calibration(self) -> None: ...

    def adjust_exposure_auto(self, img_array) -> dict | None: ...

    def is_within_scheduled_window(self) -> bool: ...

    def is_in_time_window(self) -> bool: ...

    def scheduled_window_label(self) -> str: ...

    def request_immediate_capture(self) -> None: ...

    def consume_immediate_capture(self) -> bool: ...

    def set_exposure(self, seconds: float) -> None: ...

    def set_gain(self, gain) -> None: ...

    def set_capture_interval(self, seconds: float) -> None: ...

    def set_offset_live(self, offset) -> None: ...

    def set_flip_live(self, flip) -> None: ...

    def set_white_balance_live(self, mode, wb_r, wb_b) -> None: ...

    def set_raw16_mode(self, enabled: bool) -> bool: ...

    def log(self, message: str) -> None: ...
