"""One Alpaca frame: expose, wait, download, decode, and build the shared metadata.

The exposure is sent to StartExposure in seconds; the GUI's milliseconds became
seconds once, in ``session_params``. The wait sleeps in short slices until just
before the exposure ends — checking Stop and the watchdog's recovery request
and aborting on either — then polls ImageReady, with a CameraState liveness
check every couple of seconds. Every wait is bounded: the exposure by its
duration plus ``EXPOSURE_GRACE_S``, the download by a size-scaled timeout, and
a download can be cut short by Stop within one chunk.

The frame is normalised to ZWO's raw layout (``to_raw_array``), flipped in
software with the Bayer pattern following the flip, and handed to the same
``build_frame`` / ``build_frame_metadata`` a ZWO frame goes through, so the
metadata keys are identical by construction.
"""
from __future__ import annotations

import time

import numpy as np

from ..errors import CameraConfigurationError, CameraError, CameraTimeoutError
from ..frame_builder import build_frame
from ..frame_metadata import build_frame_metadata, temperature_fields
from .alpaca_protocol import AlpacaNotConnectedError, AlpacaTransportError, CameraState
from .alpaca_sensor import RGB, apply_flip, flip_bayer_pattern, to_raw_array

SLEEP_SLICE_S = 0.2
POLL_FAST_S = 0.1
POLL_SLOW_S = 0.25
LIVENESS_EVERY_S = 2.0
EXPOSURE_GRACE_S = 30.0
IDLE_LOST_AFTER_S = 2.0
MAX_TRANSIENT_FAILURES = 3
TRANSIENT_RETRY_S = 1.0
MIN_DOWNLOAD_TIMEOUT_S = 30.0
ASSUMED_LINK_BYTES_PER_S = 1_000_000


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


def _check_state(state, now, started, duration) -> None:
    if state == CameraState.ERROR:
        raise CameraError("Exposure failed (camera reported CameraState Error)")
    if state == CameraState.IDLE and now - started > duration + IDLE_LOST_AFTER_S:
        raise CameraError("Exposure error: camera returned to IDLE without an image")


def wait_for_image(session, device, duration: float, started: float) -> None:
    """Block until ImageReady, raising on Stop, recovery, error or timeout."""
    sleep_until = started + duration - SLEEP_SLICE_S
    deadline = started + duration + EXPOSURE_GRACE_S
    poll_every = POLL_SLOW_S if duration > 1.0 else POLL_FAST_S
    tolerant = _Tolerant(session)
    last_liveness = started
    while True:
        _check_interrupt(session, device)
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
            if state is not None:
                _check_state(state, now, started, duration)
        if now < sleep_until:
            time.sleep(min(SLEEP_SLICE_S, max(0.0, sleep_until - now)))
        else:
            time.sleep(poll_every)


def download_timeout_s(info, use_imagebytes: bool) -> float:
    planes = 3 if info.bayer_pattern == RGB else 1
    per_pixel = 4 if use_imagebytes else 12
    size = info.width * info.height * planes * per_pixel
    return max(MIN_DOWNLOAD_TIMEOUT_S, size / ASSUMED_LINK_BYTES_PER_S)


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


def _checked_shape(raw: np.ndarray, info) -> None:
    want_rgb = info.bayer_pattern == RGB
    ok = (raw.ndim == 3 and raw.shape[2] == 3) if want_rgb else raw.ndim == 2
    if not ok:
        raise CameraConfigurationError(
            f"Alpaca camera delivered an image of shape {raw.shape} for SensorType "
            f"{info.sensor_type}")


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
    try:
        duration = _exposure_for(session, info)
        with connection.sdk_lock:
            if not session.camera:
                raise Exception("Camera disconnected before exposure")
            device.set_gain(session.gain)
            duration = device.start(duration)
        start_wall = time.time()
        started = time.monotonic()
        session.exposure_start_time = start_wall

        wait_for_image(session, device, duration, started)
        session.exposure_remaining = 0.0
        session.exposure_start_time = None

        image = device.download(abort_event=_SessionAbort(session),
                                timeout=download_timeout_s(info, device.use_imagebytes))
        client = connection.client
        if client is not None:
            session.last_download_s = client.last_download_s
            session.last_download_bytes = client.last_image_bytes
            session.last_image_format = client.last_image_format

        raw, bit_depth = to_raw_array(image, info.max_adu)
        _checked_shape(raw, info)
        flip = int(session.flip or 0)
        raw = apply_flip(raw, flip)
        height, width = raw.shape[:2]
        pattern = flip_bayer_pattern(info.bayer_pattern, flip, width, height)
        if bit_depth == 16:
            raw = raw.astype('<u2', copy=False)
        raw_bytes = raw.tobytes()

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
        session.log(f"ERROR capturing frame: {e}")
        raise
