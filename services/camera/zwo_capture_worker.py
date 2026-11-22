"""ZWO camera capture worker.

Holds the ZWO single-frame capture routine: the ASI_EXP_* poll, the
ASI_EXP_FAILED retry, the readout under sdk_lock and the ROI byte check.
Functions take a ZWOCamera instance and operate on its attributes — they are
not standalone; they rely on the camera's connection and callbacks. The capture
loop moved to capture_loop.py, shared with every backend.
"""
from __future__ import annotations

import time
from typing import TYPE_CHECKING

from .camera_utils import call_with_timeout
from .capture_loop import capture_loop  # re-exported for existing callers
from .frame_builder import build_frame
from .frame_metadata import build_frame_metadata, temperature_fields

if TYPE_CHECKING:
    from .zwo_camera import ZWOCamera

__all__ = ['capture_single_frame', 'capture_loop']


def _get_temperature(camera: "ZWOCamera"):
    try:
        temp_value = camera.camera.get_control_value(camera.asi.ASI_TEMPERATURE)[0]
        return temperature_fields(temp_value / 10.0)
    except Exception:
        return temperature_fields(None)


def capture_single_frame(camera: "ZWOCamera"):
    """Capture a single frame and return (PIL image, metadata dict)."""
    if not camera.camera:
        raise Exception("Camera not connected")

    # Honour a watchdog-requested self-heal before opening a new exposure.
    # See ZWOCamera._recovery_requested.
    if getattr(camera, '_recovery_requested', False):
        camera._recovery_requested = False
        raise Exception(
            "Recovery requested by watchdog — triggering internal reconnect"
        )

    sdk_lock = camera._connection.sdk_lock

    try:
        # Enforce max exposure limit as a safety net.
        # The auto-exposure algorithm should already clamp, but this catches
        # edge cases (stale calibration manager, manual set_exposure, etc.).
        if camera.auto_exposure and camera.exposure_seconds > camera.max_exposure:
            camera.log(
                f"⚠ Exposure {camera.exposure_seconds*1000:.0f}ms exceeds "
                f"max {camera.max_exposure*1000:.0f}ms — clamping"
            )
            camera.exposure_seconds = camera.max_exposure

        # Hold SDK lock while sending commands to the camera.
        # Released before the exposure wait loop so disconnect can proceed.
        with sdk_lock:
            if not camera.camera:
                raise Exception("Camera disconnected before exposure")
            camera.camera.set_control_value(
                camera.asi.ASI_EXPOSURE, int(camera.exposure_seconds * 1000000)
            )
            camera.camera.set_control_value(camera.asi.ASI_GAIN, camera.gain)

        # Retry ASI_EXP_FAILED once before tearing down — a single failed
        # status is frequently just a USB bandwidth hiccup and recovers on
        # immediate restart. The expensive full-reconnect path stays for
        # persistent failures.
        max_exp_attempts = 2
        exposure_succeeded = False
        for exp_attempt in range(1, max_exp_attempts + 1):
            with sdk_lock:
                if not camera.camera:
                    raise Exception("Camera disconnected before exposure")
                camera.camera.start_exposure()

            # Wait for exposure to complete (lock released so disconnect can run)
            timeout = camera.exposure_seconds + 5.0
            start_time = time.time()
            camera.exposure_start_time = start_time
            transient_failure = False

            while time.time() - start_time < timeout:
                if not camera.is_capturing:
                    try:
                        camera.camera.stop_exposure()
                    except Exception:
                        pass
                    raise Exception("Capture stopped during exposure")
                if camera.camera is None:
                    raise Exception("Camera disconnected during exposure")
                if getattr(camera, '_recovery_requested', False):
                    camera._recovery_requested = False
                    try:
                        camera.camera.stop_exposure()
                    except Exception:
                        pass
                    raise Exception(
                        "Recovery requested by watchdog mid-exposure"
                    )

                status = camera.camera.get_exposure_status()
                if status == camera.asi.ASI_EXP_SUCCESS:
                    exposure_succeeded = True
                    break
                elif status == camera.asi.ASI_EXP_FAILED:
                    if exp_attempt < max_exp_attempts:
                        camera.log(
                            f"⚠ ASI_EXP_FAILED on attempt {exp_attempt}/{max_exp_attempts} — "
                            "retrying exposure (likely USB bandwidth hiccup)"
                        )
                        try:
                            with sdk_lock:
                                if camera.camera:
                                    camera.camera.stop_exposure()
                        except Exception:
                            pass
                        time.sleep(0.3)
                        transient_failure = True
                        break
                    raise Exception("Exposure failed (camera returned ASI_EXP_FAILED status)")
                elif status == camera.asi.ASI_EXP_IDLE:
                    raise Exception("Exposure error: camera returned to IDLE state unexpectedly")

                elapsed = time.time() - start_time
                camera.exposure_remaining = max(0, camera.exposure_seconds - elapsed)
                time.sleep(0.05)

            if exposure_succeeded:
                break
            if not transient_failure:
                # Fell out of wait loop via timeout — don't retry, let the
                # timeout check below raise.
                break

        if not exposure_succeeded and time.time() - start_time >= timeout:
            camera.exposure_remaining = 0.0
            camera.exposure_start_time = None
            raise Exception(
                f"Exposure timeout: camera did not complete {camera.exposure_seconds}s "
                f"exposure within {timeout}s"
            )

        camera.exposure_remaining = 0.0
        camera.exposure_start_time = None

        with sdk_lock:
            if not camera.camera:
                raise Exception("Camera disconnected before data readout")

            # The readout is the one per-frame SDK call that can wedge a USB
            # bus indefinitely — and it runs while holding sdk_lock, so a hang
            # here would block disconnect()'s close() forever (USB stuck until
            # reboot). The exposure has already completed, so the transfer
            # itself should be fast; bound it generously and raise on a wedge
            # so the loop's recovery path runs instead. See the connection
            # audit, 2026-06-02.
            readout_timeout = max(10.0, camera.exposure_seconds)

            def _read_frame():
                data = camera.camera.get_data_after_exposure()
                info = camera.camera.get_camera_property()
                # Use the SDK's view of the active ROI rather than the sensor's
                # MaxWidth/MaxHeight. If set_roi was ever silently rejected or
                # another process left the SDK with a non-default ROI, these
                # diverge — and trusting MaxWidth then crashes in reshape.
                roi = camera.camera.get_roi_format()
                return data, info, roi

            img_data, camera_info, roi_format = call_with_timeout(
                _read_frame, readout_timeout,
                hint="frame readout — USB bus may be wedged",
            )
            roi_width, roi_height, _bins, roi_image_type = roi_format

        active_bit_depth = (
            16 if roi_image_type == camera.asi.ASI_IMG_RAW16 else 8
        )
        expected_bytes = roi_width * roi_height * (active_bit_depth // 8)
        if len(img_data) != expected_bytes:
            raise Exception(
                f"Frame size mismatch: SDK delivered {len(img_data)} bytes but "
                f"ROI {roi_width}x{roi_height} @ {active_bit_depth}-bit expects "
                f"{expected_bytes}. Camera may have been reset by another process."
            )
        width, height = roi_width, roi_height

        temp_info = _get_temperature(camera)

        img, arrays, stats = build_frame(
            img_data, width, height, active_bit_depth,
            camera.bayer_pattern, camera.wb_config,
        )

        metadata = build_frame_metadata(
            camera_name=camera_info['Name'],
            exposure_seconds=camera.exposure_seconds,
            gain=camera.gain,
            start_time=start_time,
            temp=temp_info,
            width=width,
            height=height,
            stats=stats,
            arrays=arrays,
            raw_bytes=img_data,
            bit_depth=active_bit_depth,
            bayer_pattern=camera.bayer_pattern,
            wb_config=camera.wb_config,
            camera_bit_depth=camera_info.get('BitDepth', 8),
            pixel_size=camera_info.get('PixelSize', 0),
            elec_per_adu=camera_info.get('ElecPerADU', 1.0),
        )

        return img, metadata

    except Exception as e:
        camera.log(f"ERROR capturing frame: {e}")
        raise


