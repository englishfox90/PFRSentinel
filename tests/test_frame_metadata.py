"""Golden metadata for one ZWO frame: exact keys, order and values (issue #43).

Written against the pre-seam capture path and kept green through the move to
``frame_metadata.build_frame_metadata``. Every overlay token, the raw-frame
cache and the dev-mode exports read these keys, so a second camera backend
must produce the same dict. ``GOLDEN_KEYS`` is the list it is checked against.
"""
import importlib
from datetime import datetime as real_datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

GOLDEN_KEYS = (
    'CAMERA', 'EXPOSURE', 'EXPOSURE_START_UTC', 'GAIN', 'TEMP', 'TEMPERATURE',
    'TEMP_C', 'TEMP_F', 'RES', 'CAPTURE AREA SIZE', 'FILENAME', 'SESSION',
    'DATETIME', 'BRIGHTNESS', 'MEAN', 'MEDIAN', 'MIN', 'MAX', 'STD_DEV', 'P25',
    'P75', 'P95', 'RAW_RGB_NO_WB', 'RAW_RGB_16BIT', 'RAW_BAYER', 'RAW_GEOMETRY',
    'WB_CONFIG', 'CAMERA_BIT_DEPTH', 'IMAGE_BIT_DEPTH', 'BAYER_PATTERN',
    'PIXEL_SIZE', 'ELEC_PER_ADU',
)

_START_EPOCH = 1_790_000_000.0
_WALL_CLOCK = real_datetime(2026, 9, 21, 22, 15, 30)
_WIDTH, _HEIGHT = 8, 6

# Wherever the capture path reads the clock: before the seam it was all in the
# worker; after it, the metadata builder owns datetime and the worker owns time.
_CLOCK_MODULES = (
    'services.camera.zwo_capture_worker',
    'services.camera.frame_metadata',
)

ASI = SimpleNamespace(
    ASI_EXP_IDLE=0, ASI_EXP_WORKING=1, ASI_EXP_SUCCESS=2, ASI_EXP_FAILED=3,
    ASI_GAIN=0, ASI_EXPOSURE=1, ASI_BRIGHTNESS=5, ASI_TEMPERATURE=8, ASI_FLIP=9,
    ASI_IMG_RAW8=0, ASI_IMG_RAW16=2,
)


class _Clock:
    def __init__(self):
        self.now = _START_EPOCH

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class _FixedDatetime(real_datetime):
    @classmethod
    def now(cls, tz=None):
        return _WALL_CLOCK if tz is None else _WALL_CLOCK.replace(tzinfo=tz)


def _raw8_bytes():
    ramp = (np.arange(_WIDTH * _HEIGHT, dtype=np.uint16) * 5) % 256
    return ramp.astype(np.uint8).tobytes()


def _raw16_bytes():
    ramp = (np.arange(_WIDTH * _HEIGHT, dtype=np.uint32) * 1301) % 65536
    return ramp.astype('<u2').tobytes()


def _handle(data, image_type, temperature=215):
    handle = MagicMock()
    handle.get_exposure_status.return_value = ASI.ASI_EXP_SUCCESS
    handle.get_data_after_exposure.return_value = data
    handle.get_camera_property.return_value = {
        'Name': 'ZWO ASI676MC', 'MaxWidth': _WIDTH, 'MaxHeight': _HEIGHT,
        'BitDepth': 12, 'PixelSize': 2.0, 'ElecPerADU': 0.25,
    }
    handle.get_roi_format.return_value = (_WIDTH, _HEIGHT, 1, image_type)
    if temperature is None:
        handle.get_control_value.side_effect = RuntimeError("no sensor")
    else:
        handle.get_control_value.return_value = (temperature, False)
    return handle


def _session(handle):
    from services.camera.zwo_camera import ZWOCamera
    with patch('services.camera.zwo_camera.CameraConnection') as conn_cls:
        conn_cls.return_value = MagicMock(asi=ASI, camera=handle, sdk_lock=MagicMock())
        cam = ZWOCamera(sdk_path=None, exposure_sec=2.5, gain=150,
                        wb_config={'mode': 'gray_world'}, bayer_pattern='BGGR')
    cam.on_log_callback = lambda _msg: None
    cam.is_capturing = True
    return cam


@pytest.fixture
def capture(monkeypatch):
    for name in _CLOCK_MODULES:
        try:
            module = importlib.import_module(name)
        except ImportError:
            continue
        if hasattr(module, 'time'):
            monkeypatch.setattr(module, 'time', _Clock())
        if hasattr(module, 'datetime'):
            monkeypatch.setattr(module, 'datetime', _FixedDatetime)

    def run(handle):
        from services.camera import zwo_capture_worker
        cam = _session(handle)
        return cam, zwo_capture_worker.capture_single_frame(cam)
    return run


GOLDEN_RAW8_STATS = {
    'MEAN': '117.5', 'MEDIAN': '117.5', 'MIN': '45', 'MAX': '190',
    'STD_DEV': '51.26', 'P25': '68.8', 'P75': '166.2', 'P95': '190.0',
}


def test_raw8_frame_metadata_keys_in_order(capture):
    _cam, (_img, metadata) = capture(_handle(_raw8_bytes(), ASI.ASI_IMG_RAW8))
    assert tuple(metadata) == GOLDEN_KEYS


