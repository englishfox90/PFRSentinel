"""The long-running capture loop shared by every camera backend.

Reconnect with backoff, long-interval retry, the scheduled-window off-peak
disconnect, the cold-open warm-up absorb and the recovery-off single reconnect
all live here. Moved verbatim out of zwo_capture_worker.py (which re-exports
it); the loop only reaches the session through the CaptureSession contract in
session.py, so a backend gets all of it by providing that surface.
"""
from __future__ import annotations

import time
import traceback
from typing import TYPE_CHECKING

import numpy as np

from .capabilities import ZWO_TROUBLESHOOTING_HINTS
from .errors import ExposureStalledError, FrameSkipped
from .recovery_wording import wording_for
from ..logger import app_logger
from ..posthog_service import capture_error

if TYPE_CHECKING:
    from .session import CaptureSession


def troubleshooting_hints(camera) -> tuple[str, ...]:
    """The session's own steps for an unreachable camera, else ZWO's USB ones."""
    hints = getattr(camera, 'troubleshooting_hints', None)
    if isinstance(hints, (tuple, list)) and hints:
        return tuple(hints)
    return ZWO_TROUBLESHOOTING_HINTS


def _log_failure(camera, wording, message):
    """A failure line: the session's log (ZWO), or WARNING when ``levelled``."""
    if wording.levelled:
        app_logger.warning(message)
    else:
        camera.log(message)


def _log_trace(camera, wording, error):
    """The traceback of ``error``: at DEBUG for an expected (network) failure."""
    text = f"Stack trace: {traceback.format_exc()}"
    # The loop's own plain Exception("Camera disconnected") etc. is as expected
    # as a transport error; a TypeError or KeyError is a bug and keeps its trace.
    expected = (error is None or type(error) is Exception
                or isinstance(error, wording.quiet_traceback_types))
    if wording.levelled and expected:
        app_logger.debug(text)
    else:
        camera.log(text)


SKIP_LOG_EVERY_S = 300.0


def _wait_skipped_frame(camera, skip, last_skip_log):
    """Log a skipped frame (rate-limited) and wait out one capture interval."""
    now = time.time()
    message = f"⏭ Frame skipped: {skip}"
    if last_skip_log[0] != message or now - last_skip_log[1] >= SKIP_LOG_EVERY_S:
        camera.log(message)
        last_skip_log[:] = [message, now]
    # The camera answered: a skipped frame is not a wedge.
    camera._last_frame_time = now
    wait_end = time.time() + camera.effective_capture_interval
    while camera.is_capturing and time.time() < wait_end:
        if camera.consume_immediate_capture():
            break
        time.sleep(0.2)


def _stalled(camera, error=None) -> bool:
    return (isinstance(error, ExposureStalledError)
            or int(getattr(camera, 'stalled_exposures', 0) or 0) > 0)


def _link_recovering(camera, wording, error):
    """``link_state`` while recovering: 'stalled' once exposures stop finishing."""
    if wording.errors_survive_reconnect and _stalled(camera, error):
        return 'stalled'
    return 'reconnecting'


def _link_after_reconnect(camera, wording, error, current):
    """``link_state`` once a reconnect answered but before a frame has arrived."""
    if not wording.errors_survive_reconnect:
        return current
    return 'stalled' if _stalled(camera, error) else 'unconfirmed'


