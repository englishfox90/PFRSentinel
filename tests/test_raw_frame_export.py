"""Tests for services/raw_frame_export.py — Bayer FITS, unprocessed PNG, metadata JSON."""
import json
import os

import numpy as np
import pytest
from PIL import Image

from services.raw_frame_export import bayer_array, export_raw_frame, json_safe_metadata

astropy_fits = pytest.importorskip("astropy.io.fits")

W, H = 16, 12


def _camera_metadata(bit_depth=8):
    dtype = np.uint16 if bit_depth == 16 else np.uint8
    mosaic = (np.arange(W * H, dtype=np.uint32) % (1 << bit_depth)).astype(dtype).reshape(H, W)
    return mosaic, {
        'CAMERA': 'ZWO ASI676MC',
        'EXPOSURE': '30.00s',
        'GAIN': '180',
        'TEMP': '30.1',
        'DATETIME': '2026-09-05 21:24:42',
        'BAYER_PATTERN': 'BGGR',
        'IMAGE_BIT_DEPTH': bit_depth,
        'CAMERA_BIT_DEPTH': 12,
        'P75': '3.0',
        'RAW_BAYER': mosaic.tobytes(),
        'RAW_GEOMETRY': (W, H, bit_depth, 'BGGR'),
        'WB_CONFIG': {'mode': 'manual', 'wb_r': 75, 'wb_b': 99},
        'RAW_RGB_NO_WB': np.zeros((H, W, 3), dtype=np.uint8),
        'RAW_RGB_16BIT': None,
    }


class TestBayerArray:
    @pytest.mark.parametrize("bit_depth", [8, 16])
    def test_roundtrips_geometry_and_dtype(self, bit_depth):
        mosaic, meta = _camera_metadata(bit_depth)
        out = bayer_array(meta)
        assert out.shape == (H, W) and out.dtype == mosaic.dtype
        assert np.array_equal(out, mosaic)

    def test_none_without_bayer(self):
        assert bayer_array({'CAMERA': 'x'}) is None
        assert bayer_array(None) is None


class TestJsonSafeMetadata:
    def test_drops_arrays_and_bytes_keeps_scalars(self):
        _mosaic, meta = _camera_metadata()
        safe = json_safe_metadata(meta)
        assert 'RAW_BAYER' not in safe and 'RAW_RGB_NO_WB' not in safe
        assert safe['GAIN'] == '180'
        assert safe['RAW_GEOMETRY'] == [W, H, 8, 'BGGR']
        assert safe['WB_CONFIG'] == {'mode': 'manual', 'wb_r': 75, 'wb_b': 99}
        assert safe['RAW_RGB_16BIT'] is None
        json.dumps(safe)


class TestExportRawFrame:
    def test_camera_mode_writes_fits_png_and_json(self, tmp_path):
        mosaic, meta = _camera_metadata(16)
        result = export_raw_frame(str(tmp_path), meta, stem='frame')

        names = sorted(os.path.basename(p) for p in result['files'])
        assert names == ['frame_bayer.fits', 'frame_metadata.json', 'frame_unprocessed.png']
        assert result['notes'] == []

        with astropy_fits.open(tmp_path / 'frame_bayer.fits') as hdul:
            data = hdul[0].data
            header = hdul[0].header
        assert data.shape == (H, W)
        assert np.array_equal(data.astype(np.uint16), mosaic)
        assert header['BAYERPAT'] == 'BGGR'
        assert header['GAIN'] == '180'
        assert header['IMGBITS'] == 16

        png = Image.open(tmp_path / 'frame_unprocessed.png')
        assert png.size == (W, H) and png.mode == 'RGB'

        saved = json.loads((tmp_path / 'frame_metadata.json').read_text())
        assert saved['EXPOSURE'] == '30.00s' and 'RAW_BAYER' not in saved

    def test_watch_mode_uses_supplied_image_and_notes_missing_fits(self, tmp_path):
        img = Image.new('RGB', (8, 6), color=(10, 20, 30))
        result = export_raw_frame(str(tmp_path), {'FILENAME': 'a.fits'}, pil_image=img, stem='w')

        names = sorted(os.path.basename(p) for p in result['files'])
        assert names == ['w_metadata.json', 'w_unprocessed.png']
        assert any('FITS skipped' in n for n in result['notes'])

    def test_no_frame_at_all_still_writes_metadata(self, tmp_path):
        result = export_raw_frame(str(tmp_path), {}, stem='empty')
        assert [os.path.basename(p) for p in result['files']] == ['empty_metadata.json']
        assert len(result['notes']) == 2
