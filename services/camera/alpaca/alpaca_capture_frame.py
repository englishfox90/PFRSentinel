"""One Alpaca frame: expose, wait, download, decode, and build the shared metadata.

The exposure is sent to StartExposure in seconds; the GUI's milliseconds became
seconds once, in ``session_factory``. The wait sleeps in short slices until just
before the exposure ends — checking Stop and the watchdog's recovery request
and aborting on either — then polls ImageReady, with a CameraState liveness
check every couple of seconds. Every wait is bounded: the exposure by its
duration plus ``EXPOSURE_GRACE_S``, the download by ``alpaca_frame_budget``'s
deadline (enforced by a watchdog that shuts the socket down), and a download
can be cut short by Stop at any moment.

The camera may be shared with another client (NINA through ASCOM Remote), so
before every exposure gain, offset and the full-frame ROI are read back and
re-asserted (``alpaca_shared_camera``), and a delivered image whose shape or
duration is not this frame's is dropped (``FrameSkipped``) rather than
published or treated as a disconnect.

The frame is normalised to ZWO's raw layout (``to_raw_array``, in place),
flipped in software with the Bayer pattern following the flip, and handed to
the same ``build_frame`` / ``build_frame_metadata`` a ZWO frame goes through, so
the metadata keys are identical by construction.
"""
from __future__ import annotations

import time

import numpy as np

from services.logger import app_logger

from ..errors import CameraError, CameraTimeoutError, FrameSkipped
from ..frame_builder import build_frame
from ..frame_metadata import build_frame_metadata, temperature_fields
from .alpaca_frame_budget import EXPOSURE_GRACE_S, session_download_timeout
from .alpaca_protocol import (
    AlpacaCancelledError,
    AlpacaNotConnectedError,
    AlpacaTransportError,
    CameraState,
)
from .alpaca_readout import adopt_pixel_range
from .alpaca_sensor import apply_flip, flip_bayer_pattern, to_raw_array
from .alpaca_shared_camera import check_delivered, prepare_frame

SLEEP_SLICE_S = 0.2
POLL_FAST_S = 0.1
POLL_SLOW_S = 0.25
FAST_POLL_WINDOW_S = 1.0
LIVENESS_EVERY_S = 2.0
IDLE_LOST_AFTER_S = 2.0
MAX_TRANSIENT_FAILURES = 3
TRANSIENT_RETRY_S = 1.0


class _SessionAbort:
    """``abort_event`` stand-in: set when the session is stopping or recovering."""

    def __init__(self, session):
        self._session = session

    def is_set(self) -> bool:
        s = self._session
        return (not s.is_capturing) or bool(getattr(s, '_recovery_requested', False))


def _check_interrupt(session, device) -> None:
    if not session.is_capturing:
        device.abort()
        raise Exception("Capture stopped during exposure")
    if session.camera is None:
        raise Exception("Camera disconnected during exposure")
    if getattr(session, '_recovery_requested', False):
        session._recovery_requested = False
        device.abort()
        raise Exception("Recovery requested by watchdog mid-exposure")


class _Tolerant:
    """Runs a poll; up to ``MAX_TRANSIENT_FAILURES`` transport blips in a row
    read as "no answer" (None), the next one raises. NotConnected never waits."""

    def __init__(self, session):
        self.session = session
        self.failures = 0

    def __call__(self, poll):
        try:
            value = poll()
        except AlpacaNotConnectedError:
            raise
        except AlpacaTransportError as exc:
            self.failures += 1
            if self.failures > MAX_TRANSIENT_FAILURES:
                raise
            self.session.log(f"⚠ Alpaca poll failed ({self.failures}/{MAX_TRANSIENT_FAILURES}): "
                             f"{exc} — retrying")
            time.sleep(TRANSIENT_RETRY_S)
            return None
        self.failures = 0
        return value


def _check_state(state, now, started, duration, device=None, tolerant=None) -> bool:
    """Raise when CameraState says the exposure is lost; True if the image is in.

    A driver may flip CameraState to Idle a moment before it sets ImageReady,
    so Idle past the grace re-reads ImageReady once before giving up.
    """
    if state == CameraState.ERROR:
        if device is not None:
            device.mark_finished()
        raise CameraError("Exposure failed (camera reported CameraState Error)")
    if state == CameraState.IDLE and now - started > duration + IDLE_LOST_AFTER_S:
        if device is not None and tolerant is not None and tolerant(device.image_ready):
            return True
        if device is not None:
            device.mark_finished()
        raise CameraError("Exposure error: camera returned to IDLE without an image")
    return False


