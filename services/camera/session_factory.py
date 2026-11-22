"""Build a configured, not yet connected, capture session from start parameters.

``params`` is what ``session_params.read_capture_params`` returned. The
post-construction writes keep the order the controller always used. Runs on
the start worker thread; it reads no config of its own (``gate`` is built on
the main thread).
"""
from .zwo_camera import ZWOCamera


def build_zwo_session(params: dict, gate, camera_index: int, on_error,
                      on_calibration) -> ZWOCamera:
    """A ``ZWOCamera`` for ``params`` at the resolved SDK ``camera_index``."""
    cam = ZWOCamera(
        sdk_path=params['sdk_path'],
        camera_index=camera_index,
        exposure_sec=params['exposure_ms'] / 1000.0,
        gain=params['gain'],
        white_balance_r=params['wb_r'],
        white_balance_b=params['wb_b'],
        offset=params['offset'],
        flip=params['flip'],
        auto_exposure=params['auto_exposure'],
        max_exposure_sec=params['max_exposure_ms'] / 1000.0,
        bayer_pattern=params['bayer_pattern'],
        wb_config=params['wb_config'],
        wb_mode=params['wb_config']['mode'],
        scheduled_capture_mode=params['scheduled_capture_mode'],
        scheduled_start_time=params['scheduled_start_time'],
        scheduled_end_time=params['scheduled_end_time'],
        scheduled_window_interval=params['scheduled_window_interval'],
        camera_name=params['clean_camera_name'],
        camera_serial=params['camera_serial'],
    )
    cam.schedule_gate = gate
    cam.target_brightness = params['target_brightness']
    cam.set_capture_interval(params['capture_interval'])
    cam.use_raw16 = params['use_raw16']
    cam.auto_recovery_enabled = params['auto_recovery']
    cam.on_error_callback = on_error
    cam.on_calibration_callback = on_calibration
    return cam


def build_alpaca_session(params: dict, gate, on_error, on_calibration):
    """An ``AlpacaCamera`` for ``params`` (``params['alpaca']`` from ``alpaca_settings``)."""
    from .alpaca.alpaca_camera import AlpacaCamera
    cam = AlpacaCamera(
        alpaca_settings=params['alpaca'],
        exposure_sec=params['exposure_ms'] / 1000.0,
        gain=params['gain'],
        white_balance_r=params['wb_r'],
        white_balance_b=params['wb_b'],
        offset=params['offset'],
        flip=params['flip'],
        auto_exposure=params['auto_exposure'],
        max_exposure_sec=params['max_exposure_ms'] / 1000.0,
        bayer_pattern=params['bayer_pattern'],
        wb_config=params['wb_config'],
        wb_mode=params['wb_config']['mode'],
        scheduled_capture_mode=params['scheduled_capture_mode'],
        scheduled_start_time=params['scheduled_start_time'],
        scheduled_end_time=params['scheduled_end_time'],
        scheduled_window_interval=params['scheduled_window_interval'],
        camera_name=params['clean_camera_name'] or None,
        camera_serial=params['camera_serial'] or None,
    )
    cam.schedule_gate = gate
    cam.target_brightness = params['target_brightness']
    cam.set_capture_interval(params['capture_interval'])
    cam.auto_recovery_enabled = params['auto_recovery']
    cam.on_error_callback = on_error
    cam.on_calibration_callback = on_calibration
    return cam


def build_session(params: dict, gate, camera_index: int, on_error, on_calibration):
    """The session for ``params['camera_source']`` (absent reads as ZWO)."""
    if params.get('camera_source') == 'alpaca':
        return build_alpaca_session(params, gate, on_error, on_calibration)
    return build_zwo_session(params, gate, camera_index, on_error, on_calibration)
