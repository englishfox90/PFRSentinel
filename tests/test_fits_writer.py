"""services/fits_writer — the one FITS writer, with and without astropy.

The frozen app ships without astropy, and inside the bundle
``from astropy.io import fits`` can still succeed as a hollow module with no
PrimaryHDU on it (the 2026-09-29 dev build logged exactly that on every
frame). The writer must detect that, not the import, and the built-in
fallback must produce files real astropy reads back identically.
"""
import sys
import types

import numpy as np
import pytest

from services import fits_writer
from services.fits_writer import astropy_fits, write_fits

astropy_io_fits = pytest.importorskip("astropy.io.fits")


@pytest.fixture
def builtin_only(monkeypatch):
    """Force the built-in writer, as the frozen app sees it."""
    monkeypatch.setattr(fits_writer, 'astropy_fits', lambda: None)


def _read(path):
    with astropy_io_fits.open(path) as hdul:
        return hdul[0].data.copy(), hdul[0].header.copy()


ARRAYS = {
    'uint8_2d': np.arange(12, dtype=np.uint8).reshape(3, 4),
    'uint16_2d': np.array([[0, 1, 32768, 65535], [4095, 4096, 65520, 2]], dtype=np.uint16),
    'uint16_rgb': np.arange(2 * 3 * 4, dtype=np.uint16).reshape(3, 2, 4) * 1000,
    'float32_2d': np.linspace(0.0, 1.0, 12, dtype=np.float32).reshape(3, 4),
    'int16_2d': np.array([[-32768, 32767], [0, -1]], dtype=np.int16),
    'float64_2d': np.array([[1e-9, 2.5], [-3.0, 1e6]]),
}


class TestBuiltinWriter:
    @pytest.mark.parametrize("name", sorted(ARRAYS))
    def test_astropy_reads_back_every_array_type_exactly(self, tmp_path, builtin_only, name):
        data = ARRAYS[name]
        path = tmp_path / f'{name}.fits'
        write_fits(str(path), data, {'CAMERA': 'ZWO ASI676MC'})

        out, header = _read(path)
        assert out.shape == data.shape
        assert (out.dtype.kind, out.dtype.itemsize) == (data.dtype.kind, data.dtype.itemsize)
        assert np.array_equal(out, data)
        assert header['NAXIS'] == data.ndim
        assert header['CAMERA'] == 'ZWO ASI676MC'

    def test_header_cards_round_trip_with_comments(self, tmp_path, builtin_only):
        header = {
            'EXPOSURE': '13.00s',
            'GAIN': 300,
            'EGAIN': (0.25, 'Electrons per ADU'),
            'SCALED': (False, 'Data saved without scaling'),
            'PIXSIZE': (2.0, 'Pixel size in microns'),
            'DATE-OBS': '2026-09-29T00:12:12',
            'QUOTE': "it's",
            'COMMENT': 'RAW16 data - true sensor values preserved',
        }
        path = tmp_path / 'h.fits'
        write_fits(str(path), ARRAYS['uint16_2d'], header)

        _, out = _read(path)
        assert out['EXPOSURE'] == '13.00s'
        assert out['GAIN'] == 300
        assert out['EGAIN'] == 0.25 and out.comments['EGAIN'] == 'Electrons per ADU'
        assert out['SCALED'] is False
        assert out['PIXSIZE'] == 2.0
        assert out['DATE-OBS'] == '2026-09-29T00:12:12'
        assert out['QUOTE'] == "it's"
        assert 'RAW16 data - true sensor values preserved' in str(out['COMMENT'])

    def test_file_is_whole_2880_byte_blocks(self, tmp_path, builtin_only):
        path = tmp_path / 'b.fits'
        write_fits(str(path), ARRAYS['uint16_rgb'], {})
        assert path.stat().st_size % 2880 == 0

    def test_structural_keywords_from_the_caller_are_ignored(self, tmp_path, builtin_only):
        path = tmp_path / 's.fits'
        write_fits(str(path), ARRAYS['uint8_2d'], {'NAXIS': 7, 'BITPIX': -64, 'SIMPLE': False})
        out, header = _read(path)
        assert header['NAXIS'] == 2 and header['BITPIX'] == 8
        assert np.array_equal(out, ARRAYS['uint8_2d'])

    def test_overlong_string_and_keyword_are_truncated_not_fatal(self, tmp_path, builtin_only):
        path = tmp_path / 't.fits'
        write_fits(str(path), ARRAYS['uint8_2d'],
                   {'AVERYLONGKEYWORD': 'x' * 200, 'UNICODE': 'µ-degrees'})
        _, header = _read(path)
        assert header['AVERYLON'] == 'x' * 68
        assert header['UNICODE'] == '?-degrees'

    def test_an_unsupported_dtype_is_stored_as_float32(self, tmp_path, builtin_only):
        data = np.arange(6, dtype=np.int64).reshape(2, 3)
        path = tmp_path / 'i64.fits'
        write_fits(str(path), data, {})
        out, _ = _read(path)
        assert out.dtype.kind == 'f' and out.dtype.itemsize == 4 and np.array_equal(out, data)

    def test_one_dimensional_data_is_refused(self, tmp_path, builtin_only):
        with pytest.raises(ValueError):
            write_fits(str(tmp_path / 'x.fits'), np.zeros(4, dtype=np.uint8), {})


