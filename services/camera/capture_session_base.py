"""Backend-neutral half of a camera capture session.

Schedule gate, capture interval, capture-now wake, the capture thread's
start / stop / join, and the auto-exposure wrappers — moved verbatim out of
zwo_camera.py so every backend runs the same lifecycle. A subclass supplies
``_connection`` (with ``sdk_lock``), the ``camera`` / ``asi`` handles,
``current_bit_depth`` and the hardware methods in session.CaptureSession.

``stop_capture`` is the documented ZWO-safe shutdown: abort any calibration,
take ``_connection.sdk_lock`` (2 s) to stop the exposure, then join the capture
thread for 3 s and keep the reference if it is still alive.
"""
import threading

from .camera_calibration import CameraCalibration
from .camera_utils import is_within_scheduled_window as check_scheduled_window
from .capture_loop import capture_loop as run_capture_loop
from ..logger import app_logger


class CaptureSessionBase:
    """Lifecycle and scheduling shared by every camera session."""

    backend = 'unknown'
    _GAIN_LIMITS = (0, 600)

    def __init__(self, exposure_sec=1.0, gain=100, auto_exposure=False,
                 max_exposure_sec=30.0, auto_wb=False, wb_mode='asi_auto',
                 wb_config=None, bayer_pattern='BGGR',
                 scheduled_capture_enabled=False, scheduled_start_time="17:00",
                 scheduled_end_time="09:00", scheduled_capture_mode=None,
                 scheduled_window_interval=5.0, status_callback=None):
        # Callbacks first: a subclass hands self.log to its connection
        # manager right after this returns.
        self.on_log_callback = None
        self.on_frame_callback = None
        self.on_calibration_callback = None
        self.on_error_callback = None

        # Capture state
        self.is_capturing = False
        self.capture_thread = None
        # Set by the UI-level watchdog to nudge the capture thread into
        # running its own reconnect on the next poll point.  Kept here (not
        # on the connection) because the capture worker reads it directly.
        self._recovery_requested = False
        self.status_callback = status_callback  # Callback for schedule status updates

        # Capture settings
        self.exposure_seconds = exposure_sec
        self.gain = gain
        self.capture_interval = 5.0  # Seconds between captures
        self.auto_exposure = auto_exposure
        self.max_exposure = max_exposure_sec  # Max exposure for auto mode
        self.target_brightness = 100  # Target brightness for auto exposure
        self.exposure_algorithm = 'percentile'  # 'mean', 'median', or 'percentile'
        self.exposure_percentile = 75  # Use 75th percentile (focuses on brighter areas)
        self.clipping_threshold = 245  # Consider pixels > this value as clipped
        self.clipping_prevention = True  # Prevent further exposure increase if clipping detected
        self.auto_wb = auto_wb
        self.wb_config = wb_config if wb_config else {'mode': wb_mode}  # Full WB config
        self.bayer_pattern = bayer_pattern  # RGGB, BGGR, GRBG, GBRG
        # One-shot "capture now": the worker's inter-frame wait polls it so a
        # diagnostics export need not sit out a long capture interval.
        self._capture_now = threading.Event()

        # Scheduled capture settings
        # mode: "always" | "gated" | "variable" — see services/config.py for semantics.
        # If caller didn't specify a mode, derive it from the legacy boolean so older
        # entry points (tests, headless runner pre-migration) still get sensible behaviour.
        self.scheduled_capture_mode = scheduled_capture_mode or (
            "gated" if scheduled_capture_enabled else "always"
        )
        self.scheduled_capture_enabled = self.scheduled_capture_mode != "always"
        self.scheduled_start_time = scheduled_start_time  # Format: "HH:MM"
        self.scheduled_end_time = scheduled_end_time      # Format: "HH:MM"
        self.scheduled_window_interval = scheduled_window_interval  # seconds (variable mode)
        # Assigned from config by whoever builds the camera (see
        # capture_schedule_window.gate_for_config). None keeps the legacy
        # fixed HH:MM window; a gate follows the timelapse window instead.
        self.schedule_gate = None

        # Exposure tracking for UI
        self.exposure_start_time = None
        self.exposure_remaining = 0.0

        # Rapid calibration mode
        self.calibration_mode = False  # Fast convergence before normal capture
        self.calibration_complete = False

        # Initialize calibration manager
        self.calibration_manager = None  # Will be initialized after camera connection

    def log(self, message):
        """Send log message via callback"""
        if self.on_log_callback:
            self.on_log_callback(message)
        app_logger.debug(message)
    
    def _gate_verdict(self):
        """The schedule gate's verdict, or None when no gate is set.

        Fails open on error, like check_scheduled_window does — a schedule
        fault must never stop an unattended camera, nor escape into the
        capture loop.
        """
        gate = self.schedule_gate
        if gate is None:
            return None
        try:
            return bool(gate())
        except Exception as e:
            app_logger.debug(f"Schedule gate error ({e}), allowing capture")
            return True

    def _legacy_window(self):
        return check_scheduled_window(
            True,
            self.scheduled_start_time,
            self.scheduled_end_time
        )

    def is_within_scheduled_window(self):
        """
        Whether capture is currently permitted (i.e. we should NOT pause).

        Only ``gated`` mode ever pauses capture — ``always`` and ``variable``
        both keep the camera live around the clock. The time window still
        matters in ``variable`` mode, but only for interval selection, which
        is handled by :pyattr:`effective_capture_interval`.
        """
        if self.scheduled_capture_mode != "gated":
            return True
        verdict = self._gate_verdict()
        return self._legacy_window() if verdict is None else verdict

    def is_in_time_window(self):
        """True when the current clock falls inside the configured window, mode-independent."""
        verdict = self._gate_verdict()
        return self._legacy_window() if verdict is None else verdict

    def scheduled_window_label(self):
        """Human-readable capture window, for logs and the status API."""
        gate = self.schedule_gate
        if gate is not None:
            try:
                return gate.describe()
            except Exception as e:
                app_logger.debug(f"Schedule gate describe error ({e})")
        return f"{self.scheduled_start_time} - {self.scheduled_end_time}"

    def request_immediate_capture(self):
        """Ask the capture loop to skip the rest of the current inter-frame wait."""
        self._capture_now.set()

    def consume_immediate_capture(self) -> bool:
        """True once per request_immediate_capture(); clears the flag."""
        if self._capture_now.is_set():
            self._capture_now.clear()
            return True
        return False

    @property
    def effective_capture_interval(self):
        """The interval (seconds) to wait before the next capture, honouring the schedule mode."""
        if self.scheduled_capture_mode == "variable" and self.is_in_time_window():
            return max(1.0, float(self.scheduled_window_interval))
        return self.capture_interval
    
    def _init_calibration_manager(self):
        """Initialize the calibration manager for connected camera"""
        if not self.camera:
            return
        
        self.log(f"Initializing calibration manager (max_exposure={self.max_exposure}s)...")
        self.calibration_manager = CameraCalibration(
            self.camera, self.asi, self.log, 
            bit_depth=self.current_bit_depth  # Pass current RAW mode bit depth
        )
        self.calibration_manager.update_settings(
            exposure_seconds=self.exposure_seconds,
            gain=self.gain,
            target_brightness=self.target_brightness,
            max_exposure_sec=self.max_exposure,
            algorithm=self.exposure_algorithm,
            percentile=self.exposure_percentile,
            clipping_threshold=self.clipping_threshold,
            clipping_prevention=self.clipping_prevention
        )
    
    def capture_loop(self):
        return run_capture_loop(self)

    def start_capture(self, on_frame_callback, on_log_callback=None):
        """Start continuous capture"""
        if self.is_capturing:
            self.log("Capture already running")
            return False
        # A wake left over from a previous session must not skip this one's
        # first inter-frame wait.
        self._capture_now.clear()
        
        if not self.camera:
            self.log("ERROR: Camera not connected")
            return False
        
        self.on_frame_callback = on_frame_callback
        self.on_log_callback = on_log_callback
        self.is_capturing = True
        
        # Start capture thread
        self.capture_thread = threading.Thread(target=self.capture_loop, daemon=True)
        self.capture_thread.start()
        
        return True
    
    def wait_for_capture_thread_exit(self, timeout: float = 10.0) -> bool:
        """Wait for the capture thread to exit without issuing new SDK calls.

        Used by the UI-level auto-recovery path: after the watchdog declares a
        fatal wedge, the capture thread may still be blocked inside an ASI SDK
        call. Starting a new ZWOCamera instance (which reinitialises the SDK)
        while that thread is alive has caused C++ SEH exceptions and access
        violations from concurrent SDK access. Join the thread first, then
        recover.

        Unlike stop_capture(), this does NOT call stop_exposure() or touch
        the SDK at all — the caller has already decided the camera is wedged
        and SDK calls may themselves hang or crash. We just wait for the
        thread to unwind on its own.

        Returns True if the thread exited (or was never alive), False on timeout.
        """
        self.is_capturing = False
        if self.calibration_manager:
            try:
                self.calibration_manager.abort()
            except Exception:
                pass

        if not self.capture_thread or not self.capture_thread.is_alive():
            return True

        self.log(f"Waiting up to {timeout:.0f}s for wedged capture thread to exit...")
        self.capture_thread.join(timeout=timeout)
        if self.capture_thread.is_alive():
            self.log(
                "⚠ Capture thread did not exit within timeout — "
                "SDK call still blocked; recovery will proceed anyway"
            )
            return False
        self.log("✓ Capture thread exited cleanly before recovery")
        self.capture_thread = None
        return True

    def stop_capture(self):
        """Stop continuous capture and wait for thread to finish"""
        if not self.is_capturing:
            return

        self.log("Stopping capture...")
        self.is_capturing = False
        self._recovery_requested = False

        # Abort any running calibration so its loop exits quickly
        if self.calibration_manager:
            self.calibration_manager.abort()

        # Abort any in-progress exposure so the poll loop exits immediately.
        # During calibration exposure_start_time isn't set, so also fire when a
        # calibration exposure may be in flight. Take sdk_lock (briefly) so we
        # don't issue stop_exposure() concurrently with the worker's own SDK
        # calls — a documented ZWO DLL corruption trigger. If the worker is
        # wedged holding the lock, the short timeout lets us give up; the
        # bounded readout/calibration timeouts surface the wedge instead.
        if self.camera and (self.exposure_start_time is not None or self.calibration_mode):
            lock = self._connection.sdk_lock
            if lock.acquire(timeout=2.0):
                try:
                    if self.camera:
                        self.camera.stop_exposure()
                except Exception:
                    pass
                finally:
                    lock.release()

        # Wait for capture thread — should exit quickly now that
        # is_capturing is False and exposure is aborted
        if self.capture_thread and self.capture_thread.is_alive():
            self.log("Waiting for capture thread to finish...")
            self.capture_thread.join(timeout=3.0)

            if self.capture_thread.is_alive():
                # Keep the reference: the worker is wedged inside an SDK call,
                # and reinitialising the SDK while it's alive corrupts the ZWO
                # DLL. Callers join it via wait_for_capture_thread_exit().
                self.log("Warning: Capture thread still running (will finish in background)")
            else:
                self.log("Capture thread finished successfully")
                self.capture_thread = None

        self.log("Capture stopped")
    
    def set_exposure(self, seconds):
        """Set exposure time in seconds"""
        self.exposure_seconds = max(0.000001, min(3600, seconds))
    
    def set_gain(self, gain):
        """Set gain value"""
        low, high = self._GAIN_LIMITS
        self.gain = max(low, min(high, int(gain)))
    
    def set_capture_interval(self, seconds):
        """Set interval between captures"""
        self.capture_interval = max(1.0, seconds)
    
    def run_calibration(self):
        """Rapid calibration to find optimal exposure before starting interval captures"""
        if not self.auto_exposure or not self.camera or not self.calibration_manager:
            return

        self.log(f"Starting rapid auto-exposure calibration... "
                 f"(max_exposure={self.max_exposure}s, "
                 f"cal_max={self.calibration_manager.max_exposure_sec}s)")
        self.calibration_mode = True

        # Notify UI that calibration is starting
        if self.on_calibration_callback:
            self.on_calibration_callback(True)

        # Run calibration using the calibration manager
        import time as _time
        _cal_start = _time.time()
        success = self.calibration_manager.run_calibration(max_attempts=15)
        _cal_duration = _time.time() - _cal_start

        # Update our exposure from calibration manager
        self.exposure_seconds = self.calibration_manager.exposure_seconds

        self.calibration_complete = True
        self.calibration_mode = False

        # PostHog: calibration results
        from ..posthog_service import capture_event
        cal_history = getattr(self.calibration_manager, 'calibration_history', [])
        capture_event('calibration_completed', {
            'success': success,
            'duration_seconds': round(_cal_duration, 1),
            'attempts': len(cal_history) if cal_history else None,
            'final_exposure_ms': round(self.exposure_seconds * 1000, 2),
            'final_brightness': round(cal_history[-1][1], 1) if cal_history else None,
            'target_brightness': self.calibration_manager.target_brightness,
            'max_exposure_ms': round(self.max_exposure * 1000, 0),
        })

        # Notify UI that calibration is complete
        if self.on_calibration_callback:
            self.on_calibration_callback(False)
    
    def adjust_exposure_auto(self, img_array):
        """
        Adjust exposure based on image brightness with intelligent step sizing.
        
        Returns:
            dict with 'needs_recalibration' flag and brightness info, or None if auto-exposure disabled
        """
        if not self.auto_exposure or not self.calibration_manager:
            return None
        
        # Use calibration manager to adjust exposure
        result = self.calibration_manager.adjust_exposure_auto(img_array)
        
        # Update our exposure from calibration manager
        self.exposure_seconds = self.calibration_manager.exposure_seconds
        
        return result