def test_raw8_frame_metadata_values(capture):
    data = _raw8_bytes()
    _cam, (img, metadata) = capture(_handle(data, ASI.ASI_IMG_RAW8))
    scalars = {k: v for k, v in metadata.items()
               if k not in ('RAW_RGB_NO_WB', 'RAW_RGB_16BIT', 'RAW_BAYER')}
    assert scalars == {
        'CAMERA': 'ZWO ASI676MC',
        'EXPOSURE': '2.5s',
        'EXPOSURE_START_UTC': real_datetime.fromtimestamp(
            _START_EPOCH, timezone.utc).isoformat(),
        'GAIN': '150',
        'TEMP': '21.5 C',
        'TEMPERATURE': '21.5 C',
        'TEMP_C': '21.5°C',
        'TEMP_F': '70.7°F',
        'RES': '8x6',
        'CAPTURE AREA SIZE': '8 * 6',
        'FILENAME': 'capture_20260921_221530.png',
        'SESSION': '2026-09-21',
        'DATETIME': '2026-09-21 22:15:30',
        'BRIGHTNESS': GOLDEN_RAW8_STATS['MEAN'],
        **GOLDEN_RAW8_STATS,
        'RAW_GEOMETRY': (8, 6, 8, 'BGGR'),
        'WB_CONFIG': {'mode': 'gray_world'},
        'CAMERA_BIT_DEPTH': 12,
        'IMAGE_BIT_DEPTH': 8,
        'BAYER_PATTERN': 'BGGR',
        'PIXEL_SIZE': 2.0,
        'ELEC_PER_ADU': 0.25,
    }
    assert metadata['RAW_BAYER'] is data
    assert metadata['RAW_RGB_16BIT'] is None
    no_wb = metadata['RAW_RGB_NO_WB']
    assert no_wb.shape == (_HEIGHT, _WIDTH, 3) and no_wb.dtype == np.uint8
    assert img.size == (_WIDTH, _HEIGHT) and img.mode == 'RGB'





def test_wb_config_is_a_copy(capture):
    cam, (_img, metadata) = capture(_handle(_raw8_bytes(), ASI.ASI_IMG_RAW8))
    assert metadata['WB_CONFIG'] == cam.wb_config
    assert metadata['WB_CONFIG'] is not cam.wb_config


def test_unreadable_temperature_reads_na(capture):
    _cam, (_img, metadata) = capture(
        _handle(_raw8_bytes(), ASI.ASI_IMG_RAW8, temperature=None))
    for key in ('TEMP', 'TEMPERATURE', 'TEMP_C', 'TEMP_F'):
        assert metadata[key] == 'N/A'


def test_negative_temperature_formatting(capture):
    _cam, (_img, metadata) = capture(
        _handle(_raw8_bytes(), ASI.ASI_IMG_RAW8, temperature=-73))
    assert (metadata['TEMP'], metadata['TEMP_C'], metadata['TEMP_F']) == (
        '-7.3 C', '-7.3°C', '18.9°F')


def test_raw16_frame_metadata(capture):
    data = _raw16_bytes()
    _cam, (_img, metadata) = capture(_handle(data, ASI.ASI_IMG_RAW16))
    assert tuple(metadata) == GOLDEN_KEYS
    assert metadata['IMAGE_BIT_DEPTH'] == 16
    assert metadata['RAW_GEOMETRY'] == (8, 6, 16, 'BGGR')
    assert metadata['RAW_BAYER'] is data
    assert metadata['RAW_RGB_NO_WB'] is None
    assert metadata['RAW_RGB_16BIT'].dtype == np.uint16


def test_camera_property_fallbacks(capture):
    handle = _handle(_raw8_bytes(), ASI.ASI_IMG_RAW8)
    handle.get_camera_property.return_value = {'Name': 'ZWO ASI120MM'}
    _cam, (_img, metadata) = capture(handle)
    assert (metadata['CAMERA_BIT_DEPTH'], metadata['PIXEL_SIZE'],
            metadata['ELEC_PER_ADU']) == (8, 0, 1.0)


def test_builder_key_list_is_the_golden_one():
    from services.camera.frame_metadata import FRAME_METADATA_KEYS
    assert FRAME_METADATA_KEYS == GOLDEN_KEYS


def test_builder_alone_produces_the_golden_keys():
    from services.camera.frame_metadata import build_frame_metadata, temperature_fields
    metadata = build_frame_metadata(
        camera_name='cam', exposure_seconds=1.0, gain=0, start_time=0.0,
        temp=temperature_fields(None), width=2, height=2,
        stats={k: 0 for k in ('mean', 'median', 'min', 'max', 'std_dev', 'p25', 'p75', 'p95')},
        arrays={'RAW_RGB_NO_WB': None, 'RAW_RGB_16BIT': None}, raw_bytes=b'\0' * 4,
        bit_depth=8, bayer_pattern='RGGB', wb_config=None, camera_bit_depth=8,
        pixel_size=0, elec_per_adu=1.0)
    assert tuple(metadata) == GOLDEN_KEYS
    assert metadata['WB_CONFIG'] == {}
