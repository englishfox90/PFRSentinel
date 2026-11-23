"""ImagingConditionsCard + ImagingConditionsController + the IMAGING tile:
load is silent, edits emit once and values() carries the controlled keys,
the controller saves them into the config block and refreshes the card and
the status strip from the store, the learning line, and Reset baseline
forgets the learned counts (offscreen Qt)."""
import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QApplication

from services.config import Config
from services.imaging_conditions import ConditionsStore, get_imaging_conditions_feed
from ui.components.imaging_conditions_text import (
    baseline_learning_text, detail_lines, summarize_conditions)
from ui.controllers.imaging_conditions_controller import ImagingConditionsController
from ui.panels.imaging_conditions_card import ImagingConditionsCard


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


@pytest.fixture
def card(qapp):
    widget = ImagingConditionsCard()
    yield widget
    widget.close()
    widget.deleteLater()
    qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_load_is_silent_and_values_round_trip(card):
    hits = []
    card.changed.connect(lambda: hits.append(1))
    cfg = {'enabled': False, 'max_sun_alt_deg': -12.0, 'max_cloud_cover_pct': 35,
           'min_star_ratio': 0.6, 'bad_frames': 5, 'good_frames': 2}
    card.load(cfg)
    assert hits == []
    assert card.values() == dict(cfg, max_cloud_cover_pct=35.0)


def test_garbage_loads_as_defaults(card):
    card.load({'max_sun_alt_deg': 'x', 'bad_frames': None, 'min_star_ratio': float('nan')})
    v = card.values()
    assert v['max_sun_alt_deg'] == -18.0 and v['bad_frames'] == 3 and v['min_star_ratio'] == 0.4


def test_each_edit_emits_once(card):
    hits = []
    card.changed.connect(lambda: hits.append(1))
    card.cloud_spin.setValue(70)
    card.sun_combo.setCurrentIndex(1)
    card.enabled_switch.set_checked(not card.enabled_switch.is_checked())
    assert len(hits) == 3


def test_summaries():
    assert summarize_conditions({'enabled': False}) == ("Off", 'muted')
    assert summarize_conditions({'enabled': True, 'imaging_ok': True,
                                 'cloud_cover_pct': 12.0}) == ("OK · 12% cloud", 'ok')
    assert summarize_conditions({'enabled': True, 'stale': True, 'reasons': ['stale']})[1] == 'muted'
    assert summarize_conditions({'enabled': True, 'reasons': ['warming_up']}) == ("Warming up", 'info')
    assert summarize_conditions({'enabled': True, 'reasons': ['cloud_cover_high']}) == \
        ("Paused · Cloud cover high", 'warn')


def test_learning_line():
    snap = {'enabled': True, 'baseline_status': {'state': 'learning', 'samples': 12,
                                                 'needed': 30, 'bin': 'moon_down'},
            'stars': {'count': 210, 'baseline': None, 'change_pct': -45.0, 'window_frames': 10},
            'roof': {'state': 'Open'}}
    assert baseline_learning_text(snap).startswith("Learning your sky (12/30 clear frames")
    text = detail_lines(snap)
    assert "Stars: 210" in text and "-45%" in text and "Learning your sky" in text
    assert baseline_learning_text({'baseline_status': {'state': 'ready'}}) == ""


def test_controller_saves_refreshes_and_resets(card, tmp_path):
    class Window:
        config = Config(str(tmp_path / 'config.json'))

    class Strip:
        seen = None

        def set_imaging(self, text, tone):
            self.seen = (text, tone)

    store = ConditionsStore()
    strip = Strip()
    controller = ImagingConditionsController(Window(), store=store, parent=card)
    controller.bind(card, strip)
    assert strip.seen == ("Warming up", 'info')
    assert card.verdict_label.text() == "Imaging: Warming up"

    card.cloud_spin.setValue(65)
    block = Config(str(tmp_path / 'config.json')).get('imaging_conditions')
    assert block['max_cloud_cover_pct'] == 65.0 and block['trend_frames'] == 10

    card.enabled_switch.set_checked(False)
    assert store.snapshot()['reasons'] == ['disabled']
    assert strip.seen == ("Off", 'muted')

    baseline = get_imaging_conditions_feed().baseline
    baseline.add('moon_down', 300)
    card.reset_button.click()
    assert baseline.sample_count('moon_down') == 0


def test_status_strip_has_an_imaging_tile(qapp):
    from ui.components.status_strip import StatusStrip
    strip = StatusStrip()
    assert strip.imaging_tile.value.text() == "—"
    strip.set_imaging("OK", 'ok')
    assert strip.imaging_tile.value.text() == "OK"
    strip.deleteLater()