def capture_loop(camera: "CaptureSession"):
    """Background capture loop with automatic recovery and scheduled capture support."""
    wording = wording_for(camera)
    camera.log("=== Capture Loop Started ===")
    mode = getattr(camera, 'scheduled_capture_mode', 'always')
    if mode == "gated":
        camera.log(
            f"Scheduled capture (gated): {camera.scheduled_window_label()}"
            " — paused outside window"
        )
        # Gated mode disconnects the camera during off-peak hours and reconnects
        # at the next window. If the camera comes back unopenable, recovery needs
        # a USB disable/enable, which requires Administrator rights — and that
        # need only surfaces hours later. Warn now, while the operator can act.
        try:
            conn = camera._connection
            if (getattr(conn, '_usb_disable_enable_func', None)
                    and not conn._is_running_as_admin()):
                camera.log(
                    "⚠ Gated capture is enabled but the app is NOT running as "
                    "Administrator. If the camera fails to reopen after an "
                    "off-peak window, automatic USB recovery (disable/enable) "
                    "cannot run. Recommended: run the app as Administrator for "
                    "unattended scheduled capture."
                )
        except Exception:
            pass  # Diagnostic only — never block capture startup
    elif mode == "variable":
        camera.log(
            f"Scheduled capture (variable rates): {camera.scheduled_window_interval}s inside "
            f"{camera.scheduled_window_label()}, {camera.capture_interval}s outside"
        )
    else:
        camera.log("Scheduled capture disabled: will run continuously")

    consecutive_errors = 0
    max_reconnect_attempts = 5
    # Long-interval retry state: after max_reconnect_attempts, rather than
    # permanently exiting (which strands a 24/7 unattended rig), we sleep
    # for an escalating interval and then run the whole recovery cycle
    # again. The flag is only used to gate one-shot Discord alerts so we
    # don't spam on every cycle.
    long_retry_mode = False
    # Backoff schedule: 5m -> 15m -> 1h -> stay at 1h. Reduces Discord/log
    # noise for a permanently dead camera while still trying often enough
    # to self-recover after a transient outage.
    long_retry_schedule = [300, 900, 3600]
    long_retry_cycle = 0
    last_schedule_log = None
    frames_captured = 0
    # The first frame after ANY reconnect (scheduled-window transition OR
    # error-recovery) runs against a freshly (re)opened SDK handle. The ZWO
    # SDK's first control op in that state frequently fails once with "Camera
    # closed" and recovers on a clean reopen. When set, the next capture
    # failure is absorbed quietly (no Discord alert, no consecutive-error
    # count) via one silent reopen, instead of masquerading as a fault and
    # kicking off another full disconnect/reconnect cycle. Leaving the
    # error-recovery path WITHOUT this absorption is what produced three
    # open/close cycles in 35s on 2026-06-03 16:32 and wedged the ZWO DLL
    # (the SDK can't take that churn — it recommends 10-15s between ops).
    # See also the 2026-06-02 16:00 scheduled-reconnect incident.
    warmup_pending = False
    # 'camera_auto_recovery' off: a fault gets ONE reconnect; cleared by a good frame.
    reconnect_spent = False
    fatal_reason = "Capture loop terminated unexpectedly"
    # Heartbeat + state flags observed by the UI watchdog. _last_frame_time
    # is updated after every successful capture. long_retry_mode_public
    # mirrors the local long_retry_mode so the watchdog can skip bogus
    # "wedged" alerts while we're intentionally sleeping between retries.
    camera._last_frame_time = time.time()
    camera.long_retry_mode_public = False
    # Read by the UI (camera chip): 'ok' | 'reconnecting' | 'waiting'.
    camera.link_state = 'ok'
    last_skip_log = [None, 0.0]

    # Recalibration rate limiting to prevent infinite loops
    # (e.g., someone turning lights on/off repeatedly)
    last_recalibration_time = 0
    recalibration_cooldown_sec = 60
    recalibration_count = 0
    recalibration_window_start = time.time()
    max_recalibrations_per_window = 3
    recalibration_window_sec = 600

    try:
        if camera.auto_exposure and not camera.calibration_complete:
            try:
                camera.run_calibration()
            except Exception as e:
                camera.log(f"Calibration failed: {e}. Continuing with current settings.")
                camera.calibration_complete = True
    except Exception as e:
        camera.log(f"Error during calibration: {e}")

    try:
        while camera.is_capturing:
            try:
                within_window = camera.is_within_scheduled_window()

                if not within_window:
                    camera.link_state = 'waiting'
                    current_status = "outside_window"
                    if last_schedule_log != current_status:
                        camera.log(
                            f"⏸ Outside scheduled capture window "
                            f"({camera.scheduled_window_label()})"
                        )
                        camera.log(
                            "Entering off-peak mode: disconnecting camera to reduce hardware load..."
                        )
                        last_schedule_log = current_status

                        if camera.camera:
                            try:
                                was_capturing = camera.is_capturing
                                camera.is_capturing = False

                                try:
                                    if camera.exposure_start_time is not None:
                                        camera.camera.stop_exposure()
                                        camera.exposure_start_time = None
                                        camera.exposure_remaining = 0.0
                                except Exception:
                                    pass

                                # release_sdk=True: off-peak gaps span hours, and
                                # reusing a stale SDK handle across that idle is
                                # what left the camera unopenable on the next
                                # scheduled window. Drop the SDK so reconnect
                                # re-inits cleanly.
                                camera._connection.disconnect(release_sdk=True)
                                camera.log(
                                    "✓ Camera disconnected for off-peak hours (reducing hardware load)"
                                )

                                camera.is_capturing = was_capturing

                                if camera.status_callback:
                                    camera.status_callback(
                                        "Idle (off-peak, window "
                                        f"{camera.scheduled_window_label()})"
                                    )
                            except Exception as e:
                                camera.log(f"Error disconnecting camera: {e}")
                                camera.is_capturing = was_capturing

                    # Keep _last_frame_time fresh so the UI watchdog doesn't
                    # mistake intentional off-peak idle for a wedged capture.
                    camera._last_frame_time = time.time()
                    wait_end = time.time() + 30.0
                    while camera.is_capturing and time.time() < wait_end:
                        time.sleep(0.2)
                    continue
                else:
                    if last_schedule_log == "outside_window":
                        camera.log(
                            f"▶ Entered scheduled capture window "
                            f"({camera.scheduled_window_label()})"
                        )
                        camera.log("Transitioning to active capture mode: reconnecting camera...")
                        last_schedule_log = "inside_window"
                        if camera.camera:
                            camera.link_state = 'ok'

                        if camera.status_callback:
                            camera.status_callback("Reconnecting for scheduled window...")

                        if not camera.camera:
                            camera.link_state = 'reconnecting'
                            camera.log("Attempting to reconnect camera (re-detecting cameras)...")
                            if not camera.reconnect_camera_safe():
                                camera.log("✗ ERROR: Failed to reconnect camera for scheduled window")
                                if not getattr(camera, 'auto_recovery_enabled', True):
                                    fatal_reason = ("Camera failed to reopen for the scheduled window; "
                                                    "automatic recovery is off — capture stopped")
                                    break
                                camera.log("Will retry in 5 seconds...")
                                wait_end = time.time() + 5.0
                                while camera.is_capturing and time.time() < wait_end:
                                    time.sleep(0.2)
                                continue
                            camera.log("✓ Camera reconnected successfully for scheduled captures")
                            # Mirror the recovery path's post-reconnect settle:
                            # a cold-re-inited SDK needs the USB bus to stabilise
                            # before the first control op, otherwise the first
                            # frame reliably fails with "Camera closed".
                            wait_end = time.time() + 3.0
                            while camera.is_capturing and time.time() < wait_end:
                                time.sleep(0.2)
                            warmup_pending = True
                            # Suppress watchdog during the first post-reconnect exposure.
                            camera._last_frame_time = time.time()

                if not camera.camera:
                    raise Exception("Camera disconnected")

                img, metadata = camera.capture_single_frame()

                consecutive_errors = 0
                warmup_pending = reconnect_spent = False
                camera._last_frame_time = time.time()
                camera.link_state = 'ok'
                if long_retry_mode:
                    long_retry_mode = False
                    camera.long_retry_mode_public = False
                    long_retry_cycle = 0
                    camera.log("✓ Capture recovered from long-retry mode")
                    if hasattr(camera, 'on_error_callback') and camera.on_error_callback:
                        try:
                            camera.on_error_callback("Camera recovered — capture resumed")
                        except Exception:
                            pass

                if camera.auto_exposure:
                    img_array = np.array(img)
                    exposure_result = camera.adjust_exposure_auto(img_array)
                    if exposure_result and exposure_result.get('needs_recalibration', False):
                        current_time = time.time()

                        if current_time - recalibration_window_start > recalibration_window_sec:
                            recalibration_count = 0
                            recalibration_window_start = current_time

                        time_since_last = current_time - last_recalibration_time
                        can_recalibrate = (
                            time_since_last >= recalibration_cooldown_sec
                            and recalibration_count < max_recalibrations_per_window
                        )

                        if can_recalibrate:
                            camera.log("⚠ Drastic scene change detected - running rapid calibration")
                            camera.log(
                                f"  (Recalibration {recalibration_count + 1}/"
                                f"{max_recalibrations_per_window} in current window)"
                            )

                            if camera.on_calibration_callback:
                                camera.on_calibration_callback(True)

                            try:
                                camera.run_calibration()
                                last_recalibration_time = time.time()
                                recalibration_count += 1
                            except Exception as cal_error:
                                camera.log(
                                    f"Recalibration error: {cal_error} - continuing with adjusted exposure"
                                )

                            if camera.on_calibration_callback:
                                camera.on_calibration_callback(False)

                            # Skip publishing this badly-exposed frame;
                            # next iteration will capture with calibrated exposure.
                            continue
                        else:
                            if time_since_last < recalibration_cooldown_sec:
                                wait_time = int(recalibration_cooldown_sec - time_since_last)
                                camera.log(
                                    f"⚠ Scene change detected but recalibration on cooldown "
                                    f"({wait_time}s remaining)"
                                )
                            else:
                                camera.log(
                                    f"⚠ Scene change detected but max recalibrations reached "
                                    f"({max_recalibrations_per_window} per "
                                    f"{recalibration_window_sec//60}min window)"
                                )
                            camera.log("  Using aggressive auto-exposure adjustment instead")

                if camera.on_frame_callback:
                    camera.on_frame_callback(img, metadata)

                frames_captured += 1
                if frames_captured == 1 or frames_captured % 100 == 0:
                    camera.log(f"Captured {frames_captured} frames (latest: {metadata['FILENAME']})")

                try:
                    dropped = camera.camera.get_dropped_frames()
                    if dropped > 0:
                        camera.log(f"⚠ USB performance warning: {dropped} dropped frames detected")
                        camera.log(
                            "  Consider: reducing bandwidth_overload, lowering frame rate, "
                            "or checking USB connection"
                        )
                except Exception:
                    pass

                # Each frame now owns its arrays (~100 MB at 3552x3552 RAW16),
                # so holding these locals across the inter-frame wait would keep
                # a finished frame resident alongside the next one.
                img = None
                metadata = None

                if camera.is_capturing:
                    wait_end = time.time() + camera.effective_capture_interval
                    while camera.is_capturing and time.time() < wait_end:
                        if camera.consume_immediate_capture():
                            break
                        time.sleep(0.2)

            except FrameSkipped as skip:
                if not camera.is_capturing:
                    break
                _wait_skipped_frame(camera, skip, last_skip_log)
                continue

            except Exception as e:
                if not camera.is_capturing:
                    break

                auto_recovery = getattr(camera, 'auto_recovery_enabled', True)
                # The cold-open quirk below belongs to that one reconnect, not a 2nd fault.
                if not auto_recovery and reconnect_spent and not warmup_pending:
                    fatal_reason = (f"Capture failed again after the single reconnect ({e}); "
                                    "automatic recovery is off — capture stopped")
                    break

                # First frame after a scheduled-window reconnect: absorb a
                # single "Camera closed" quietly. The off-peak path releases the
                # SDK, so the window-transition reconnect runs against a cold
                # re-init whose first control op is flaky and heals on a clean
                # reopen. Reuse the recovery reopen flow, but without a Discord
                # alert or a consecutive-error count so the expected cold-open
                # quirk doesn't read as a fault (see 2026-06-02 16:00 incident).
                if warmup_pending:
                    warmup_pending = False
                    reconnect_spent = True
                    if wording.errors_survive_reconnect:
                        consecutive_errors += 1
                    camera.link_state = _link_recovering(camera, wording, e)
                    camera.log(wording.cold_open_absorb.format(error=e))
                    try:
                        if camera.calibration_manager:
                            camera.calibration_manager.abort()
                        if camera.camera:
                            camera._connection.disconnect()
                        wait_end = time.time() + 8.0
                        while camera.is_capturing and time.time() < wait_end:
                            time.sleep(0.2)
                        if camera.reconnect_camera_safe():
                            camera.log("✓ Camera reopened after scheduled-reconnect warm-up")
                            camera._last_frame_time = time.time()
                            camera.link_state = _link_after_reconnect(
                                camera, wording, e, camera.link_state)
                        else:
                            camera.log(
                                "⚠ Warm-up reopen failed — normal recovery will handle it"
                            )
                    except Exception as warmup_err:
                        camera.log(
                            f"Warm-up reopen error: {warmup_err} — "
                            "normal recovery will handle it"
                        )
                    continue

                consecutive_errors += 1
                error_msg = str(e)
                _log_failure(camera, wording, f"✗ ERROR in capture loop: {error_msg}")
                camera.log(f"Consecutive errors: {consecutive_errors}/{max_reconnect_attempts}")
                _log_trace(camera, wording, e)

                capture_error(e, context='camera_capture_loop')

                if (
                    consecutive_errors == 1
                    and not (wording.levelled and long_retry_mode)
                    and hasattr(camera, 'on_error_callback')
                    and camera.on_error_callback
                ):
                    camera.on_error_callback(
                        f"Capture error: {error_msg} - attempting recovery..."
                    )

                if consecutive_errors <= max_reconnect_attempts:
                    camera.log(
                        f"Initiating reconnection attempt "
                        f"{consecutive_errors}/{max_reconnect_attempts}..."
                    )
                    reconnect_spent = True
                    camera.link_state = _link_recovering(camera, wording, e)
                    try:
                        # Abort any running calibration before disconnecting so it
                        # doesn't keep calling SDK methods on a dying camera handle.
                        if camera.calibration_manager:
                            camera.calibration_manager.abort()

                        if camera.camera:
                            camera.log("Cleaning up existing camera connection...")
                            camera._connection.disconnect()

                        # ZWO SDK docs recommend waiting 10-15s before reopening
                        # a camera after an error. 0.5s almost guaranteed
                        # "Invalid ID" on first reopen, wasting one of five
                        # recovery attempts. 8s interruptible keeps Stop
                        # responsive.
                        pre_reconnect_wait = 8.0
                        camera.log(wording.pre_reconnect_wait.format(seconds=pre_reconnect_wait))
                        wait_end = time.time() + pre_reconnect_wait
                        while camera.is_capturing and time.time() < wait_end:
                            time.sleep(0.2)

                        if camera.reconnect_camera_safe():
                            camera.log("✓ Camera reconnected successfully")
                            if not wording.errors_survive_reconnect:
                                consecutive_errors = 0
                            camera.link_state = _link_after_reconnect(
                                camera, wording, e, camera.link_state)
                            # Absorb the cold-open quirk on the FIRST frame after
                            # this reopen (same as the scheduled-window path).
                            # Without this, that benign "Camera closed" counted
                            # as a fresh fault and triggered another full
                            # reconnect — the churn that wedged the DLL.
                            warmup_pending = True
                            camera.log(wording.post_reconnect_settle)
                            wait_end = time.time() + 3.0
                            while camera.is_capturing and time.time() < wait_end:
                                time.sleep(0.2)
                            # No explicit probe here: an SDK call that hangs
                            # (e.g. get_camera_property after a wedged USB bus)
                            # would block forever since it cannot be interrupted.
                            # capture_single_frame on the next loop iteration
                            # has bounded timeouts and will raise cleanly.
                            camera._last_frame_time = time.time()
                            continue
                        else:
                            raise Exception("Failed to reconnect camera")
                    except Exception as reconnect_error:
                        _log_failure(camera, wording,
                                     f"✗ Reconnection attempt failed: {reconnect_error}")
                        _log_trace(camera, wording, None)
                        if not auto_recovery:
                            fatal_reason = ("Camera reconnect failed; automatic "
                                            "recovery is off — capture stopped")
                            break
                        backoff_time = min(2 ** consecutive_errors, 32)
                        camera.log(
                            f"Using exponential backoff: waiting {backoff_time}s "
                            f"before next recovery cycle "
                            f"(attempt {consecutive_errors}/{max_reconnect_attempts} failed)..."
                        )
                        wait_end = time.time() + backoff_time
                        while camera.is_capturing and time.time() < wait_end:
                            time.sleep(0.2)
                else:
                    # Max attempts reached — enter long-interval retry mode
                    # instead of permanently exiting. The rig is unattended;
                    # we'd rather keep trying every few minutes than leave
                    # the user staring at a stale image all night.
                    interval = long_retry_schedule[
                        min(long_retry_cycle, len(long_retry_schedule) - 1)
                    ]
                    camera.link_state = 'waiting'
                    if not long_retry_mode:
                        long_retry_mode = True
                        camera.long_retry_mode_public = True
                        camera.log(
                            f"✗ Maximum reconnection attempts ({max_reconnect_attempts}) reached"
                        )
                        camera.log(
                            f"⏳ Entering long-interval retry mode — first retry in {interval}s "
                            "(backoff: 5m → 15m → 1h)"
                        )
                        camera.log("Troubleshooting steps:")
                        for step, hint in enumerate(troubleshooting_hints(camera), start=1):
                            camera.log(f"  {step}. {hint}")
                        if hasattr(camera, 'on_error_callback') and camera.on_error_callback:
                            try:
                                camera.on_error_callback(
                                    f"Camera unreachable — retrying every {interval // 60} min"
                                )
                            except Exception:
                                pass
                    else:
                        camera.log(
                            f"⏳ Long-retry cycle {long_retry_cycle + 1} — "
                            f"next retry in {interval}s"
                        )

                    wait_end = time.time() + interval
                    while camera.is_capturing and time.time() < wait_end:
                        time.sleep(0.2)

                    long_retry_cycle += 1
                    consecutive_errors = 0
                    camera.link_state = 'reconnecting'
    finally:
        camera.log("Capture loop exiting - cleaning up...")
        # Snapshot mode, not video: nothing to stop here, and stop_capture() owns
        # the disconnect. It never runs on a fatal exit though, and with recovery
        # off nothing reopens the handle — release the USB device ourselves.
        if camera.is_capturing and not getattr(camera, 'auto_recovery_enabled', True):
            camera.log(f"✗ {fatal_reason}")
            try:
                camera._connection.disconnect()
            except Exception:
                pass

        # If capture is exiting while is_capturing is still True, something
        # fatal (unhandled exception) forced us out — tell the UI so it can
        # tear down state, not sit pretending we're still running.
        if (
            camera.is_capturing
            and hasattr(camera, 'on_error_callback')
            and camera.on_error_callback
        ):
            camera.is_capturing = False
            try:
                camera.on_error_callback(fatal_reason, is_fatal=True)
            except TypeError:
                try:
                    camera.on_error_callback(fatal_reason)
                except Exception:
                    pass
            except Exception:
                pass

    camera.log("Capture loop stopped")
