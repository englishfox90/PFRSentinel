"""What the diagnostics bundle records about a live Alpaca session (issue #43).

Pure reads of the session's connection and last download; never raises and
never makes a request, so the export works while the camera is unreachable.
The host and UniqueID are left out: ``diagnostics_bundle`` masks them in the
config, and the summary must not put them back.
"""


def _transfer_rate_mb_s(n_bytes, seconds):
    try:
        n_bytes, seconds = float(n_bytes or 0), float(seconds or 0)
    except (TypeError, ValueError):
        return None
    if n_bytes <= 0 or seconds <= 0:
        return None
    return round(n_bytes / seconds / 1e6, 2)


def session_diagnostics(session) -> dict:
    """Driver, sensor and transfer facts for the support summary."""
    info = getattr(getattr(session, '_connection', None), 'static_info', None)
    last_s = getattr(session, 'last_download_s', None)
    n_bytes = getattr(session, 'last_download_bytes', 0)
    image_format = getattr(session, 'last_image_format', None)
    out = {
        'backend': getattr(session, 'backend', 'alpaca'),
        'connected': info is not None,
        'bayer_pattern': getattr(session, 'bayer_pattern', None),
        'image_format': image_format,
        'imagebytes': (image_format == 'imagebytes') if image_format else None,
        'last_download_s': round(float(last_s), 3) if isinstance(last_s, (int, float)) else None,
        'last_download_bytes': int(n_bytes or 0),
        'download_mb_s': _transfer_rate_mb_s(n_bytes, last_s),
    }
    if info is not None:
        out.update({
            'driver_info': info.driver_info,
            'driver_version': info.driver_version,
            'interface_version': info.interface_version,
            'sensor_type': info.sensor_type,
            'sensor_name': info.sensor_name,
            'max_adu': info.max_adu,
            'sensor_bit_depth': info.sensor_bit_depth,
            'physical_bayer_pattern': info.physical_bayer_pattern,
            'width': info.width,
            'height': info.height,
        })
    return out
