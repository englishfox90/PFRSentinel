"""Raw frame export — the unprocessed frame as files a human can inspect.

Given the main window's cached frame (camera mode: the SDK Bayer bytes plus
geometry; watch mode: the clean PIL image) this writes:

- ``<stem>_bayer.fits``      the sensor mosaic, unscaled, with BAYERPAT — only
                             in camera mode, needs astropy
- ``<stem>_unprocessed.png`` debayered + white-balanced, before stretch,
                             sharpening, overlays or resize
- ``<stem>_metadata.json``   the frame's scalar metadata (exposure, gain,
                             stats, WB config)

Production-safe: unlike dev-mode saving this is not gated on the build flag,
because its whole purpose is letting a user hand the developer what the
camera actually saw.
"""
import json
import os
from datetime import datetime

import numpy as np

from .camera.frame_builder import is_rebuildable, rebuild_frame
from .logger import app_logger

# metadata key -> FITS keyword (8 chars max)
FITS_HEADER_KEYS = (
    ('CAMERA', 'CAMERA'),
    ('EXPOSURE', 'EXPOSURE'),
    ('GAIN', 'GAIN'),
    ('TEMP', 'CCDTEMP'),
    ('DATETIME', 'DATE-OBS'),
    ('BAYER_PATTERN', 'BAYERPAT'),
    ('IMAGE_BIT_DEPTH', 'IMGBITS'),
    ('CAMERA_BIT_DEPTH', 'ADCBITS'),
)


def bayer_array(metadata):
    """The cached sensor mosaic as a 2-D array, or None when not rebuildable."""
    if not is_rebuildable(metadata):
        return None
    width, height, bit_depth, _pattern = metadata['RAW_GEOMETRY']
    dtype = np.uint16 if bit_depth == 16 else np.uint8
    return np.frombuffer(metadata['RAW_BAYER'], dtype=dtype).reshape((height, width))


def json_safe_metadata(metadata) -> dict:
    """Scalar-only view of ``metadata`` (drops arrays, bytes, and callables)."""
    return {k: v for k, v in ((k, _json_safe(v)) for k, v in (metadata or {}).items())
            if v is not _DROP}


_DROP = object()


def _json_safe(value):
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, (list, tuple)):
        items = [_json_safe(v) for v in value]
        return [v for v in items if v is not _DROP]
    if isinstance(value, dict):
        safe = ((str(k), _json_safe(v)) for k, v in value.items())
        return {k: v for k, v in safe if v is not _DROP}
    return _DROP


def export_raw_frame(dest_dir, metadata, pil_image=None, stem=None) -> dict:
    """Write the raw-frame files into ``dest_dir``.

    Returns ``{'files': [paths], 'notes': [str]}``. Notes explain anything
    that was skipped so the bundle summary can say why a file is absent.
    """
    os.makedirs(dest_dir, exist_ok=True)
    metadata = metadata or {}
    stem = stem or datetime.now().strftime('raw_%Y%m%d_%H%M%S')
    files, notes = [], []

    mosaic = bayer_array(metadata)
    if mosaic is not None:
        fits_path = os.path.join(dest_dir, f'{stem}_bayer.fits')
        if _write_bayer_fits(fits_path, mosaic, metadata):
            files.append(fits_path)
        else:
            npy_path = os.path.join(dest_dir, f'{stem}_bayer.npy')
            np.save(npy_path, mosaic)
            files.append(npy_path)
            notes.append('astropy unavailable: Bayer mosaic saved as .npy instead of FITS')
    else:
        notes.append('No Bayer data cached (watch mode or no frame yet): FITS skipped')

    if pil_image is None and mosaic is not None:
        # Index rather than unpack: the rebuilt metadata carries the RAW16
        # companion array, and binding it would keep it alive through the save.
        pil_image = rebuild_frame(metadata)[0]
    if pil_image is not None:
        png_path = os.path.join(dest_dir, f'{stem}_unprocessed.png')
        pil_image.save(png_path, 'PNG')
        files.append(png_path)
    else:
        notes.append('No image available for the unprocessed PNG')

    meta_path = os.path.join(dest_dir, f'{stem}_metadata.json')
    with open(meta_path, 'w', encoding='utf-8') as f:
        json.dump(json_safe_metadata(metadata), f, indent=2, default=str)
    files.append(meta_path)

    app_logger.info(f"Raw frame exported: {len(files)} file(s) under {dest_dir}")
    return {'files': files, 'notes': notes}


def _write_bayer_fits(path, mosaic, metadata) -> bool:
    try:
        from astropy.io import fits
    except ImportError:
        return False
    hdu = fits.PrimaryHDU(mosaic)
    for meta_key, fits_key in FITS_HEADER_KEYS:
        value = metadata.get(meta_key)
        if value is not None:
            hdu.header[fits_key] = str(value) if not isinstance(value, (int, float)) else value
    hdu.header['COMMENT'] = 'PFR Sentinel raw sensor mosaic - unscaled, not debayered'
    hdu.writeto(path, overwrite=True)
    return True
