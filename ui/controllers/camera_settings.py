import threading

from services.logger import app_logger
from services.capture_schedule_window import gate_for_config
from services.config import DEFAULT_CAMERA_PROFILE
from services.camera.capabilities import CameraCapabilities
from services.camera.camera_source import active_camera_identity, is_alpaca
from .camera_wedge_recovery import is_alpaca_session


def apply_camera_settings(zwo_camera, config):
    """Push all mutable settings to a running capture session (no stop/restart needed).

    The caller is responsible for checking zwo_camera is not None before calling.
    """
    caps = _capabilities(zwo_camera)
    if is_alpaca_session(zwo_camera) or is_alpaca(config):
        profile = _alpaca_profile(zwo_camera, config)
        if profile is None:
            return
    else:
        profile = _zwo_profile(zwo_camera, config)
        if profile is None:
            return

    exposure_ms = profile.get('exposure_ms', DEFAULT_CAMERA_PROFILE['exposure_ms'])
    gain = profile.get('gain', DEFAULT_CAMERA_PROFILE['gain'])
    auto_exposure = config.get('zwo_auto_exposure', False)
    target_brightness = profile.get('target_brightness', DEFAULT_CAMERA_PROFILE['target_brightness'])
    max_exposure_ms = profile.get('max_exposure_ms', DEFAULT_CAMERA_PROFILE['max_exposure_ms'])

    zwo_camera.auto_exposure = auto_exposure
    zwo_camera.target_brightness = target_brightness
    zwo_camera.max_exposure = max_exposure_ms / 1000.0

    if not auto_exposure:
        zwo_camera.set_exposure(exposure_ms / 1000.0)
    else:
        # Clamp auto-exposure immediately if max was lowered below current value.
        max_sec = max_exposure_ms / 1000.0
        if zwo_camera.exposure_seconds > max_sec:
            app_logger.info(
                f"Clamping auto-exposure from "
                f"{zwo_camera.exposure_seconds*1000:.0f}ms to "
                f"new max {max_sec*1000:.0f}ms"
            )
            zwo_camera.set_exposure(max_sec)

    zwo_camera.set_gain(gain)

    if zwo_camera.calibration_manager:
        # When auto-exposure is active, do NOT override the current exposure —
        # let the algorithm keep its computed value.
        cal_exposure = None if auto_exposure else exposure_ms / 1000.0
        zwo_camera.calibration_manager.update_settings(
            exposure_seconds=cal_exposure,
            gain=gain,
            target_brightness=target_brightness,
            max_exposure_sec=max_exposure_ms / 1000.0,
        )

    zwo_camera.set_capture_interval(config.get('zwo_interval', 5.0))

    # Live SDK writes go through sdk_lock-guarded helpers — a bare
    # set_control_value here races the capture worker's SDK calls and can
    # corrupt the ZWO DLL.
    offset = profile.get('offset', DEFAULT_CAMERA_PROFILE['offset'])
    if caps is None or caps.offset:
        zwo_camera.set_offset_live(offset)

    flip = profile.get('flip', DEFAULT_CAMERA_PROFILE['flip'])
    if flip != zwo_camera.flip:
        zwo_camera.set_flip_live(flip)

    bayer = profile.get('bayer_pattern', DEFAULT_CAMERA_PROFILE['bayer_pattern'])
    # A device-reported pattern (Alpaca SensorType/BayerOffset) must not be
    # clobbered by the profile's value on every settings change.
    if caps is None or not caps.bayer_from_device:
        zwo_camera.bayer_pattern = bayer

    mode = config.get('scheduled_capture_mode', 'always')
    zwo_camera.scheduled_capture_mode = mode
    zwo_camera.scheduled_capture_enabled = mode != 'always'
    zwo_camera.scheduled_start_time = config.get('scheduled_start_time', '17:00')
    zwo_camera.scheduled_end_time = config.get('scheduled_end_time', '09:00')
    zwo_camera.scheduled_window_interval = config.get('scheduled_window_interval', 5.0)
    # Re-read on every settings change so switching the window source — or
    # editing the Timelapse window it follows — takes effect without a restart.
    zwo_camera.schedule_gate = gate_for_config(config)

    wb_settings = config.get('white_balance', {})
    zwo_camera.wb_config = dict(wb_settings)
    zwo_camera.wb_config.setdefault('mode', 'asi_auto')
    zwo_camera.set_white_balance_live(
        zwo_camera.wb_config['mode'],
        profile.get('wb_r', DEFAULT_CAMERA_PROFILE['wb_r']),
        profile.get('wb_b', DEFAULT_CAMERA_PROFILE['wb_b']),
    )

    app_logger.debug(
        f"Settings updated live: exposure={exposure_ms}ms, gain={gain}, "
        f"max_exposure={max_exposure_ms}ms, interval={zwo_camera.capture_interval}s, "
        f"offset={offset}, flip={flip}, bayer={bayer}"
    )


