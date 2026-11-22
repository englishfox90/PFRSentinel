"""alpaca_sensor — Bayer phase from SensorType/BayerOffset/subframe, flips, MaxADU normalisation."""
import numpy as np
import pytest

from services.camera.alpaca import alpaca_sensor as sensor
from tests.alpaca_fake import load_sim_package

_sim_scene = load_sim_package().scene


@pytest.fixture(autouse=True)
def _fresh_warnings():
    sensor._reset_warnings_for_tests()
    yield
    sensor._reset_warnings_for_tests()


@pytest.mark.parametrize("ox,oy,expected", [
    (0, 0, "RGGB"), (1, 0, "GRBG"), (0, 1, "GBRG"), (1, 1, "BGGR"),
])
def test_four_offsets_physical(ox, oy, expected):
    assert sensor.physical_bayer_pattern(2, ox, oy) == expected
    assert sensor.bayer_pattern_for(2, ox, oy) == expected[::-1]


@pytest.mark.parametrize("ox,oy", [(0, 0), (1, 0), (0, 1), (1, 1)])
def test_pipeline_name_debayers_the_right_colours(ox, oy):
    from services.camera.camera_utils import debayer_raw_image
    scene = _sim_scene.SceneGenerator(16, 12, "red_top_blue_bottom")
    mosaic = scene.render(exposure_s=1, gain=0, offset=0, max_adu=4095, sensor_type=2,
                          bayer_offset_x=ox, bayer_offset_y=oy)
    raw, depth = sensor.to_raw_bytes(mosaic, 4095)
    rgb, _ = debayer_raw_image(raw, 16, 12, sensor.bayer_pattern_for(2, ox, oy), depth)
    top, bottom = rgb[1:5, 2:-2].reshape(-1, 3).mean(0), rgb[7:-1, 2:-2].reshape(-1, 3).mean(0)
    assert top[0] > 4 * top[2] and bottom[2] > 4 * bottom[0]


def test_zwo_676mc_naming_matches_a_red_top_left_sensor():
    # The repo's "BGGR" for the ASI676MC is OpenCV's name for a red-at-(0,0) tile.
    assert sensor.pipeline_pattern_name("RGGB") == "BGGR"
    assert sensor.pipeline_pattern_name("GRBG") == "GBRG"
    assert sensor.pipeline_pattern_name("MONO") == "MONO"


@pytest.mark.parametrize("ox,oy", [(0, 0), (1, 0), (0, 1), (1, 1)])
@pytest.mark.parametrize("sx,sy", [(0, 0), (1, 0), (0, 1), (1, 1), (2, 3), (5, 4)])
def test_subframe_start_parity_shifts_the_phase(ox, oy, sx, sy):
    assert sensor.physical_bayer_pattern(2, ox, oy, sx, sy) == \
        sensor.physical_bayer_pattern(2, (ox + sx) % 2, (oy + sy) % 2)


@pytest.mark.parametrize("ox,oy", [(0, 0), (1, 0), (0, 1), (1, 1)])
def test_pattern_agrees_with_the_simulator_mosaic(ox, oy):
    lut = "RGB"
    ch = _sim_scene.cfa_channel_map(2, 2, ox, oy)
    tile = "".join(lut[c] for c in ch.ravel())
    assert sensor.physical_bayer_pattern(2, ox, oy) == tile


def test_mono_and_colour():
    assert sensor.bayer_pattern_for(0, None, None) == "MONO"
    assert sensor.bayer_pattern_for(1) == "RGB"


@pytest.mark.parametrize("sensor_type", [3, 4, 5, 9, "x", None])
def test_unsupported_types_read_as_mono_and_warn_once(sensor_type):
    from services.logger import app_logger
    calls = []
    original = app_logger.warning
    app_logger.warning = lambda msg, *a, **k: calls.append(msg)
    try:
        assert sensor.bayer_pattern_for(sensor_type) == "MONO"
        assert sensor.bayer_pattern_for(sensor_type) == "MONO"
    finally:
        app_logger.warning = original
    assert len(calls) == 1 and "monochrome" in calls[0]


@pytest.mark.parametrize("pattern,flip,expected", [
    ("RGGB", 0, "RGGB"), ("RGGB", 1, "GRBG"), ("RGGB", 2, "GBRG"), ("RGGB", 3, "BGGR"),
    ("BGGR", 1, "GBRG"), ("GRBG", 2, "BGGR"), ("MONO", 3, "MONO"), ("RGB", 1, "RGB"),
])
def test_flip_adjusts_the_pattern(pattern, flip, expected):
    assert sensor.flip_bayer_pattern(pattern, flip) == expected


def test_flip_on_odd_dimensions_keeps_that_axis():
    assert sensor.flip_bayer_pattern("RGGB", 1, width=5, height=4) == "RGGB"
    assert sensor.flip_bayer_pattern("RGGB", 2, width=5, height=3) == "RGGB"
    assert sensor.flip_bayer_pattern("RGGB", 3, width=5, height=4) == "GBRG"


