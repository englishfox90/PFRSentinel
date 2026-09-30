"""SettingsPanel Weather Units — the Imperial choice is saved and applied (issue #125).

The panel used to decide units by a case-sensitive substring match on the
combo's display text: ``'imperial' in "Imperial (°F, mph)"`` is False, so
every edit saved 'metric', the Test button requested metric values, and the
combo fell back to Metric on the next load. The units key now travels as the
combo item's data, and the panel reads that.
"""
import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QApplication, QWidget

import services.weather as weather_mod
from services.config import Config
from ui.panels.settings_panel import SettingsPanel


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def build_panel(qapp):
    """Panel on a stand-in main window that carries a real Config, loaded."""
    built = []

    def _build(config):
        window = QWidget()
        window.config = config
        panel = SettingsPanel(window)
        panel.load_from_config(config)
        built.append((panel, window))
        return panel

    yield _build
    for panel, window in built:
        panel.close()
        window.deleteLater()
    qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    qapp.processEvents()


def _config(path, **weather):
    cfg = Config(str(path))
    cfg.set('weather', {**cfg.get('weather', {}), **weather})
    return cfg


def _select(panel, units):
    panel.units_combo.setCurrentIndex(panel.units_combo.findData(units))


def test_selecting_imperial_saves_imperial(tmp_path, build_panel):
    cfg = _config(tmp_path / "config.json")
    _select(build_panel(cfg), 'imperial')
    assert cfg.get('weather')['units'] == 'imperial'


def test_selecting_metric_saves_metric(tmp_path, build_panel):
    cfg = _config(tmp_path / "config.json", units='imperial')
    _select(build_panel(cfg), 'metric')
    assert cfg.get('weather')['units'] == 'metric'


def test_saved_imperial_survives_a_restart(tmp_path, build_panel):
    path = tmp_path / "config.json"
    cfg = _config(path)
    _select(build_panel(cfg), 'imperial')
    cfg.save()
    assert build_panel(Config(str(path))).units_combo.currentData() == 'imperial'


def test_loading_metric_returns_the_combo_to_metric(tmp_path, build_panel):
    panel = build_panel(_config(tmp_path / "imperial.json", units='imperial'))
    assert panel.units_combo.currentData() == 'imperial'
    panel.load_from_config(_config(tmp_path / "metric.json", units='metric'))
    assert panel.units_combo.currentData() == 'metric'


def test_units_the_combo_does_not_offer_read_as_metric(tmp_path, build_panel):
    panel = build_panel(_config(tmp_path / "config.json", units='standard'))
    assert panel.units_combo.currentData() == 'metric'


def test_test_button_requests_the_selected_units(tmp_path, build_panel, monkeypatch):
    constructed = []

    class _RecordingService:
        def __init__(self, **kwargs):
            constructed.append(kwargs)

        def fetch_weather(self):
            return {'temp': '68.0°F', 'condition': 'Clear'}

    monkeypatch.setattr(weather_mod, 'WeatherService', _RecordingService)
    panel = build_panel(_config(tmp_path / "config.json", units='imperial',
                                api_key='k', location='Austin, US'))
    panel._test_weather()
    assert [c['units'] for c in constructed] == ['imperial']
    assert panel.weather_status_label.text() == "✓ Clear, 68.0°F"
