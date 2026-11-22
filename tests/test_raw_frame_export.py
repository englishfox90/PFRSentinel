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
        assert header['BAYERPAT'] == 'RGGB'  # physical tile; pipeline name is BGGR
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


class TestBayerHeaderIsPhysical:
    """BAYERPAT must name the physical tile so Siril / PixInsight debayer correctly."""

    SIZE = 16

    def _red_top_blue_bottom_rggb(self):
        """A physical RGGB mosaic (red at (0, 0)) of a red-top / blue-bottom scene."""
        mosaic = np.zeros((self.SIZE, self.SIZE), np.uint8)
        half = self.SIZE // 2
        mosaic[0:half:2, 0::2] = 200       # R sites, top half
        mosaic[half + 1::2, 1::2] = 200    # B sites, bottom half
        return mosaic

    def _metadata(self, mosaic, pipeline_name):
        return {
            'CAMERA': 'synthetic', 'BAYER_PATTERN': pipeline_name, 'IMAGE_BIT_DEPTH': 8,
            'RAW_BAYER': mosaic.tobytes(),
            'RAW_GEOMETRY': (self.SIZE, self.SIZE, 8, pipeline_name),
            'WB_CONFIG': None,
        }

    @staticmethod
    def _external_debayer(mosaic, bayerpat):
        """Superpixel debayer the way a FITS reader does: BAYERPAT is the tile at (0, 0)."""
        planes = {c: [] for c in 'RGB'}
        for i, colour in enumerate(bayerpat):
            planes[colour].append(mosaic[i // 2::2, i % 2::2].astype(float))
        return np.dstack([np.mean(planes[c], axis=0) for c in 'RGB'])

    def test_physical_rggb_exports_as_rggb_top_down(self, tmp_path):
        mosaic = self._red_top_blue_bottom_rggb()
        # The pipeline name that renders this sensor correctly in the app.
        export_raw_frame(str(tmp_path), self._metadata(mosaic, 'BGGR'), stem='f')

        with astropy_fits.open(tmp_path / 'f_bayer.fits') as hdul:
            header = hdul[0].header
            data = hdul[0].data
        assert header['BAYERPAT'] == 'RGGB'
        assert header['ROWORDER'] == 'TOP-DOWN'
        assert header['XBAYROFF'] == 0 and header['YBAYROFF'] == 0
        assert np.array_equal(data, mosaic)

        rgb = self._external_debayer(data, header['BAYERPAT'])
        top, bottom = rgb[:2].reshape(-1, 3).mean(0), rgb[-2:].reshape(-1, 3).mean(0)
        assert top[0] > 0 and top[1] == 0 and top[2] == 0
        assert bottom[2] > 0 and bottom[0] == 0 and bottom[1] == 0

    def test_in_app_debayer_unchanged(self, tmp_path):
        pytest.importorskip("cv2")
        mosaic = self._red_top_blue_bottom_rggb()
        export_raw_frame(str(tmp_path), self._metadata(mosaic, 'BGGR'), stem='f')
        png = np.asarray(Image.open(tmp_path / 'f_unprocessed.png')).astype(int)
        top, bottom = png[2:5, 4:12].reshape(-1, 3).mean(0), png[-5:-2, 4:12].reshape(-1, 3).mean(0)
        assert top[0] > 100 and top[2] < 10
        assert bottom[2] > 100 and bottom[0] < 10

    @pytest.mark.parametrize("pipeline_name,physical", [
        ('BGGR', 'RGGB'), ('RGGB', 'BGGR'), ('GRBG', 'GBRG'), ('GBRG', 'GRBG')])
    def test_every_pipeline_name_written_reversed(self, tmp_path, pipeline_name, physical):
        mosaic = self._red_top_blue_bottom_rggb()
        export_raw_frame(str(tmp_path), self._metadata(mosaic, pipeline_name), stem='f')
        with astropy_fits.open(tmp_path / 'f_bayer.fits') as hdul:
            assert hdul[0].header['BAYERPAT'] == physical

    def test_unknown_pattern_omits_bayerpat_but_keeps_roworder(self, tmp_path):
        mosaic = self._red_top_blue_bottom_rggb()
        meta = self._metadata(mosaic, 'XYZW')
        meta['RAW_GEOMETRY'] = (self.SIZE, self.SIZE, 8, 'BGGR')  # still rebuildable
        export_raw_frame(str(tmp_path), meta, stem='f')
        with astropy_fits.open(tmp_path / 'f_bayer.fits') as hdul:
            assert 'BAYERPAT' not in hdul[0].header
            assert hdul[0].header['ROWORDER'] == 'TOP-DOWN'

    def test_builtin_writer_carries_the_same_cards(self, tmp_path, monkeypatch):
        """The frozen app has no usable astropy; its writer must write the same header."""
        import services.fits_writer as fits_writer
        monkeypatch.setattr(fits_writer, 'astropy_fits', lambda: None)
        mosaic = self._red_top_blue_bottom_rggb()
        export_raw_frame(str(tmp_path), self._metadata(mosaic, 'BGGR'), stem='f')
        with astropy_fits.open(tmp_path / 'f_bayer.fits') as hdul:
            header = hdul[0].header
        assert header['BAYERPAT'] == 'RGGB' and header['ROWORDER'] == 'TOP-DOWN'
        assert header['XBAYROFF'] == 0 and header['YBAYROFF'] == 0
