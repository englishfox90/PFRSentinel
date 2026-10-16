"""Watch mode hands the output crop to the all-sky preview renderer (issue #12)."""
import pytest

pytest.importorskip("PySide6")
from PIL import Image

from ui.controllers import watch_controller as wc


class _Config:
    def __init__(self, data):
        self._data = data

    def get(self, key, default=None):
        return self._data.get(key, default)


def _controller(config):
    ctrl = wc.WatchControllerQt.__new__(wc.WatchControllerQt)
    ctrl.config = _Config(config)
    ctrl.file_detected = _Signal()
    ctrl.image_processed = _Signal()
    return ctrl


class _Signal:
    def __init__(self):
        self.calls = []

    def emit(self, *args):
        self.calls.append(args)


def test_crop_on_the_image_reaches_the_allsky_renderer(monkeypatch):
    seen = {}

    def fake_render(img, allsky_cfg, config, metadata):
        seen['metadata'] = dict(metadata)
        return img

    monkeypatch.setattr(wc, 'render_allsky_for_preview', fake_render)
    ctrl = _controller({'allsky_overlay': {'enabled': True}})
    img = Image.new('RGB', (200, 200))
    crop = {'x': 40, 'y': 100, 'width': 200, 'height': 200, 'frame_width': 640, 'frame_height': 480}
    img.info['OUTPUT_CROP'] = crop

    ctrl._on_file_processed('out.png', img)

    assert seen['metadata'] == {'OUTPUT_CROP': crop}
    assert ctrl.image_processed.calls[0][1] is img


def test_uncropped_image_passes_empty_metadata(monkeypatch):
    seen = {}
    monkeypatch.setattr(wc, 'render_allsky_for_preview',
                        lambda img, a, c, metadata: seen.setdefault('metadata', dict(metadata)) and img)
    ctrl = _controller({})

    ctrl._on_file_processed('out.png', Image.new('RGB', (10, 10)))

    assert seen['metadata'] == {}


def test_extras_are_forwarded_on_the_image_processed_signal(monkeypatch):
    monkeypatch.setattr(wc, 'render_allsky_for_preview', lambda img, a, c, metadata: img)
    ctrl = _controller({})
    img = Image.new('RGB', (10, 10))
    extras = {'metadata': {'FILENAME': 'x.png'}, 'native_size': (640, 480),
              'clean_frame': Image.new('RGB', (640, 480))}

    ctrl._on_file_processed('out.png', img, extras)

    call = ctrl.image_processed.calls[0]
    assert call[0] is not None and call[1] is img and call[2] == 'out.png'
    assert call[3] == extras
    assert call[3] is not extras  # forwarded as a copy, not the caller's dict


def test_extras_default_to_an_empty_dict_when_none(monkeypatch):
    monkeypatch.setattr(wc, 'render_allsky_for_preview', lambda img, a, c, metadata: img)
    ctrl = _controller({})

    ctrl._on_file_processed('out.png', Image.new('RGB', (10, 10)))

    assert ctrl.image_processed.calls[0][3] == {}


# --- the processor's metadata reaches the renderer (issue #93) --------------

def test_processor_metadata_reaches_the_renderer_with_the_crop_added(monkeypatch):
    seen = {}

    def fake_render(img, allsky_cfg, config, metadata):
        seen['metadata'] = metadata
        return img

    monkeypatch.setattr(wc, 'render_allsky_for_preview', fake_render)
    ctrl = _controller({'allsky_overlay': {'enabled': True}})
    img = Image.new('RGB', (200, 200))
    crop = {'x': 40, 'y': 100, 'width': 200, 'height': 200, 'frame_width': 640, 'frame_height': 480}
    img.info['OUTPUT_CROP'] = crop
    metadata = {'EXPOSURE': '13s', 'ROOF_STATUS': 'Open (95%)',
                '_SKY_EVIDENCE': {'star_count': 3}, '_observing_window': False,
                '_observing_window_reason': 'no_stars'}

    ctrl._on_file_processed('out.png', img, {'metadata': metadata, 'native_size': (640, 480)})

    # The very dict the processor judged — its cached verdict included — so
    # the overlay follows that decision instead of asking again blind.
    assert seen['metadata'] is metadata
    assert metadata['OUTPUT_CROP'] == crop
    assert ctrl.image_processed.calls[0][3]['metadata'] is metadata


def test_the_renderer_honours_the_cached_verdict_on_forwarded_metadata(monkeypatch):
    from services.allsky import overlay_renderer

    def fail_render(*a, **kw):
        raise AssertionError("must not render a frame the gate suppressed")

    monkeypatch.setattr(overlay_renderer, 'render_allsky_overlay', fail_render)
    monkeypatch.setattr(wc, 'render_allsky_for_preview', overlay_renderer.render_allsky_for_preview)
    ctrl = _controller({'allsky_overlay': {'enabled': True, 'calibration_file': 'cal.json'},
                        'weather': {}})
    img = Image.new('RGB', (10, 10))
    metadata = {'_observing_window': False, '_observing_window_reason': 'no_stars'}

    ctrl._on_file_processed('out.png', img, {'metadata': metadata})

    assert ctrl.image_processed.calls[0][0] is img


def test_extras_without_metadata_fall_back_to_a_fresh_dict(monkeypatch):
    seen = {}
    monkeypatch.setattr(wc, 'render_allsky_for_preview',
                        lambda img, a, c, metadata: seen.setdefault('metadata', metadata) and img)
    ctrl = _controller({})

    ctrl._on_file_processed('out.png', Image.new('RGB', (10, 10)), {'metadata': None})

    assert seen['metadata'] == {}