def wait_for_image(session, device, duration: float, started: float, interrupt=None,
                   fast_poll: bool = True) -> None:
    """Block until ImageReady, raising on Stop, recovery, error or timeout.

    ``interrupt()`` replaces the capture checks (Stop / recovery) — the
    calibration exposure passes its own abort, and ``fast_poll=False`` (never
    faster than ``POLL_SLOW_S``).
    """
    check = interrupt or (lambda: _check_interrupt(session, device))
    sleep_until = started + duration - SLEEP_SLICE_S
    fast_until = started + duration + FAST_POLL_WINDOW_S
    deadline = started + duration + EXPOSURE_GRACE_S
    tolerant = _Tolerant(session)
    last_liveness = started
    while True:
        check()
        now = time.monotonic()
        session.exposure_remaining = max(0.0, duration - (now - started))
        if now >= deadline:
            device.abort()
            raise CameraTimeoutError(
                f"Exposure timeout: camera did not complete {duration}s exposure "
                f"within {duration + EXPOSURE_GRACE_S}s")
        if now >= sleep_until:
            if tolerant(device.image_ready):
                return
        if now - last_liveness >= LIVENESS_EVERY_S:
            last_liveness = now
            state = tolerant(device.camera_state)
            if state is not None and _check_state(state, now, started, duration, device,
                                                  tolerant):
                return
        if now < sleep_until:
            time.sleep(min(SLEEP_SLICE_S, max(0.0, sleep_until - now)))
        else:
            fast = fast_poll and duration <= 1.0 and now < fast_until
            time.sleep(POLL_FAST_S if fast else POLL_SLOW_S)


def _exposure_for(session, info) -> float:
    if session.auto_exposure and session.exposure_seconds > session.max_exposure:
        session.log(
            f"⚠ Exposure {session.exposure_seconds*1000:.0f}ms exceeds "
            f"max {session.max_exposure*1000:.0f}ms — clamping"
        )
        session.exposure_seconds = session.max_exposure
    duration = info.clamp_exposure(session.exposure_seconds)
    if abs(duration - session.exposure_seconds) > 1e-9 \
            and getattr(session, '_range_clamp_logged', None) != session.exposure_seconds:
        session._range_clamp_logged = session.exposure_seconds
        session.log(f"⚠ Exposure {session.exposure_seconds}s is outside the camera's range "
                    f"{info.exposure_range_s} — using {duration}s")
    return duration


def download_frame(session, device, info, abort):
    """Download the image; a watchdog-requested cancel consumes the request."""
    try:
        return device.download(abort_event=abort,
                               timeout=session_download_timeout(session, info, device))
    except AlpacaCancelledError:
        if session.is_capturing and getattr(session, '_recovery_requested', False):
            session._recovery_requested = False
            raise Exception("Recovery requested by watchdog mid-download") from None
        raise


def _record_transfer(session, connection) -> None:
    client = connection.client
    if client is not None:
        session.last_download_s = client.last_download_s
        session.last_download_bytes = client.last_image_bytes
        session.last_image_format = client.last_image_format


