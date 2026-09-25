"""AllSkySettingsPanel.get_config keeps every allsky_overlay key (H11, issue #93).

The panel used to rebuild the dict from scratch, so any key it had no
control for — utc_offset_hours, planets.colors, constellations.edge_fade_px,
and every key added since — was wiped on the first edit of the page. It now
merges its controls over the dict it loaded.
"""
import copy

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QApplication

from services.config_defaults import DEFAULT_CONFIG
from ui.panels.allsky_settings import AllSkySettingsPanel


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def panel(qapp):
    widget = AllSkySettingsPanel()
    yield widget
    widget.close()
    widget.deleteLater()
    qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    qapp.processEvents()


def _leaf_paths(d, prefix=()):
    for key, value in d.items():
        if isinstance(value, dict):
            yield from _leaf_paths(value, prefix + (key,))
        else:
            yield prefix + (key,)


def _get(d, path):
    for key in path:
        d = d[key]
    return d


def _customised():
    """Every default leaf changed to a value the panel's controls can also
    hold, so equality after the round-trip proves nothing was rebuilt."""
    cfg = copy.deepcopy(DEFAULT_CONFIG['allsky_overlay'])
    cfg.update(enabled=True, calibration_file='cal.json', top_n=20,
               utc_offset_hours=5, min_exposure_s=1.25, min_star_detections=40)
    cfg['constellations'].update(enabled=False, lines=False, labels=False, color='#112233',
                                 line_width=7, label_size=21, opacity=99, edge_fade_px=123)
    cfg['bright_stars'].update(enabled=True, max_magnitude=4.0, bayer_fallback=True,
                               color='#223344', label_size=17, opacity=33)
    cfg['messier'].update(enabled=False, color='#334455', marker_size=3, label_size=4, opacity=5)
    cfg['ngc'].update(enabled=True, min_magnitude=10.0, color='#445566',
                      marker_size=2, label_size=3, opacity=4)
    cfg['planets'].update(enabled=False, color='#556677', label_size=6, marker_size=7, opacity=8)
    cfg['planets']['colors'].update(Mars='#123456', Moon='#654321')
    cfg['grid'].update(enabled=True, horizon=True, altitude_rings=True, altitude_step=15,
                       azimuth_lines=True, cardinal_labels=True, color='#667788',
                       line_width=3, label_size=9, opacity=10)
    cfg['burn_into_output'].update(saved_file=True, web=True, timelapse=True)
    return cfg


def test_every_default_key_round_trips_through_the_panel(panel):
    loaded = _customised()
    panel.load_from_config(copy.deepcopy(loaded))
    out = panel.get_config()

    for path in _leaf_paths(DEFAULT_CONFIG['allsky_overlay']):
        assert _get(out, path) == _get(loaded, path), '.'.join(path)
    assert out == loaded


def test_keys_the_panel_has_no_control_for_survive_an_edit(panel):
    loaded = _customised()
    panel.load_from_config(loaded)
    panel._top_n.setValue(25)
    out = panel.get_config()

    assert out['top_n'] == 25
    assert out['utc_offset_hours'] == 5
    assert out['planets']['colors']['Mars'] == '#123456'
    assert out['constellations']['edge_fade_px'] == 123
    assert out['grid']['enabled'] is True
    assert out['calibration_file'] == 'cal.json'


def test_a_key_unknown_to_the_defaults_is_carried_through(panel):
    loaded = _customised()
    loaded['future_key'] = {'nested': 1}
    panel.load_from_config(loaded)
    assert panel.get_config()['future_key'] == {'nested': 1}


def test_an_old_config_without_the_gate_keys_gets_the_defaults(panel):
    old = _customised()
    del old['min_exposure_s'], old['min_star_detections']
    panel.load_from_config(old)
    out = panel.get_config()
    assert out['min_exposure_s'] == 0.5 and out['min_star_detections'] == 100


def test_the_gate_spin_boxes_load_and_emit(panel, qapp):
    loaded = _customised()
    panel.load_from_config(loaded)
    assert panel._min_exposure.value() == pytest.approx(1.25)
    assert panel._min_stars.value() == 40

    emitted = []
    panel.settings_changed.connect(emitted.append)
    panel._min_exposure.setValue(2.0)
    panel._min_stars.setValue(30)
    qapp.processEvents()
    assert emitted[-1]['min_exposure_s'] == pytest.approx(2.0)
    assert emitted[-1]['min_star_detections'] == 30


def test_the_panel_does_not_alias_or_mutate_the_loaded_dict(panel):
    loaded = _customised()
    panel.load_from_config(loaded)
    out = panel.get_config()
    out['planets']['colors']['Mars'] = '#000000'
    assert loaded['planets']['colors']['Mars'] == '#123456'
    assert panel.get_config()['planets']['colors']['Mars'] == '#123456'
