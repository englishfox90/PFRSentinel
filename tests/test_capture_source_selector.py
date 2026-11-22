"""Capture Mode selector: three segments ↔ (capture_mode, camera_source) (offscreen Qt)."""
from types import SimpleNamespace

import pytest

from services.config import Config


@pytest.fixture
def panel_and_config(tmp_path):
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication, QWidget
    app = QApplication.instance() or QApplication([])
    from ui.panels.capture_settings import CaptureSettingsPanel
    config = Config(str(tmp_path / "config.json"))
    host = QWidget()
    host.config = config
    panel = CaptureSettingsPanel(host)
    emitted = []
    panel.settings_changed.connect(lambda: emitted.append(1))
    yield SimpleNamespace(panel=panel, config=config, emitted=emitted, app=app)
    host.deleteLater()


@pytest.mark.parametrize("key, mode, source", [
    ('zwo', 'camera', 'zwo'),
    ('alpaca', 'camera', 'alpaca'),
])
def test_camera_segments_write_mode_and_source(panel_and_config, key, mode, source):
    p = panel_and_config
    p.panel.load_from_config(p.config)
    p.panel._on_mode_changed(key)
    assert p.config.get('capture_mode') == mode
    assert p.config.get('camera_source') == source
    assert p.emitted
    assert p.panel.settings_stack.currentIndex() == 1


def test_watch_segment_leaves_the_camera_source_alone(panel_and_config):
    p = panel_and_config
    p.config.set('camera_source', 'alpaca')
    p.panel.load_from_config(p.config)
    p.panel._on_mode_changed('watch')
    assert p.config.get('capture_mode') == 'watch'
    assert p.config.get('camera_source') == 'alpaca'
    assert p.panel.settings_stack.currentIndex() == 0


@pytest.mark.parametrize("mode, source, key", [
    ('watch', 'zwo', 'watch'),
    ('watch', 'alpaca', 'watch'),
    ('camera', 'zwo', 'zwo'),
    ('camera', 'alpaca', 'alpaca'),
    ('camera', 'bogus', 'zwo'),
])
def test_load_selects_the_segment_from_the_pair(panel_and_config, mode, source, key):
    p = panel_and_config
    p.config.set('capture_mode', mode)
    p.config.set('camera_source', source)
    p.panel.load_from_config(p.config)
    assert p.panel.mode_selector.currentRouteKey() == key
    assert p.config.get('camera_source') == source, "loading never rewrites config"
    assert not p.emitted


def test_description_follows_the_segment(panel_and_config):
    p = panel_and_config
    p.panel.load_from_config(p.config)
    texts = {}
    for key in ('watch', 'zwo', 'alpaca'):
        p.panel._on_mode_changed(key)
        texts[key] = p.panel.mode_desc.text()
    assert 'ZWO' in texts['zwo'] and 'Alpaca' in texts['alpaca']
    assert len(set(texts.values())) == 3


def test_selecting_a_source_gates_the_camera_widgets(panel_and_config):
    p = panel_and_config
    p.panel.load_from_config(p.config)
    w = p.panel.camera_widget
    p.panel._on_mode_changed('alpaca')
    assert w.zwo_card.isHidden() and not w.alpaca_card.isHidden()
    p.panel._on_mode_changed('zwo')
    assert not w.zwo_card.isHidden() and w.alpaca_card.isHidden()


def test_capture_locks_the_source_selector(panel_and_config):
    p = panel_and_config
    p.panel.set_capture_active(True)
    assert not p.panel.mode_selector.isEnabled()
    p.panel.set_capture_active(False)
    assert p.panel.mode_selector.isEnabled()