def _capabilities(session):
    try:
        caps = session.capabilities
    except Exception:
        return None
    return caps if isinstance(caps, CameraCapabilities) else None


def _zwo_profile(zwo_camera, config):
    """The selected ZWO camera's profile, or None when it is not the running one."""
    camera_name = config.get('zwo_selected_camera_name', '')
    if '(Index:' in camera_name:
        camera_name = camera_name.split('(Index:')[0].strip()
    serial = config.get('zwo_selected_camera_serial', '')

    # Identity guard: this pushes the *selected* camera's profile onto the
    # *running* camera. If the user changed the dropdown to a different body
    # mid-capture, the selection and the live camera have decoupled — pushing
    # here would drive the running camera with another camera's gain/exposure/
    # target-brightness (and never actually switch hardware). Switching cameras
    # requires Stop → select → Start; refuse the cross-camera push instead.
    running_name = (getattr(zwo_camera, 'camera_name', '') or '').strip()
    if camera_name and running_name and camera_name != running_name:
        app_logger.warning(
            f"Skipping live settings push: selected camera '{camera_name}' differs "
            f"from the running camera '{running_name}'. Stop capture and Start again "
            "to switch cameras."
        )
        return None

    return (
        config.get_camera_profile(camera_name, serial) if camera_name
        else dict(DEFAULT_CAMERA_PROFILE)
    )


def _alpaca_profile(session, config):
    """The configured Alpaca camera's profile, or None when it is not the running one.

    The identity is the profile key (``alpaca:<UniqueID>``, or host:port:n
    before one is learned); a config that switched source or server
    mid-capture no longer describes the running camera.
    """
    if not (is_alpaca_session(session) and is_alpaca(config)):
        app_logger.warning("Skipping live settings push: the camera source changed "
                           "while capturing. Stop capture and Start again to switch.")
        return None
    name, key = active_camera_identity(config)
    running_key = (getattr(session, 'camera_serial', '') or '').strip()
    if key and running_key and key != running_key:
        app_logger.warning(
            f"Skipping live settings push: the configured Alpaca camera ({key}) differs "
            f"from the running one ({running_key}). Stop capture and Start again "
            "to switch cameras."
        )
        return None
    if not (name or key):
        return dict(DEFAULT_CAMERA_PROFILE)
    return config.get_camera_profile(name, key) or dict(DEFAULT_CAMERA_PROFILE)


def apply_camera_settings_async(controller):
    """Run apply_camera_settings on a daemon thread.

    apply_camera_settings issues sdk_lock-guarded SDK writes (offset/flip) that
    can briefly block if the capture worker holds the lock; keeping it off the
    Qt main thread avoids any UI stall under USB instability.
    """
    cam = controller.zwo_camera
    if not cam:
        return

    def _apply():
        try:
            apply_camera_settings(cam, controller.config)
        except Exception as e:
            app_logger.error(f"Failed to update camera settings: {e}")

    threading.Thread(target=_apply, daemon=True).start()


def set_raw16_mode_async(controller, enabled: bool):
    """Change RAW8/RAW16 on the live camera off the Qt main thread.

    ZWOCamera.set_raw16_mode() takes sdk_lock and issues several SDK calls; on a
    marginal USB bus those can block for the USB-timeout duration, freezing the
    event loop if run inline. Result is delivered via the controller's
    raw16_mode_done signal so the panel can revert its toggle on failure.
    """
    cam = controller.zwo_camera
    if not cam:
        controller.raw16_mode_done.emit(enabled, False)
        return

    def _worker():
        ok = False
        try:
            ok = cam.set_raw16_mode(enabled)
        except Exception as e:
            app_logger.error(f"RAW mode change failed: {e}")
        controller.raw16_mode_done.emit(enabled, ok)

    threading.Thread(target=_worker, daemon=True).start()
