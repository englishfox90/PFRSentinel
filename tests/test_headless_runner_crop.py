"""
services/headless_runner.py `_process_and_save` — output framing (issue #12).

The crop must apply after resize and before overlays. Constructed via
`HeadlessRunner.__new__` to avoid the real __init__ (camera/config/web-server
bring-up); only the attributes `_process_and_save` actually touches are
stubbed.
"""
from PIL import Image

from services.headless_runner import HeadlessRunner


class _Config:
    def __init__(self, data):
        self._data = data

    def get(self, key, default=None):
        return self._data.get(key, default)


def _runner(config_data):
    runner = HeadlessRunner.__new__(HeadlessRunner)
    runner.config = _Config(config_data)
    runner.image_library = None
    runner.web_server = None
    runner._logs = []
    runner._log = runner._logs.append
    return runner


def test_process_and_save_applies_output_crop(tmp_path):
    runner = _runner({
        'resize_percent': 100,
        'overlays': [],
        'output_directory': str(tmp_path),
        'filename_pattern': 'latestImage',
        'output_format': 'png',
        'output_crop': {'enabled': True, 'x': 40, 'y': 100, 'width': 200, 'height': 200,
                        'ref_width': 640, 'ref_height': 480},
    })
    img = Image.new('RGB', (640, 480), (10, 20, 30))
    metadata = {}

    runner._process_and_save(img, metadata)

    assert runner._logs == [], f"unexpected error log output: {runner._logs}"
    saved = list(tmp_path.glob('*.png'))
    assert len(saved) == 1
    assert Image.open(saved[0]).size == (200, 200)
    assert metadata['OUTPUT_CROP']['frame_width'] == 640


def test_process_and_save_crops_after_resize(tmp_path):
    # resize_percent halves the frame first; the crop reference (640x480) must
    # scale down with it, and the saved file must reflect the SCALED crop, not
    # the crop applied to the pre-resize frame.
    runner = _runner({
        'resize_percent': 50,
        'overlays': [],
        'output_directory': str(tmp_path),
        'filename_pattern': 'latestImage',
        'output_format': 'png',
        'output_crop': {'enabled': True, 'x': 40, 'y': 100, 'width': 200, 'height': 200,
                        'ref_width': 640, 'ref_height': 480},
    })
    img = Image.new('RGB', (640, 480), (10, 20, 30))
    metadata = {}

    runner._process_and_save(img, metadata)

    assert runner._logs == [], f"unexpected error log output: {runner._logs}"
    saved = list(tmp_path.glob('*.png'))
    assert len(saved) == 1
    assert Image.open(saved[0]).size == (100, 100)
    assert metadata['OUTPUT_CROP']['frame_width'] == 320


def test_disabled_crop_saves_the_full_frame(tmp_path):
    runner = _runner({
        'resize_percent': 100,
        'overlays': [],
        'output_directory': str(tmp_path),
        'filename_pattern': 'latestImage',
        'output_format': 'png',
        'output_crop': {'enabled': False},
    })
    img = Image.new('RGB', (640, 480), (10, 20, 30))
    metadata = {}

    runner._process_and_save(img, metadata)

    assert runner._logs == [], f"unexpected error log output: {runner._logs}"
    saved = list(tmp_path.glob('*.png'))
    assert len(saved) == 1
    assert Image.open(saved[0]).size == (640, 480)
    assert 'OUTPUT_CROP' not in metadata