class TestParityWithAstropy:
    @pytest.mark.parametrize("name", ['uint16_2d', 'uint16_rgb', 'float32_2d'])
    def test_both_paths_give_the_same_data_and_cards(self, tmp_path, monkeypatch, name):
        data = ARRAYS[name]
        header = {'CAMERA': 'x', 'GAIN': 300, 'EGAIN': (0.25, 'e/ADU'), 'COMMENT': 'c'}
        write_fits(str(tmp_path / 'astropy.fits'), data, dict(header))
        monkeypatch.setattr(fits_writer, 'astropy_fits', lambda: None)
        write_fits(str(tmp_path / 'builtin.fits'), data, dict(header))

        a_data, a_hdr = _read(tmp_path / 'astropy.fits')
        b_data, b_hdr = _read(tmp_path / 'builtin.fits')
        assert np.array_equal(a_data, b_data) and a_data.dtype == b_data.dtype
        for key in ('BITPIX', 'NAXIS', 'CAMERA', 'GAIN', 'EGAIN'):
            assert a_hdr[key] == b_hdr[key], key
        assert a_hdr.get('BZERO') == b_hdr.get('BZERO')


class TestHollowAstropyModule:
    """The frozen app's failure: the import works, the class is missing."""

    @pytest.fixture
    def hollow_astropy(self, monkeypatch):
        astropy = types.ModuleType('astropy')
        io = types.ModuleType('astropy.io')
        fits = types.ModuleType('astropy.io.fits')
        astropy.io = io
        io.fits = fits
        for name, module in (('astropy', astropy), ('astropy.io', io),
                             ('astropy.io.fits', fits)):
            monkeypatch.setitem(sys.modules, name, module)
        return fits

    def test_astropy_fits_reports_it_unusable(self, hollow_astropy):
        assert astropy_fits() is None

    def test_write_fits_still_writes_the_file(self, tmp_path, hollow_astropy):
        path = tmp_path / 'dev.fits'
        write_fits(str(path), ARRAYS['uint16_rgb'], {'CAMERA': 'x'})
        assert path.stat().st_size % 2880 == 0 and path.stat().st_size > 2880

    def test_dev_mode_raw_and_luminance_writers_survive(self, tmp_path, hollow_astropy):
        from ui.controllers.file_writers import save_luminance_fits, save_raw_fits

        raw = np.random.default_rng(0).integers(0, 65520, size=(4, 6, 3), dtype=np.uint16)
        save_raw_fits(str(tmp_path / 'raw.fits'), raw, 16, {'CAMERA': 'x'})
        save_luminance_fits(str(tmp_path / 'lum.fits'), np.zeros((4, 6), np.float32),
                            {'CAMERA': 'x'})
        assert (tmp_path / 'raw.fits').exists() and (tmp_path / 'lum.fits').exists()

    def test_real_astropy_is_used_when_whole(self):
        assert astropy_fits() is astropy_io_fits


class TestDevModeWritersReadBack:
    def test_raw_rgb_16bit_keeps_channels_first_and_values(self, tmp_path, builtin_only):
        from ui.controllers.file_writers import save_raw_fits

        raw = np.random.default_rng(1).integers(0, 65520, size=(5, 7, 3), dtype=np.uint16)
        save_raw_fits(str(tmp_path / 'raw.fits'), raw, 16, {'CAMERA': 'x'})
        out, header = _read(tmp_path / 'raw.fits')
        assert out.shape == (3, 5, 7)
        assert np.array_equal(out, np.transpose(raw, (2, 0, 1)))
        assert header['SCALED'] is False and header['COLORTYP'] == 'RGB'

    def test_raw_8bit_is_scaled_by_257(self, tmp_path, builtin_only):
        from ui.controllers.file_writers import save_raw_fits

        raw = np.array([[0, 255], [128, 1]], dtype=np.uint8)
        save_raw_fits(str(tmp_path / 'raw8.fits'), raw, 8, {})
        out, header = _read(tmp_path / 'raw8.fits')
        assert np.array_equal(out, raw.astype(np.uint16) * 257)
        assert header['SCALED'] is True and header['COLORTYP'] == 'MONO'
