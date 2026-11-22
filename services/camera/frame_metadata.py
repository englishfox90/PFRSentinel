"""The per-frame metadata dict every camera backend hands the processor.

Overlay tokens, the raw-frame cache (``frame_builder.CACHE_KEYS``), the dev-mode
exports and the all-sky / ML consumers all read these keys, so one builder
fills them for every backend and the key set cannot drift between ZWO and
Alpaca. Values are formatted exactly as the ZWO capture path always did.
"""
from __future__ import annotations

from datetime import datetime, timezone

FRAME_METADATA_KEYS = (
    'CAMERA', 'EXPOSURE', 'EXPOSURE_START_UTC', 'GAIN', 'TEMP', 'TEMPERATURE',
    'TEMP_C', 'TEMP_F', 'RES', 'CAPTURE AREA SIZE', 'FILENAME', 'SESSION',
    'DATETIME', 'BRIGHTNESS', 'MEAN', 'MEDIAN', 'MIN', 'MAX', 'STD_DEV', 'P25',
    'P75', 'P95', 'RAW_RGB_NO_WB', 'RAW_RGB_16BIT', 'RAW_BAYER', 'RAW_GEOMETRY',
    'WB_CONFIG', 'CAMERA_BIT_DEPTH', 'IMAGE_BIT_DEPTH', 'BAYER_PATTERN',
    'PIXEL_SIZE', 'ELEC_PER_ADU',
)

_NO_TEMPERATURE = {'display': "N/A", 'celsius_str': "N/A", 'fahrenheit_str': "N/A"}


def temperature_fields(celsius: float | None) -> dict:
    """Display strings for a sensor temperature; ``None`` reads "N/A" throughout."""
    if celsius is None:
        return dict(_NO_TEMPERATURE)
    fahrenheit = (celsius * 9 / 5) + 32
    return {
        'display': f"{celsius:.1f} C",
        'celsius_str': f"{celsius:.1f}°C",
        'fahrenheit_str': f"{fahrenheit:.1f}°F",
    }


def build_frame_metadata(*, camera_name, exposure_seconds, gain, start_time, temp,
                         width, height, stats, arrays, raw_bytes, bit_depth,
                         bayer_pattern, wb_config, camera_bit_depth, pixel_size,
                         elec_per_adu) -> dict:
    """One frame's metadata, keys in ``FRAME_METADATA_KEYS`` order.

    ``start_time`` is the exposure start as a Unix timestamp, ``temp`` a
    :func:`temperature_fields` dict, ``stats`` / ``arrays`` what
    ``frame_builder.build_frame`` returned for ``raw_bytes``.
    """
    return {
        'CAMERA': camera_name,
        'EXPOSURE': f"{exposure_seconds}s",
        'EXPOSURE_START_UTC': datetime.fromtimestamp(start_time, timezone.utc).isoformat(),
        'GAIN': str(gain),
        'TEMP': temp['display'],
        'TEMPERATURE': temp['display'],
        'TEMP_C': temp['celsius_str'],
        'TEMP_F': temp['fahrenheit_str'],
        'RES': f"{width}x{height}",
        'CAPTURE AREA SIZE': f"{width} * {height}",
        'FILENAME': f"capture_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png",
        'SESSION': datetime.now().strftime('%Y-%m-%d'),
        'DATETIME': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'BRIGHTNESS': f"{stats['mean']:.1f}",
        'MEAN': f"{stats['mean']:.1f}",
        'MEDIAN': f"{stats['median']:.1f}",
        'MIN': f"{stats['min']}",
        'MAX': f"{stats['max']}",
        'STD_DEV': f"{stats['std_dev']:.2f}",
        'P25': f"{stats['p25']:.1f}",
        'P75': f"{stats['p75']:.1f}",
        'P95': f"{stats['p95']:.1f}",
        'RAW_RGB_NO_WB': arrays['RAW_RGB_NO_WB'],
        'RAW_RGB_16BIT': arrays['RAW_RGB_16BIT'],
        # Rebuild keys: the SDK bytes (referenced, not copied) plus what
        # build_frame needs to decode them again. They let the reprocess /
        # all-sky cache hold ~25 MB instead of the decoded frame's ~125 MB.
        'RAW_BAYER': raw_bytes,
        'RAW_GEOMETRY': (width, height, bit_depth, bayer_pattern),
        'WB_CONFIG': dict(wb_config or {}),
        'CAMERA_BIT_DEPTH': camera_bit_depth,
        'IMAGE_BIT_DEPTH': bit_depth,
        'BAYER_PATTERN': bayer_pattern,
        'PIXEL_SIZE': pixel_size,
        'ELEC_PER_ADU': elec_per_adu,
    }