def capture_single_frame(session):
    """Capture one frame and return ``(PIL image, metadata dict)``."""
    device = session.camera
    if not device:
        raise Exception("Camera not connected")
    if getattr(session, '_recovery_requested', False):
        session._recovery_requested = False
        raise Exception("Recovery requested by watchdog — triggering internal reconnect")

    connection = session._connection
    info = connection.static_info
    warnings = connection.warnings
    try:
        duration = _exposure_for(session, info)
        with connection.sdk_lock:
            if not session.camera:
                raise Exception("Camera disconnected before exposure")
            prepare_frame(device, session, warnings)
        # Not under sdk_lock: a legacy driver may hold StartExposure for the
        # whole exposure, and Stop must still reach AbortExposure meanwhile.
        session.exposure_start_time = time.time()
        duration = device.start(duration)
        start_wall = time.time()
        started = time.monotonic()
        session.exposure_start_time = start_wall

        wait_for_image(session, device, duration, started)
        session.exposure_remaining = 0.0
        session.exposure_start_time = None

        image = download_frame(session, device, info, _SessionAbort(session))
        _record_transfer(session, connection)
        ceiling = check_delivered(device, info, image, duration, warnings)

        raw, bit_depth = to_raw_array(image, ceiling, inplace=True)
        del image
        flip = int(session.flip or 0)
        raw = apply_flip(raw, flip)
        height, width = raw.shape[:2]
        pattern = flip_bayer_pattern(info.bayer_pattern, flip, width, height)
        if bit_depth == 16 and raw.dtype.byteorder == '>':
            raw = raw.astype('<u2')
        # A byte view, not tobytes(): the frame keeps one copy of its pixels.
        raw_bytes = memoryview(np.ascontiguousarray(raw)).cast('B')

        try:
            temp = temperature_fields(device.ccd_temperature())
        except Exception:
            temp = temperature_fields(None)

        wb_config = session.wb_config
        img, arrays, stats = build_frame(raw_bytes, width, height, bit_depth, pattern, wb_config)
        return img, build_frame_metadata(
            camera_name=info.name,
            exposure_seconds=duration,
            gain=session.gain,
            start_time=start_wall,
            temp=temp,
            width=width,
            height=height,
            stats=stats,
            arrays=arrays,
            raw_bytes=raw_bytes,
            bit_depth=bit_depth,
            bayer_pattern=pattern,
            wb_config=wb_config,
            camera_bit_depth=info.sensor_bit_depth,
            pixel_size=info.pixel_size_x or 0,
            elec_per_adu=info.electrons_per_adu or 1.0,
        )
    except Exception as e:
        session.exposure_start_time = None
        session.exposure_remaining = 0.0
        # The capture loop logs the failure once, at the right level.
        app_logger.debug(f"Alpaca frame failed: {e}")
        raise


CALIBRATION_BUSY_WAIT_S = 5.0


class _CalibrationAbort:
    """Abort predicate for a calibration exposure: Stop, recovery, or the
    calibration manager's own abort (stop_capture sets it)."""

    def __init__(self, session, abort_event):
        self._session = session
        self._abort = abort_event

    def is_set(self) -> bool:
        return (self._abort.is_set() or self._session.camera is None
                or bool(getattr(self._session, '_recovery_requested', False)))


def calibration_exposure(session, seconds: float, abort_event):
    """``CameraCalibration.exposure_hook`` for an Alpaca session.

    One exposure through the same start / wait / bounded download a frame
    uses: polls at the Alpaca rate, waits out the readout, aborts our own
    exposure on timeout or abort, and returns ``get_data_after_exposure``'s
    layout (row-major pixels at the transfer depth). A busy shared camera
    waits a few seconds (interruptibly) before the attempt is reported failed.
    """
    device = session.camera
    if not device:
        raise Exception("Camera not connected")
    connection = session._connection
    info = connection.static_info
    abort = _CalibrationAbort(session, abort_event)

    def interrupt():
        if abort.is_set():
            device.abort()
            raise Exception("Calibration aborted during exposure wait")

    try:
        duration = device.start(seconds)
    except FrameSkipped:
        abort_event.wait(CALIBRATION_BUSY_WAIT_S)
        raise
    wait_for_image(session, device, duration, time.monotonic(), interrupt=interrupt,
                   fast_poll=False)
    session.exposure_remaining = 0.0
    image = device.download(abort_event=abort,
                            timeout=session_download_timeout(session, info, device))
    _record_transfer(session, connection)
    if image.ndim == 3:
        image = image.mean(axis=2)
    if image.shape != (info.height, info.width):
        device.set_full_frame_roi()
        raise FrameSkipped(f"calibration image is {image.shape[1]}x{image.shape[0]}, "
                           f"not full frame")
    ceiling = adopt_pixel_range(image.max() if image.size else 0, info.max_adu,
                                connection.warnings.state, connection.warnings)
    raw, _depth = to_raw_array(image, ceiling, inplace=True)
    return np.ascontiguousarray(raw).tobytes()
