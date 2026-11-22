"""debayer_raw_image MONO / RGB passthrough; Bayer patterns and the BGGR default unchanged;
raw-frame export of mono and RGB frames."""
import cv2
import numpy as np
import pytest

from services.camera.camera_utils import debayer_raw_image
from services.camera.frame_builder import build_frame
from services.raw_frame_export import bayer_array, export_raw_frame

W, H = 6, 4


def _plane(dtype=np.uint8):
    top = np.iinfo(dtype).max
    return (np.arange(W * H).reshape(H, W) * (top // (W * H))).astype(dtype)


def test_mono_8bit_copies_grey_to_every_channel():
    plane = _plane()
    rgb, raw16 = debayer_raw_image(plane.tobytes(), W, H, 'MONO', bit_depth=8)
    assert rgb.shape == (H, W, 3) and rgb.dtype == np.uint8 and raw16 is None
    for c in range(3):
        assert np.array_equal(rgb[..., c], plane)


def test_mono_16bit_returns_raw16_and_the_high_byte():
    plane = _plane(np.uint16)
    rgb, raw16 = debayer_raw_image(plane.astype('<u2').tobytes(), W, H, 'MONO', bit_depth=16,
                                   return_raw16=True)
    assert raw16.dtype == np.uint16 and np.array_equal(raw16[..., 1], plane)
    assert np.array_equal(rgb[..., 2], (plane >> 8).astype(np.uint8))


@pytest.mark.parametrize("bit_depth", [8, 16])
def test_rgb_planes_pass_through_unchanged(bit_depth):
    dtype = np.uint16 if bit_depth == 16 else np.uint8
    rgb_in = np.stack([_plane(dtype), _plane(dtype)[::-1], np.full((H, W), 7, dtype)], axis=2)
    rgb, raw16 = debayer_raw_image(rgb_in.astype(dtype).tobytes(), W, H, 'RGB',
                                   bit_depth=bit_depth, return_raw16=True)
    if bit_depth == 16:
        assert np.array_equal(raw16, rgb_in)
        assert np.array_equal(rgb, (rgb_in >> 8).astype(np.uint8))
    else:
        assert raw16 is None and np.array_equal(rgb, rgb_in)
    assert rgb.flags.writeable


def test_dst_buffers_are_honoured_for_mono():
    dst = np.zeros((H, W, 3), np.uint8)
    rgb, _ = debayer_raw_image(_plane().tobytes(), W, H, 'MONO', dst_rgb8=dst)
    assert rgb is dst and dst.any()


@pytest.mark.parametrize("pattern,code", [
    ('RGGB', cv2.COLOR_BayerRG2RGB), ('BGGR', cv2.COLOR_BayerBG2RGB),
    ('GRBG', cv2.COLOR_BayerGR2RGB), ('GBRG', cv2.COLOR_BayerGB2RGB),
    ('bogus', cv2.COLOR_BayerBG2RGB),
])
def test_bayer_patterns_and_the_bggr_default_are_unchanged(pattern, code):
    plane = _plane()
    rgb, _ = debayer_raw_image(plane.tobytes(), W, H, pattern, bit_depth=8)
    assert np.array_equal(rgb, cv2.cvtColor(plane, code))


def test_build_frame_and_rebuild_carry_mono_and_rgb():
    img, arrays, stats = build_frame(_plane().tobytes(), W, H, 8, 'MONO', {'mode': 'manual'})
    assert img.size == (W, H) and img.mode == 'RGB'
    rgb_bytes = np.zeros((H, W, 3), np.uint8).tobytes()
    img, _arrays, _stats = build_frame(rgb_bytes, W, H, 8, 'RGB', None)
    assert img.size == (W, H)


def _cached(raw_bytes, pattern, bit_depth=8):
    return {'RAW_BAYER': raw_bytes, 'RAW_GEOMETRY': (W, H, bit_depth, pattern),
            'WB_CONFIG': {}, 'BAYER_PATTERN': pattern, 'CAMERA': 'Alpaca'}


def test_rgb_frame_exports_png_without_a_bayer_fits(tmp_path):
    meta = _cached(np.zeros((H, W, 3), np.uint8).tobytes(), 'RGB')
    assert bayer_array(meta) is None
    out = export_raw_frame(str(tmp_path), meta, stem='f')
    names = sorted(p.rsplit('/', 1)[-1].rsplit('\\', 1)[-1] for p in out['files'])
    assert names == ['f_metadata.json', 'f_unprocessed.png']
    assert any('RGB planes' in n for n in out['notes'])


def test_mono_frame_exports_fits_without_bayerpat(tmp_path, monkeypatch):
    from services import raw_frame_export
    written = {}
    monkeypatch.setattr(raw_frame_export, 'write_fits',
                        lambda path, data, header: written.update(data=data, header=header))
    meta = _cached(_plane().tobytes(), 'MONO')
    out = export_raw_frame(str(tmp_path), meta, stem='m')
    assert written['data'].shape == (H, W)
    assert 'BAYERPAT' not in written['header']
    assert 'monochrome' in written['header']['COMMENT']
    assert any(p.endswith('m_unprocessed.png') for p in out['files'])
