"""
services/watcher.py — ImageFileHandler.process_file (issue #12).

process_image now fills an `extras` dict (metadata / native_size / clean_frame)
so watch mode can cache a full, uncropped frame for "Calibrate Now" and
reprocess. The watcher must forward that dict as the callback's third
positional argument.
"""
from PIL import Image

from services.watcher import ImageFileHandler


class _StubConfig:
    """Minimal stand-in for services.config.Config — process_image only calls
    .get() and .get_overlays()."""

    def __init__(self, values: dict):
        self._values = values

    def get(self, key, default=None):
        return self._values.get(key, default)

    def get_overlays(self):
        return self._values.get('overlays', [])


def _handler(config, on_image_processed):
    return ImageFileHandler(config, on_image_processed=on_image_processed)


def test_process_file_forwards_extras_to_the_callback(tmp_path):
    watch_dir = tmp_path / 'watch'
    watch_dir.mkdir()
    out_dir = tmp_path / 'out'
    filepath = str(watch_dir / 'frame.png')
    Image.new('RGB', (64, 48), (10, 20, 30)).save(filepath)

    config = _StubConfig({
        'output_directory': str(out_dir),
        'output_pattern': '{filename}',
        'output_format': 'PNG',
        'overlays': [],
        'resize_percent': 100,
        'auto_stretch': {'enabled': False},
        'ml_models': {'enabled': False},
        'cleanup_enabled': False,
    })

    received = []
    handler = _handler(config, lambda *args: received.append(args))
    try:
        handler.process_file(filepath)
    finally:
        handler.executor.shutdown(wait=False)

    assert len(received) == 1
    out_path, processed_img, extras = received[0]
    assert out_path and processed_img is not None
    assert isinstance(extras, dict)
    assert extras['native_size'] == (64, 48)
    assert extras['clean_frame'].size == (64, 48)
    assert extras['metadata']['FILENAME'] == 'frame.png'


def test_process_file_forwards_extras_even_when_output_is_cropped(tmp_path):
    watch_dir = tmp_path / 'watch'
    watch_dir.mkdir()
    out_dir = tmp_path / 'out'
    filepath = str(watch_dir / 'frame.png')
    Image.new('RGB', (640, 480), (10, 20, 30)).save(filepath)

    config = _StubConfig({
        'output_directory': str(out_dir),
        'output_pattern': '{filename}',
        'output_format': 'PNG',
        'overlays': [],
        'resize_percent': 100,
        'auto_stretch': {'enabled': False},
        'ml_models': {'enabled': False},
        'cleanup_enabled': False,
        'output_crop': {'enabled': True, 'x': 40, 'y': 100, 'width': 200, 'height': 200,
                        'ref_width': 640, 'ref_height': 480},
    })

    received = []
    handler = _handler(config, lambda *args: received.append(args))
    try:
        handler.process_file(filepath)
    finally:
        handler.executor.shutdown(wait=False)

    assert len(received) == 1
    out_path, processed_img, extras = received[0]
    # The callback's image is the (cropped) output; extras carries the full
    # pre-crop frame separately so watch mode's cache never gets clipped.
    assert processed_img.size == (200, 200)
    assert extras['clean_frame'].size == (640, 480)
    assert extras['native_size'] == (640, 480)
