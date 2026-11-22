"""Wedged-thread and corrupted-SDK recovery for ``CameraControllerQt``.

Extracted from camera_controller.py for the size cap. The USB disable/enable,
the Revive button and the unrecoverable-DLL patterns are ZWO-only: an Alpaca
session makes bounded HTTP calls, so a stuck capture thread there is rescheduled
and then escalated to the restart policy without any USB step (issue #43).
"""
from services.logger import app_logger
from services.camera.camera_source import ALPACA, camera_source

# ZWO SDK errors that corrupt the DLL for the process lifetime — only a
# USB reset or app restart recovers.
_UNRECOVERABLE_ERROR_PATTERNS = (
    "access violation",
    "0xe06d7363",
    "winerror -529697949",
    "exception: exception",
)

_WEDGED_THREAD_JOIN_TIMEOUT_SEC = 3.0
# Wedge handling: the capture thread is stuck inside an uninterruptible ZWO SDK
# C call. We try ONE USB disable/enable to free it (an OS-level toggle, safe
# against the wedged DLL), then escalate. Keeping this small means we reach the
# real cure (a process restart) in minutes, not the ~90 it used to take.
_MAX_WEDGED_SKIPS = 2


def is_alpaca_session(session) -> bool:
    return getattr(session, 'backend', None) == ALPACA


