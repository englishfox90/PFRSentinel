"""render_allsky_for_preview hands the renderer the current NINA target.

Issue #137: the GUI preview path (ZWO and Watch mode, and so burn-in) reads
the target the plugin last pushed from ``services.nina_target_store`` and
injects it as ``cfg['_nina_target']``, the way it injects ``_obs_utc``. A
target older than ``allsky_overlay.nina_target.stale_after_s`` is dropped.
"""
import pytest
from PIL import Image

from services.allsky import overlay_renderer
from services.nina_target_store import (
    NinaTarget, NinaTargetStore, get_nina_target_store, reset_nina_target_store,
)

TARGET = NinaTarget(name='M31', ra_deg=10.6847, dec_deg=41.2687,
                    fov_w_deg=2.0, fov_h_deg=1.5, rotation_deg=15.0)


class _Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


@pytest.fixture
def seen(monkeypatch, tmp_path):
    """Run the preview path up to the render and record the config it built."""
    captured = {}

    def fake_render(img, cfg, metadata):
        captured['cfg'] = cfg
        return img

    import services.app_config
    import services.observing_window
    monkeypatch.setattr(overlay_renderer, 'render_allsky_overlay', fake_render)
    monkeypatch.setattr(overlay_renderer, 'overlay_withheld', lambda: False)
    monkeypatch.setattr(services.observing_window, 'is_observing_window',
                        lambda *a, **kw: True)
    monkeypatch.setattr(services.app_config, 'get_obstruction_map_path',
                        lambda: str(tmp_path / 'map.npz'))
    reset_nina_target_store()
    yield captured
    reset_nina_target_store()


@pytest.fixture
def clocked_store(monkeypatch):
    clock = _Clock()
    store = NinaTargetStore(monotonic=clock)
    monkeypatch.setattr(overlay_renderer, 'get_nina_target_store', lambda: store)
    return store, clock


def _preview(allsky_extra=None):
    allsky = {'enabled': True, 'calibration_file': 'cal.json'}
    allsky.update(allsky_extra or {})
    overlay_renderer.render_allsky_for_preview(
        Image.new('RGB', (32, 32)), allsky, {'weather': {}}, {})


def test_a_fresh_target_reaches_the_renderer(seen):
    get_nina_target_store().set(TARGET)
    _preview()
    assert seen['cfg']['_nina_target'] == TARGET.as_dict()


def test_no_target_injects_none(seen):
    _preview()
    assert seen['cfg']['_nina_target'] is None


def test_a_cleared_target_injects_none(seen):
    store = get_nina_target_store()
    store.set(TARGET)
    store.set(None)
    _preview()
    assert seen['cfg']['_nina_target'] is None


def test_a_stale_target_is_dropped_after_the_default_120_s(seen, clocked_store):
    store, clock = clocked_store
    store.set(TARGET)
    clock.t += 119.0
    _preview()
    assert seen['cfg']['_nina_target'] == TARGET.as_dict()
    clock.t += 2.0
    _preview()
    assert seen['cfg']['_nina_target'] is None


@pytest.mark.parametrize('stale_after_s, age, kept', [
    (600, 300.0, True),
    (60, 90.0, False),
])
def test_stale_after_s_is_read_from_the_layer_config(seen, clocked_store,
                                                     stale_after_s, age, kept):
    store, clock = clocked_store
    store.set(TARGET)
    clock.t += age
    _preview({'nina_target': {'stale_after_s': stale_after_s}})
    assert (seen['cfg']['_nina_target'] is not None) is kept


def test_the_callers_config_is_not_written(seen):
    get_nina_target_store().set(TARGET)
    allsky = {'enabled': True, 'calibration_file': 'cal.json'}
    overlay_renderer.render_allsky_for_preview(
        Image.new('RGB', (32, 32)), allsky, {'weather': {}}, {})
    assert '_nina_target' not in allsky


@pytest.mark.parametrize('stale_after_s, kept_at, dropped_at', [
    (None, 119.0, 121.0),
    ('2m', 119.0, 121.0),
    (float('nan'), 119.0, 121.0),
    (0, 29.0, 31.0),
    (-5, 29.0, 31.0),
    (1e9, 3599.0, 3601.0),
])
def test_an_unusable_stale_after_s_is_clamped_not_fatal(seen, clocked_store,
                                                        stale_after_s, kept_at, dropped_at):
    store, clock = clocked_store
    store.set(TARGET)
    start = clock.t
    clock.t = start + kept_at
    _preview({'nina_target': {'stale_after_s': stale_after_s}})
    assert seen['cfg']['_nina_target'] == TARGET.as_dict()
    clock.t = start + dropped_at
    _preview({'nina_target': {'stale_after_s': stale_after_s}})
    assert seen['cfg']['_nina_target'] is None


def test_a_null_layer_block_still_renders_the_overlay(seen):
    get_nina_target_store().set(TARGET)
    _preview({'nina_target': None})
    assert seen['cfg']['_nina_target'] == TARGET.as_dict()


def test_a_failing_store_loses_the_target_not_the_overlay(seen, monkeypatch):
    class _Broken:
        def current(self, max_age_s):
            raise RuntimeError('store broke')

    monkeypatch.setattr(overlay_renderer, 'get_nina_target_store', lambda: _Broken())
    _preview()
    assert 'cfg' in seen
    assert seen['cfg']['_nina_target'] is None
