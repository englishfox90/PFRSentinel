"""Everything a capture session needs from config, read in one place.

Runs on the Qt main thread before the start worker is spawned, so the blocking
SDK work on that worker never touches ``Config``. The ZWO half moved verbatim
out of ``CameraControllerQt.start_capture``; an Alpaca source reads its identity
from ``alpaca_camera`` and its profile under ``alpaca:<UniqueID>``.
"""
from ..logger import app_logger
from .camera_source import ALPACA, ZWO, alpaca_profile_key, alpaca_settings, camera_source


def read_capture_params(config, auto_recovery: bool) -> dict:
    """The capture parameters for the saved camera, from its profile and config."""
    if camera_source(config) == ALPACA:
        return _read_alpaca_params(config, auto_recovery)
    sdk_path = config.get('zwo_sdk_path', '')
    saved_camera_index = config.get('zwo_selected_camera', 0)
    camera_name = config.get('zwo_selected_camera_name', 'Unknown')
    camera_serial = config.get('zwo_selected_camera_serial', '')

    app_logger.info(f"Starting capture — saved camera: '{camera_name}' at index {saved_camera_index}")

    clean_camera_name = camera_name
    if '(Index:' in camera_name:
        clean_camera_name = camera_name.split('(Index:')[0].strip()

    profile = config.get_camera_profile(clean_camera_name, camera_serial)
    app_logger.info(f"Loading settings from camera profile: {clean_camera_name}")
    app_logger.debug(f"Profile contents: {profile}")

    params = {
        'camera_source': ZWO,
        'sdk_path': sdk_path,
        'saved_camera_index': saved_camera_index,
        'camera_name': camera_name,
        'camera_serial': camera_serial,
        'clean_camera_name': clean_camera_name,
    }
    params.update(_profile_params(config, profile, auto_recovery))
    return params


def _read_alpaca_params(config, auto_recovery: bool) -> dict:
    """The Alpaca variant: identity from ``alpaca_camera``, profile by its key."""
    from ..config import DEFAULT_CAMERA_PROFILE
    settings = alpaca_settings(config)
    camera_name = settings['name']
    camera_serial = alpaca_profile_key(settings)
    app_logger.info(f"Starting capture — Alpaca camera '{camera_name or '?'}' at "
                    f"{settings['host']}:{settings['port']} #{settings['device_number']}")
    profile = None
    if camera_name or camera_serial:
        profile = config.get_camera_profile(camera_name, camera_serial)
    params = {
        'camera_source': ALPACA,
        'alpaca': settings,
        'sdk_path': '',
        'saved_camera_index': 0,
        'camera_name': camera_name,
        'camera_serial': camera_serial,
        'clean_camera_name': camera_name,
    }
    params.update(_profile_params(config, profile or dict(DEFAULT_CAMERA_PROFILE),
                                  auto_recovery))
    return params


def _profile_params(config, profile, auto_recovery: bool) -> dict:
    from ..config import DEFAULT_CAMERA_PROFILE
    wb_config = dict(config.get('white_balance', {}))
    wb_config.setdefault('mode', 'asi_auto')
    dev_mode = config.get('dev_mode', {})

    return {
        'exposure_ms': profile.get('exposure_ms', DEFAULT_CAMERA_PROFILE['exposure_ms']),
        'gain': profile.get('gain', DEFAULT_CAMERA_PROFILE['gain']),
        'max_exposure_ms': profile.get('max_exposure_ms', DEFAULT_CAMERA_PROFILE['max_exposure_ms']),
        'target_brightness': profile.get('target_brightness', DEFAULT_CAMERA_PROFILE['target_brightness']),
        'wb_r': profile.get('wb_r', DEFAULT_CAMERA_PROFILE['wb_r']),
        'wb_b': profile.get('wb_b', DEFAULT_CAMERA_PROFILE['wb_b']),
        'offset': profile.get('offset', DEFAULT_CAMERA_PROFILE['offset']),
        'flip': profile.get('flip', DEFAULT_CAMERA_PROFILE['flip']),
        'bayer_pattern': profile.get('bayer_pattern', DEFAULT_CAMERA_PROFILE['bayer_pattern']),
        'auto_exposure': config.get('zwo_auto_exposure', False),
        'wb_config': wb_config,
        'capture_interval': config.get('zwo_interval', 5.0),
        'scheduled_capture_mode': config.get('scheduled_capture_mode', 'always'),
        'scheduled_start_time': config.get('scheduled_start_time', '17:00'),
        'scheduled_end_time': config.get('scheduled_end_time', '09:00'),
        'scheduled_window_interval': config.get('scheduled_window_interval', 5.0),
        'use_raw16': dev_mode.get('use_raw16', False),
        'auto_recovery': auto_recovery,
    }
