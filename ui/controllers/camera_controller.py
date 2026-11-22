"""
Camera Controller for Qt UI
Adapter between PySide6 UI and existing ZWO camera service.

Uses ZWOCamera.start_capture() with callbacks - NO reimplementation of capture logic.
All auto-exposure, calibration, scheduled windows, etc. are handled by ZWOCamera.
"""
from PySide6.QtCore import QObject, QTimer, Signal
from datetime import datetime
import collections
import threading
import time

from services.logger import app_logger
from services.zwo_sdk_library import library_name, resolve_library_path
from services.camera.session_factory import build_session
from services.camera.session_params import read_capture_params
from services.camera.camera_source import ALPACA
from services.capture_schedule_window import gate_for_config
from .camera_settings import apply_camera_settings_async, set_raw16_mode_async
from .camera_usb_recovery import UsbResetWorker
from .camera_wedge_recovery import (  # noqa: F401 — re-exported for existing callers
    _MAX_WEDGED_SKIPS, _UNRECOVERABLE_ERROR_PATTERNS, _WEDGED_THREAD_JOIN_TIMEOUT_SEC,
    CameraRecoveryMixin, is_alpaca_session,
)

_DISCORD_ERROR_SUPPRESS_AFTER_ATTEMPTS = 3
_SUSTAINED_CAPTURE_RESET_SEC = 300
# Stop's teardown joins the capture thread for 3 s, then disconnects.
_TEARDOWN_JOIN_TIMEOUT_SEC = 10.0