@pytest.mark.parametrize("flip", [1, 2, 3])
@pytest.mark.parametrize("w,h", [(6, 4), (5, 4), (6, 3), (5, 3)])
def test_flipped_mosaic_matches_flipped_pattern(flip, w, h):
    ch = _sim_scene.cfa_channel_map(h, w, 0, 0)
    flipped = sensor.apply_flip(ch, flip)
    tile = "".join("RGB"[c] for c in flipped[:2, :2].ravel())
    assert sensor.flip_bayer_pattern("RGGB", flip, width=w, height=h) == tile


def test_apply_flip_none_is_a_copy_shape_kept():
    arr = np.arange(12).reshape(3, 4)
    np.testing.assert_array_equal(sensor.apply_flip(arr, 0), arr)
    np.testing.assert_array_equal(sensor.apply_flip(arr, 1), arr[:, ::-1])
    assert sensor.apply_flip(arr, 3).flags["C_CONTIGUOUS"]


def test_maxadu_255_is_8_bit_clipped():
    raw, depth = sensor.to_raw_array(np.array([[-5, 0, 128, 300]]), 255)
    assert depth == 8 and raw.dtype == np.uint8
    assert raw.tolist() == [[0, 0, 128, 255]]


@pytest.mark.parametrize("max_adu,shift", [(4095, 4), (16383, 2), (65535, 0), (1023, 6)])
def test_maxadu_left_shift_to_msb(max_adu, shift):
    raw, depth = sensor.to_raw_array(np.array([[0, 1, max_adu, max_adu + 50]]), max_adu)
    assert depth == 16 and raw.dtype == np.uint16
    assert raw.tolist() == [[0, 1 << shift, max_adu << shift, max_adu << shift]]
    assert int(raw.max()) >> 8 == (max_adu << shift) >> 8  # debayer's >> 8 sees full scale


def test_maxadu_above_16_bits_shifts_right_and_unknown_reads_65535():
    raw, _ = sensor.to_raw_array(np.array([[(1 << 20) - 1]]), (1 << 20) - 1)
    assert raw.tolist() == [[65535]]
    assert raw.dtype == np.uint16
    raw2, depth = sensor.to_raw_array(np.array([[70000, 1000]]), None)
    assert depth == 16 and raw2.tolist() == [[65535, 1000]]


def test_float_input_is_rounded():
    raw, _ = sensor.to_raw_array(np.array([[1.6, 2.4]]), 255)
    assert raw.tolist() == [[2, 2]]


def test_to_raw_bytes_is_row_major_little_endian():
    arr = np.array([[1, 2], [3, 4]])
    data, depth = sensor.to_raw_bytes(arr, 4095)
    assert depth == 16
    assert np.frombuffer(data, "<u2").reshape(2, 2).tolist() == [[16, 32], [48, 64]]
    data8, depth8 = sensor.to_raw_bytes(arr, 255)
    assert depth8 == 8 and data8 == bytes([1, 2, 3, 4])


def test_bit_depth_helpers():
    assert sensor.sensor_bit_depth(4095) == 12
    assert sensor.sensor_bit_depth(65535) == 16
    assert sensor.transfer_bit_depth(255) == 8
    assert sensor.transfer_bit_depth(257) == 16
    assert sensor.sensor_bit_depth(0) == 16


def test_max_adu_reported_as_a_power_of_two_is_the_level_count():
    # 65536 must not read as 17-bit (that would halve every pixel on the way to 16).
    assert sensor.sensor_bit_depth(65536) == 16 and sensor.transfer_bit_depth(65536) == 16
    assert sensor.sensor_bit_depth(4096) == 12 and sensor.sensor_bit_depth(256) == 8
    raw, depth = sensor.to_raw_array(np.array([[0, 1000, 65535]]), 65536)
    assert depth == 16 and raw.tolist() == [[0, 1000, 65535]]
    raw, _ = sensor.to_raw_array(np.array([[4095]]), 4096)
    assert raw.tolist() == [[4095 << 4]]


@pytest.mark.parametrize("dtype", [np.uint16, np.int32, np.int64, np.float64, np.uint8])
def test_to_raw_array_matches_with_and_without_inplace(dtype):
    data = np.array([[-5, 0, 100, 4095, 5000], [7, 8, 9, 10, 11]]).clip(
        0 if np.dtype(dtype).kind == "u" else -5, 255 if dtype is np.uint8 else None).astype(dtype)
    expected, depth = sensor.to_raw_array(data.copy(), 4095)
    work = data.copy()
    got, depth2 = sensor.to_raw_array(work, 4095, inplace=True)
    assert depth == depth2 == 16 and got.dtype == np.uint16
    np.testing.assert_array_equal(got, expected)
    np.testing.assert_array_equal(expected, (np.clip(data.astype(np.int64), 0, 4095) << 4))


def test_to_raw_array_without_inplace_never_touches_the_input():
    data = np.array([[5000, 1]], dtype=np.uint16)
    sensor.to_raw_array(data, 4095)
    assert data.tolist() == [[5000, 1]]
    data.flags.writeable = False
    raw, _ = sensor.to_raw_array(data, 4095, inplace=True)
    assert raw.tolist() == [[4095 << 4, 16]] and data.tolist() == [[5000, 1]]


def test_uint16_inplace_needs_no_new_frame():
    data = np.arange(12, dtype=np.uint16).reshape(3, 4)
    raw, _ = sensor.to_raw_array(data, 4095, inplace=True)
    assert np.shares_memory(raw, data)