class CameraRecoveryMixin:
    """Recovery helpers mixed into ``CameraControllerQt``."""

    def _alpaca_source(self, session=None) -> bool:
        """True when recovery concerns an Alpaca camera: the stuck session, else
        the source the last start ran on, else config."""
        if session is not None:
            return is_alpaca_session(session)
        started = getattr(self, '_starting_source', None)
        if started is not None:
            return started == ALPACA
        try:
            return camera_source(self.config) == ALPACA
        except Exception:
            return False

    def _join_or_skip_dying_camera(self) -> bool:
        """Wait for the previous capture thread to exit; skip recovery if it
        is still wedged.

        Returns True if it's safe to proceed with SDK calls, False if the
        caller must abort this recovery cycle.  Calling the ZWO SDK while
        another thread is blocked inside it crashes the DLL (SEH 0xe06d7363);
        Windows USB IO usually times out within 30–60s, so we prefer to
        re-schedule rather than race.
        """
        dying = self._dying_camera
        if dying is None:
            self._wedged_skip_count = 0
            return True
        try:
            joined = dying.wait_for_capture_thread_exit(
                timeout=_WEDGED_THREAD_JOIN_TIMEOUT_SEC
            )
        except Exception as e:
            app_logger.warning(f"Error while joining dying capture thread: {e}")
            joined = False
        if joined:
            self._dying_camera = None
            self._wedged_skip_count = 0
            self._wedge_usb_reset_tried = False
            return True
        self._wedged_skip_count += 1
        alpaca = is_alpaca_session(dying)
        # The thread is stuck inside an uninterruptible ZWO SDK C call; the
        # backoff ladder alone can never free it. A USB disable/enable is an
        # OS-level operation (NOT an SDK call — safe against the wedged DLL)
        # that usually forces the stuck call to error out so the thread unwinds.
        # Try it once before escalating to a restart.
        if not alpaca and not self._wedge_usb_reset_tried and not self._usb_reset.in_progress:
            self._wedge_usb_reset_tried = True
            app_logger.warning(
                "Capture thread wedged in the ZWO SDK — attempting USB "
                "disable/enable to free it before escalating."
            )
            self._start_usb_reset_worker(alpaca=False)
            return False
        if self._wedged_skip_count >= _MAX_WEDGED_SKIPS:
            app_logger.error(
                f"Previous capture thread still wedged after "
                f"{self._wedged_skip_count} recovery attempts."
            )
            self._escalate_to_restart_or_alert(
                "Alpaca capture thread did not exit; process restart required" if alpaca
                else "capture thread stuck inside ZWO SDK; process restart required",
                alpaca=alpaca,
            )
            return False
        app_logger.warning(
            f"Previous capture thread still wedged "
            f"(skip {self._wedged_skip_count}/{_MAX_WEDGED_SKIPS}) — "
            "rescheduling retry to avoid concurrent SDK crash."
        )
        self._schedule_auto_recovery()
        return False

    @staticmethod
    def _is_unrecoverable_error(message: str) -> bool:
        if not message:
            return False
        lowered = message.lower()
        return any(pat in lowered for pat in _UNRECOVERABLE_ERROR_PATTERNS)

    def _start_usb_reset_worker(self, alpaca=None):
        """USB-reset the ZWO camera, then recovery continues from _on_usb_reset_done.

        ``alpaca`` is the stuck camera's backend, decided by the caller from the
        session (or the start that failed). The config is only a fallback: the
        user may have switched source since that camera stopped, and a skipped
        reset that schedules nothing would end recovery for good.
        """
        if alpaca is None:
            alpaca = self._alpaca_source()
        if alpaca:
            app_logger.info("USB reset skipped: the camera is on an Alpaca server")
            self._schedule_auto_recovery()
            return
        app_logger.warning(
            "Attempting USB device disable/enable to recover the camera..."
        )
        self._usb_reset.run_async(
            self.config.get('zwo_selected_camera_name', '') or '',
            lambda ok, _name: self._usb_reset_done.emit(ok),
        )

    def _on_usb_reset_done(self, success: bool):
        # The switch can't recall a reset already in flight when it was turned
        # off; drop the result so a failure can't go on to relaunch the app.
        if not self._auto_recovery_enabled():
            app_logger.info("USB reset finished after automatic recovery was turned off — ignored")
            return
        if success:
            # The toggle may have freed a wedged capture thread; re-run
            # recovery, which re-joins the dying thread before any new SDK call.
            # _wedge_usb_reset_tried stays set so a still-wedged thread escalates
            # rather than looping resets forever.
            self._schedule_auto_recovery()
            return
        # USB reset failed — usually admin denial (CM_Disable_DevNode 0x17) or
        # not on Windows. The DLL stays corrupt for the process lifetime, so the
        # only heal left is a clean process restart (inside the capture window);
        # otherwise alert and wait for a manual restart.
        self._escalate_to_restart_or_alert(
            "USB reset failed — Administrator rights are required to "
            "disable/enable the camera device"
        )

    def _escalate_to_restart_or_alert(self, last_error: str, alpaca=None):
        """Last resort once in-process recovery is exhausted: restart the app if
        the policy allows (inside the capture window, not boot-looping), else
        give up and alert for a manual restart. See camera_restart_policy."""
        from .camera_restart_policy import attempt_restart
        if attempt_restart(self, last_error):
            return
        self._enter_unrecoverable_mode(last_error, alpaca=alpaca)

    def revive_missing_camera(self, camera_name: str):
        """Best-effort user-triggered USB reset. Emits camera_revive_done."""
        if self._alpaca_source():
            app_logger.info("Revive is a USB reset; it does not apply to an Alpaca camera")
            self.camera_revive_done.emit(False, camera_name or '')
            return
        app_logger.info(f"User-initiated revive for '{camera_name}'")
        self._usb_reset.run_async(
            camera_name,
            lambda ok, name: self.camera_revive_done.emit(ok, name),
        )

    def _enter_unrecoverable_mode(self, last_error: str, alpaca=None):
        self._unrecoverable_mode = True
        self._cancel_auto_recovery_timer()
        self.capture_stopped.emit()
        if alpaca is None:
            alpaca = self._alpaca_source(self._dying_camera)
        cause = ("the capture thread is stuck" if alpaca
                 else "ZWO SDK state is corrupted")
        self.error.emit(
            f"Camera unrecoverable — {cause}. "
            "Please restart the application. "
            f"(last error: {last_error})"
        )
