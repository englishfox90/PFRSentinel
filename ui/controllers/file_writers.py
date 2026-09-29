"""
File Writers for Dev Mode

Handles saving FITS and JSON files for dev_mode debugging.
Extracted from dev_mode_utils.py for modularity.
"""
import os
import json
import numpy as np

from services.fits_writer import write_fits as _write_fits
from services.logger import app_logger


def save_raw_fits(path: str, raw_array: np.ndarray, image_bit_depth: int, header_kv: dict):
    """
    Save raw image data as FITS, preserving true dynamic range.

    Args:
        path: Output file path (.fits)
        raw_array: Raw image data (uint8 or uint16)
        image_bit_depth: Original image bit depth (8 or 16)
        header_kv: Dictionary of FITS header keywords
    """
    if image_bit_depth == 16:
        data = raw_array.astype(np.uint16)
        header_kv['SCALED'] = (False, 'Data saved without scaling')
        header_kv['COMMENT'] = 'RAW16 data - true sensor values preserved'
    else:
        data = (raw_array.astype(np.uint16) * 257)
        header_kv['SCALED'] = (True, 'RAW8 scaled to 16-bit (x257)')
        header_kv['COMMENT'] = 'RAW8 data scaled to 16-bit for FITS'

    if data.ndim == 3 and data.shape[2] == 3:
        data = np.transpose(data, (2, 0, 1))
        header_kv['COLORTYP'] = ('RGB', 'Color type of image')
    elif data.ndim == 2:
        header_kv['COLORTYP'] = ('MONO', 'Grayscale/mono image')

    write_fits(path, data, header_kv)
    app_logger.info(
        f"DEV MODE: ✓ Saved raw FITS to {os.path.basename(path)} "
        f"(shape: {data.shape}, scaled={image_bit_depth != 16})"
    )


def save_luminance_fits(path: str, lum: np.ndarray, header_kv: dict):
    """
    Save luminance array as FITS file.

    Args:
        path: Output file path (.fits)
        lum: Luminance array (float32, 0-1 range)
        header_kv: Base header keywords to include
    """
    lum_header = header_kv.copy()
    lum_header['COMMENT'] = 'Grayscale luminance (0.299R + 0.587G + 0.114B)'
    lum_header['DATATYPE'] = ('float32', 'Luminance in 0..1 range')

    write_fits(path, lum.astype(np.float32), lum_header)
    app_logger.info(
        f"DEV MODE: ✓ Saved luminance FITS to {os.path.basename(path)} (shape: {lum.shape})"
    )


def write_fits(path: str, data: np.ndarray, header_kv: dict):
    """
    Write data to FITS file with header keywords.

    Goes through ``services.fits_writer`` so the frozen app, which ships
    without astropy, writes the same file the dev environment does.

    Args:
        path: Output file path
        data: Image data array
        header_kv: Dictionary of header keywords. Values can be:
                   - Simple values (str, int, float)
                   - Tuples of (value, comment)
    """
    _write_fits(path, data, header_kv)


def write_json(path: str, payload: dict):
    """
    Write dictionary to JSON file with pretty formatting.

    Args:
        path: Output file path (.json)
        payload: Dictionary to serialize
    """
    with open(path, 'w') as f:
        json.dump(payload, f, indent=2, default=str)
