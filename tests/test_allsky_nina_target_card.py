"""NinaTargetCard — the All-Sky page's controls for the NINA target layer
(issue #137): load sets controls silently, a user change emits once, and
values() carries exactly the four keys the panel merges into its config."""
import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QApplication

from services.config_defaults import DEFAULT_CONFIG
from ui.panels.allsky_nina_target_card import NinaTargetCard

DEFAULTS = DEFAULT_CONFIG['allsky_overlay']['nina_target']


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def card(qapp):
    widget = NinaTargetCard()
    yield widget
    widget.close()
    widget.deleteLater()
    qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    qapp.processEvents()


def _record(card):
    hits = []
    card.changed.connect(lambda: hits.append(1))
    return hits


def test_load_then_values_round_trips_the_four_controlled_keys(card):
    layer = dict(DEFAULTS, enabled=False, show_fov=False, color='#44DDFF',
                 stale_after_s=600, opacity=12)
    card.load(layer)
    assert card.values() == {
        'enabled': False, 'show_fov': False, 'color': '#44DDFF', 'stale_after_s': 600}


def test_a_fresh_card_shows_the_defaults(card):
    assert card.values() == {key: DEFAULTS[key]
                             for key in ('enabled', 'show_fov', 'color', 'stale_after_s')}


def test_load_does_not_emit(card):
    hits = _record(card)
    card.load(dict(DEFAULTS, enabled=False, show_fov=False, color='#FFFFFF',
                   stale_after_s=900))
    card.load({})
    card.load(None)
    assert hits == []


@pytest.mark.parametrize("change", [
    lambda c: c._enabled.set_checked(False),
    lambda c: c._show_fov.set_checked(False),
    lambda c: c._color._select('#88FF44'),
    lambda c: c._stale.setValue(300),
])
def test_each_control_emits_changed_exactly_once(card, qapp, change):
    hits = _record(card)
    change(card)
    qapp.processEvents()
    assert hits == [1]


@pytest.mark.parametrize("given, shown", [
    (5, 30), (29.6, 30), (4000, 3600), (95.4, 95),
    ('abc', DEFAULTS['stale_after_s']), (None, DEFAULTS['stale_after_s']),
    (float('inf'), DEFAULTS['stale_after_s']),
])
def test_hide_after_is_clamped_to_its_range(card, given, shown):
    card.load({'stale_after_s': given})
    assert card.values()['stale_after_s'] == shown


def test_the_spin_box_itself_refuses_values_outside_30_to_3600(card):
    card._stale.setValue(1)
    assert card.values()['stale_after_s'] == 30
    card._stale.setValue(99999)
    assert card.values()['stale_after_s'] == 3600


@pytest.mark.parametrize("colour", ['not a colour', '#GGGGGG', '', 42, None])
def test_an_unusable_colour_falls_back_to_the_default(card, colour):
    card.load({'color': colour})
    assert card.values()['color'] == DEFAULTS['color']


@pytest.mark.parametrize("colour", ['red', '#abc', '#4488ff00'])
def test_a_colour_the_renderer_cannot_read_falls_back(card, colour):
    card.load({'color': colour})
    assert card.values()['color'] == DEFAULTS['color']


@pytest.mark.parametrize("colour", ['#4488ff', '#4488FF'])
def test_a_six_digit_hex_colour_is_kept_as_given(card, colour):
    card.load({'color': colour})
    assert card.values()['color'] == colour


def test_the_default_colour_is_one_the_picker_offers():
    from ui.panels.allsky_settings_rows import OVERLAY_PALETTE

    assert DEFAULTS['color'] in [hex_color for hex_color, _ in OVERLAY_PALETTE]