class CameraControllerQt(QObject, CameraRecoveryMixin):
    """
    Qt-compatible camera controller.
    
    Uses existing ZWOCamera.start_capture() with callbacks.
    All capture logic (auto-exposure, calibration, etc.) is handled by ZWOCamera.
    """
    
    cameras_detected = Signal(list)  # List of camera names
    capture_started = Signal()
    capture_stopped = Signal()
    frame_ready = Signal(object, dict)  # PIL Image, metadata
    error = Signal(str)
    calibration_status = Signal(bool)  # True=calibrating, False=complete
    # Queued cross-thread completion signals — QTimer.singleShot from a
    # non-Qt worker thread silently never fires (log 2026-04-20 08:03).
    _usb_reset_done = Signal(bool)             # recovery path: ok?
    camera_revive_done = Signal(bool, str)     # user Revive: (ok, name)
    _capture_start_done = Signal(bool, str)    # worker result: (ok, error_msg)
    raw16_mode_done = Signal(bool, bool)       # RAW mode change: (requested_enabled, ok)

    def __init__(self, main_window, parent=None):
        super().__init__(parent)
        self.main_window = main_window
        self.config = main_window.config
        
        self.zwo_camera = None
        self.is_connected = False
        self.is_capturing = False

        # 24/7 rigs: without auto-recovery a single SDK wedge ends captures
        # for the night. See _schedule_auto_recovery.
        self._user_requested_stop = False
        self._auto_recovery_attempts = 0
        self._auto_recovery_schedule = [30, 120, 300, 900, 1800]
        self._auto_recovery_timer: QTimer | None = None
        self._last_successful_frame_ts = 0.0

        # Held across a fatal error so the capture thread can be joined
        # before the next recovery attempt; concurrent SDK calls from a
        # still-alive thread and a new reinit corrupt the ZWO DLL.
        self._dying_camera = None
        self._unrecoverable_mode = False
        self._usb_reset_attempted = False
        # One worker shared by recovery resets and the user's Revive button so
        # the two never toggle the same USB device at once.
        self._usb_reset = UsbResetWorker()
        self._suppress_discord_errors = False
        # Count of consecutive recovery attempts skipped because the dying
        # capture thread is still wedged inside the SDK.
        self._wedged_skip_count = 0
        # Whether we've already tried one USB toggle to free the current wedge.
        self._wedge_usb_reset_tried = False
        # Rolling-hour auto-restart timestamps (boot-loop guard), from config
        # so the cap survives a relaunch.
        from .camera_restart_policy import load_restart_history
        self._restart_times = load_restart_history(self.config)

        self._capture_starting = False
        self._starting_source = None
        self._logged_errors = collections.deque(maxlen=16)
        self._teardown_thread = None
        self._usb_reset_done.connect(self._on_usb_reset_done)
        self._capture_start_done.connect(self._on_capture_start_done)

    @property
    def camera_session(self):
        """The running capture session (stored as ``zwo_camera`` for its many readers)."""
        return self.zwo_camera

    def capabilities(self):
        """The running session's capabilities, else the configured source's static set."""
        from services.camera.capabilities import static_capabilities
        session = self.zwo_camera
        if session is not None:
            return session.capabilities
        return static_capabilities(self.config.get('camera_source', 'zwo'))

    def detect_cameras(self):
        """Detect connected ZWO cameras"""
        app_logger.info("Detecting cameras...")
        
        sdk_path = resolve_library_path(self.config.get('zwo_sdk_path', ''))

        if not sdk_path:
            self.error.emit(f"ZWO SDK not found ({library_name()})")
            return
        
        try:
            import zwoasi as asi
            
            try:
                asi.init(sdk_path)
            except Exception as e:
                if "already" not in str(e).lower():
                    self.error.emit(f"SDK init failed: {e}")
                    return
            
            num_cameras = asi.get_num_cameras()
            
            if num_cameras == 0:
                self.cameras_detected.emit([])
                return
            
            camera_list = []
            for i in range(num_cameras):
                try:
                    name = asi.list_cameras()[i]
                    camera_list.append(f"{name} (Index: {i})")
                except Exception as e:
                    # Skip cameras that fail to enumerate - they may be phantom devices
                    # or cameras in a bad state that can't be used anyway
                    app_logger.warning(f"Camera {i} failed to enumerate: {e} - skipping")
            
            self.cameras_detected.emit(camera_list)
            app_logger.info(f"Detected {len(camera_list)} camera(s)")
            
        except Exception as e:
            self.error.emit(f"Detection failed: {e}")
            app_logger.error(f"Camera detection failed: {e}")
    
    def _resolve_camera_index(self, sdk_path: str, camera_name: str, saved_index: int) -> int:
        """Re-detect cameras and resolve the correct index by name.

        Camera indices shift when other USB cameras (NINA, guide cam, etc.)
        come online or go offline. Delegates to the shared, hijack-safe
        resolver; the connection layer makes the final serial-checked choice.
        Raises if the target camera cannot be safely resolved.
        """
        from services.camera.camera_index_resolver import resolve_camera_index
        return resolve_camera_index(self.config, sdk_path, camera_name, saved_index)

    def start_capture(self):
        """Start camera capture using ZWOCamera's built-in capture loop"""
        if self.is_capturing or self._capture_starting:
            return

        self._cancel_auto_recovery_timer()
        self._user_requested_stop = False

        # Torn down on the start worker, before it makes any SDK call of its own.
        stale_camera = self.zwo_camera
        self.zwo_camera = None
        if stale_camera is not None:
            app_logger.info("Cleaning up existing camera instance...")

        # Read all config on the main thread — keeps cross-thread Config access
        # out of the blocking SDK calls that run in the worker thread.
        params = read_capture_params(self.config, self._auto_recovery_enabled())
        # Recovery decisions about this start follow its backend, not whatever
        # the config says by the time it fails.
        self._starting_source = params.get('camera_source')

        app_logger.info(
            f"Camera config: exposure_ms={params['exposure_ms']}, gain={params['gain']}, "
            f"auto_exposure={params['auto_exposure']}, max_exposure_ms={params['max_exposure_ms']}"
        )

        self._capture_starting = True
        threading.Thread(target=self._start_capture_worker, args=(params, stale_camera),
                         daemon=True).start()

    def _start_capture_worker(self, params: dict, stale_camera=None):
        """Blocking SDK work for start_capture — runs off the Qt main thread.

        _resolve_camera_index, ZWOCamera construction, and connect_camera all
        make synchronous ZWO SDK calls that can hang for 10-30s under USB
        instability. Running them here keeps the Qt event loop responsive.
        """
        try:
            if stale_camera is not None:
                self._stop_and_disconnect(stale_camera)
            self._wait_for_previous_capture()

            if params.get('camera_source') == ALPACA:
                camera_index = 0
            else:
                camera_index = self._resolve_camera_index(
                    params['sdk_path'], params['camera_name'], params['saved_camera_index']
                )

            cam = build_session(
                params, gate_for_config(self.config), camera_index,
                on_error=self._on_camera_error,
                on_calibration=self._on_calibration_status,
            )

            if not cam.connect_camera(camera_index):
                raise Exception(getattr(cam, 'last_connect_error', '')
                                or "Failed to connect to camera")

            app_logger.info("Starting capture loop...")
            cam.start_capture(
                on_frame_callback=self._on_frame_captured,
                on_log_callback=lambda msg: app_logger.info(msg),
            )

            # Store before emitting — queued signal ensures main thread sees it.
            self.zwo_camera = cam
            self.is_connected = True
            self._capture_start_done.emit(True, "")

        except Exception as e:
            import traceback
            app_logger.error(f"Failed to start capture: {e}")
            app_logger.debug(f"Stack trace: {traceback.format_exc()}")
            self._capture_start_done.emit(False, str(e))

    def _wait_for_previous_capture(self):
        """Wait out the last session's teardown and capture thread; raise if it lives on.

        Concurrent SDK access from a still-running capture thread crashes the
        ZWO DLL, and two Alpaca loops would drive one camera. Stop marks the
        session dying at once (not after its asynchronous join), so a quick
        Stop → Start lands here rather than skipping the wait.
        """
        teardown = self._teardown_thread
        if teardown is not None and teardown is not threading.current_thread():
            teardown.join(_TEARDOWN_JOIN_TIMEOUT_SEC)
        dying = self._dying_camera
        if dying is None:
            return
        try:
            exited = dying.wait_for_capture_thread_exit(timeout=_WEDGED_THREAD_JOIN_TIMEOUT_SEC)
        except Exception:
            exited = False
        if not exited:
            raise Exception("The previous capture is still shutting down; "
                            "try Start again in a few seconds")
        if self._dying_camera is dying:
            self._dying_camera = None

    def _persist_learned_serial(self):
        """Persist the camera's hardware serial learned during connect.

        Runs on the Qt main thread (config writes off the capture worker thread).
        """
        from services.camera.camera_identity import persist_camera_serial
        cam = self.zwo_camera
        if is_alpaca_session(cam):
            from services.camera.camera_source import persist_alpaca_identity
            unique_id, name = cam.learned_identity()
            if persist_alpaca_identity(self.config, unique_id, name):
                app_logger.info(f"Persisted Alpaca camera identity: {name or '?'} ({unique_id or 'no UniqueID'})")
            return
        serial = getattr(cam, 'camera_serial', None) if cam else None
        if persist_camera_serial(self.config, serial):
            app_logger.info(f"Persisted camera serial to config: {serial}")

    def _on_capture_start_done(self, ok: bool, err: str):
        """Handle _start_capture_worker result on the main Qt thread."""
        self._capture_starting = False

        if ok:
            if self._user_requested_stop:
                app_logger.info("Capture connected but stop was requested — tearing down")
                # The worker already started the capture loop; dropping the
                # reference alone would leave it running with the USB handle open.
                camera = self.zwo_camera
                self.zwo_camera = None
                if camera:
                    self._dying_camera = camera
                    self._shutdown_camera_async(camera)
                self.is_connected = False
                return
            self.is_capturing = True
            self._persist_learned_serial()
            self.capture_started.emit()
            app_logger.info("Camera capture started")
        else:
            self.is_capturing = False
            self.is_connected = False
            self.error.emit(err)
            from services.posthog_service import capture_error
            capture_error(Exception(err), context='camera_start')
            if self._user_requested_stop:
                return
            if not self._auto_recovery_enabled():
                app_logger.warning(
                    "Capture failed and automatic recovery is off — not retrying. "
                    "Click Start when the camera is ready."
                )
                return
            alpaca = self._alpaca_source()
            if not alpaca and self._is_unrecoverable_error(err):
                if not self._usb_reset_attempted:
                    self._usb_reset_attempted = True
                    self._start_usb_reset_worker(alpaca=False)
                    return
                self._enter_unrecoverable_mode(err, alpaca=False)
                return
            if self._last_successful_frame_ts > 0:
                self._schedule_auto_recovery()
            elif alpaca:
                # A rig can boot before its Alpaca server is listening.
                app_logger.warning("Alpaca camera not reachable yet — retrying under auto-recovery")
                self._schedule_auto_recovery()
            else:
                app_logger.warning(
                    "Capture failed before first frame — not auto-recovering. "
                    "Check connections and click Start when ready."
                )
    def stop_capture(self):
        """Stop camera capture"""
        if not self.is_capturing:
            return

        self._user_requested_stop = True
        self._cancel_auto_recovery_timer()
        self._unrecoverable_mode = False
        self._usb_reset_attempted = False
        self._suppress_discord_errors = False
        self._wedged_skip_count = 0
        self._wedge_usb_reset_tried = False

        try:
            # Update state immediately for responsive UI
            self.is_capturing = False
            self.is_connected = False

            # Capture reference before clearing — the background thread
            # needs the actual object, not self.zwo_camera which we null below
            camera = self.zwo_camera
            self.zwo_camera = None

            if camera:
                # Dying until the teardown has joined its capture thread; a
                # Start in between waits for it (_wait_for_previous_capture).
                self._dying_camera = camera
                self._shutdown_camera_async(camera)

            self.capture_stopped.emit()
            app_logger.info("Camera capture stopped")

        except Exception as e:
            app_logger.error(f"Error stopping capture: {e}")
    
    def _shutdown_camera_async(self, camera):
        """Stop and disconnect ``camera`` on a daemon thread, keeping the UI responsive."""
        thread = threading.Thread(target=self._stop_and_disconnect, args=(camera,), daemon=True)
        self._teardown_thread = thread
        thread.start()

    def _stop_and_disconnect(self, camera):
        """stop_capture() aborts the exposure and joins the capture thread;
        disconnect_camera() releases the hardware. Both can block in the SDK."""
        try:
            camera.stop_capture()
        except Exception as e:
            app_logger.debug(f"Error stopping capture: {e}")
        # Wedged worker (3s join timed out) still holds the SDK;
        # retain it so a later start waits instead of reinitialising
        # the SDK concurrently (which crashes the ZWO DLL). Only a joined
        # thread lets it go.
        try:
            thread = camera.capture_thread
            alive = isinstance(thread, threading.Thread) and thread.is_alive()
        except Exception:
            alive = True
        if alive:
            self._dying_camera = camera
        elif self._dying_camera is camera:
            self._dying_camera = None
        try:
            camera.disconnect_camera()
        except Exception as e:
            app_logger.debug(f"Error disconnecting camera: {e}")

    def _on_frame_captured(self, pil_image, metadata):
        """Callback from ZWOCamera when a frame is captured.
        
        This is called from the ZWOCamera's capture thread.
        We emit a Qt signal to safely update the UI.
        """
        # Add UI-specific metadata fields
        if metadata is None:
            metadata = {}
        metadata['filename'] = f"capture_{datetime.now().strftime('%H%M%S')}.jpg"
        metadata['timestamp'] = datetime.now().strftime('%H:%M:%S')
        
        # Emit signal (thread-safe way to update Qt UI — AutoConnection
        # marshals to the main thread because this runs on the capture thread).
        self.frame_ready.emit(pil_image, metadata)

        # Reset the retry budget after a sustained stream — otherwise a rig
        # that wedges once a day eventually exhausts attempts despite every
        # recovery succeeding.
        now = time.time()
        if self._auto_recovery_attempts and self._last_successful_frame_ts:
            if now - self._last_successful_frame_ts > _SUSTAINED_CAPTURE_RESET_SEC:
                app_logger.info("Sustained capture stream — resetting auto-recovery counter")
                self._auto_recovery_attempts = 0
                self._suppress_discord_errors = False
                self._usb_reset_attempted = False
        self._last_successful_frame_ts = now
    
    def _on_camera_error(self, error_msg, is_fatal: bool = False):
        """Callback from ZWOCamera on errors.

        Args:
            error_msg: Human-readable error description.
            is_fatal: True when the capture loop has terminated and cannot
                recover on its own. In that case we must drop our own
                is_capturing flag and emit capture_stopped so the UI (AppBar,
                tray menu) doesn't keep pretending capture is running.
        """
        # The one log line for this error (the main window's handler skips it,
        # see consume_logged_error). An Alpaca loop still retrying is a network
        # outage, logged at WARNING like the capture loop's own lines.
        if not is_fatal and is_alpaca_session(self.zwo_camera):
            app_logger.warning(f"Camera error: {error_msg}")
        else:
            app_logger.error(f"Camera error: {error_msg}")
        self._logged_errors.append(error_msg)
        self.error.emit(error_msg)

        if is_fatal:
            app_logger.error("Camera error is fatal — tearing down capture state for UI sync")
            # Mirror stop_capture()'s state reset, but without touching the
            # camera (the loop already exited and cleanup ran).
            self.is_capturing = False
            self.is_connected = False
            if self.zwo_camera is not None:
                self._dying_camera = self.zwo_camera
            self.zwo_camera = None
            self.capture_stopped.emit()
            if not self._user_requested_stop:
                self._schedule_auto_recovery()

    def consume_logged_error(self, error_msg) -> bool:
        """True (once) when ``error_msg`` reached the log from the capture loop's
        callback, so the main window's error handler doesn't log it again."""
        try:
            self._logged_errors.remove(error_msg)
            return True
        except ValueError:
            return False

    def _auto_recovery_enabled(self) -> bool:
        # Only an explicit False opts out — a damaged value must not silently
        # strip recovery from an unattended rig.
        return self.config.get('camera_auto_recovery', True) is not False

    def set_auto_recovery_enabled(self, enabled: bool):
        """Apply the Settings toggle to a running session (GUI thread)."""
        if self.zwo_camera is not None:
            self.zwo_camera.auto_recovery_enabled = enabled
        if not enabled:
            self._cancel_auto_recovery_timer()
            self._auto_recovery_attempts = 0

    def _schedule_auto_recovery(self):
        # The restart timer, and everything it leads to, starts here. The two
        # paths that don't — a failed start and a finished USB reset — carry
        # their own gate (_on_capture_start_done, _on_usb_reset_done).
        if not self._auto_recovery_enabled():
            app_logger.warning(
                "Automatic recovery is off — capture stays stopped until "
                "started manually."
            )
            return
        if self._unrecoverable_mode:
            app_logger.info(
                "Auto-recovery suppressed — in unrecoverable mode, awaiting "
                "manual restart."
            )
            return
        # Schedule clamps at the final interval rather than stopping — on a
        # 24/7 rig, keep trying forever is better than giving up.
        idx = min(self._auto_recovery_attempts, len(self._auto_recovery_schedule) - 1)
        delay_s = self._auto_recovery_schedule[idx]
        self._auto_recovery_attempts += 1

        if (
            not self._suppress_discord_errors
            and self._auto_recovery_attempts > _DISCORD_ERROR_SUPPRESS_AFTER_ATTEMPTS
        ):
            self._suppress_discord_errors = True
            app_logger.warning(
                f"Auto-recovery: reached attempt #{self._auto_recovery_attempts}; "
                "suppressing further Discord error pings until capture resumes."
            )

        app_logger.warning(
            f"Auto-recovery: scheduling capture restart in {delay_s}s "
            f"(attempt #{self._auto_recovery_attempts})"
        )

        self._cancel_auto_recovery_timer()
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.timeout.connect(self._on_auto_recovery_fire)
        timer.start(delay_s * 1000)
        self._auto_recovery_timer = timer

    def _cancel_auto_recovery_timer(self):
        if self._auto_recovery_timer is not None:
            try:
                self._auto_recovery_timer.stop()
            except Exception:
                pass
            self._auto_recovery_timer.deleteLater()
            self._auto_recovery_timer = None

    def _on_auto_recovery_fire(self):
        self._auto_recovery_timer = None

        if self._user_requested_stop:
            app_logger.info("Auto-recovery: user stop requested — cancelled")
            return
        if self.is_capturing:
            app_logger.info("Auto-recovery: capture already running — cancelled")
            return

        if not self._join_or_skip_dying_camera():
            return

        app_logger.info(
            f"Auto-recovery: attempting capture restart "
            f"(attempt #{self._auto_recovery_attempts})"
        )
        try:
            self.start_capture()
        except Exception as e:
            # Safety net: start_capture's except block already re-schedules,
            # but if the exception escaped before that block (e.g. import
            # failure), keep the recovery chain alive.
            app_logger.error(f"Auto-recovery restart raised: {e}")
            self._schedule_auto_recovery()

    def recovery_state(self) -> dict:
        """Snapshot of auto-recovery state for the status API.

        Intent-revealing accessor so the status feeder doesn't reach into
        private attributes. 'in_progress' is true while a recovery timer is
        pending or an auto-recovery attempt has been made without a subsequent
        successful frame.
        """
        in_progress = (
            self._auto_recovery_timer is not None
            or (self._auto_recovery_attempts > 0 and not self.is_capturing)
        )
        return {
            "in_progress": bool(in_progress and not self._unrecoverable_mode),
            "attempts": self._auto_recovery_attempts,
            "unrecoverable": self._unrecoverable_mode,
        }

    def last_successful_frame_epoch(self):
        """Unix timestamp of the last successful frame, or None if none yet."""
        return self._last_successful_frame_ts or None

    def should_notify_discord(self) -> bool:
        return not self._suppress_discord_errors

    def mark_discord_notified(self):
        """Call after sending a Discord error so the unrecoverable-mode
        one-shot notification silences subsequent per-attempt pings."""
        if self._unrecoverable_mode:
            self._suppress_discord_errors = True
    
    def _on_calibration_status(self, is_calibrating: bool):
        """Callback from ZWOCamera when calibration status changes
        
        Args:
            is_calibrating: True when calibration starts, False when complete
        """
        self.calibration_status.emit(is_calibrating)
    
    def update_settings(self):
        """Update camera settings from config (live update, runs off-thread)."""
        apply_camera_settings_async(self)

    def set_raw16_mode(self, enabled: bool):
        """Change RAW8/RAW16 mode on the live camera (runs off-thread)."""
        set_raw16_mode_async(self, enabled)
