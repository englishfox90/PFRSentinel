"""Sensitivity presets: Balanced is exactly the old defaults, each preset's
star floor follows its cloud line, a block reads back as its preset or
Custom; on the card, picking a preset rewrites the keys and editing any
number shows Custom (offscreen Qt)."""
import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QApplication

from services.config_defaults import DEFAULT_CONFIG
from services.imaging_conditions import read_settings
from services.imaging_conditions.presets import (
    BALANCED, CUSTOM, PRESET_KEYS, PRESETS, RELAXED, STRICT, preset_for, preset_values,
    star_floor_for)

# Today's numbers, written out: a change to DEFAULT_CONFIG that moved them
# would change how every existing install behaves.
OLD_DEFAULTS = {'bad_frames': 3, 'good_frames': 3, 'max_cloud_cover_pct': 50.0,
                'min_star_ratio': 0.4, 'star_drop_pct': 40.0, 'fwhm_rise_pct': 50.0}


def test_balanced_is_the_old_defaults_exactly():
    assert PRESETS[BALANCED] == OLD_DEFAULTS
    block = DEFAULT_CONFIG['imaging_conditions']
    assert {k: block[k] for k in PRESET_KEYS} == OLD_DEFAULTS
    assert block['sensitivity'] == BALANCED
    assert preset_for({}) == BALANCED
    assert preset_for(block) == BALANCED


def test_settings_never_read_the_preset_name():
    strict_named = dict(DEFAULT_CONFIG['imaging_conditions'], sensitivity=STRICT)
    assert read_settings({'imaging_conditions': strict_named}) == read_settings({})


def test_every_preset_keeps_the_star_floor_under_its_cloud_line():
    for name, values in PRESETS.items():
        assert values['min_star_ratio'] == star_floor_for(values['max_cloud_cover_pct']), name
        # The cloud line fires first; "too few stars" is the deeper reason.
        assert values['min_star_ratio'] < 1 - values['max_cloud_cover_pct'] / 100, name


def test_presets_are_ordered_from_relaxed_to_strict():
    r, b, s = PRESETS[RELAXED], PRESETS[BALANCED], PRESETS[STRICT]
    assert r['bad_frames'] > b['bad_frames'] > s['bad_frames']
    assert r['good_frames'] < b['good_frames'] < s['good_frames']
    for key in ('max_cloud_cover_pct', 'star_drop_pct', 'fwhm_rise_pct'):
        assert r[key] > b[key] > s[key], key
    # Before the baseline is learned the ML class decides: Relaxed keeps
    # imaging through Partly Cloudy (60 %), the others pause for it.
    assert r['max_cloud_cover_pct'] > 60 > b['max_cloud_cover_pct']


def test_star_floor_bounds():
    assert star_floor_for(95) == 0.05 and star_floor_for(0) == 0.9
    assert star_floor_for('garbage') == 0.4 and star_floor_for(float('nan')) == 0.4


def test_block_reads_as_custom_when_any_number_moves():
    for name in PRESETS:
        block = preset_values(name)
        assert preset_for(block) == name
        block['star_drop_pct'] += 1
        assert preset_for(block) == CUSTOM
    assert preset_values('nonsense') == PRESETS[BALANCED]


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


@pytest.fixture
def card(qapp):
    from ui.panels.imaging_conditions_card import ImagingConditionsCard
    widget = ImagingConditionsCard()
    yield widget
    widget.close()
    widget.deleteLater()
    qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def _pick(card, name):
    from ui.panels.imaging_conditions_card import SENSITIVITY_ITEMS
    card.sensitivity_combo.setCurrentIndex(SENSITIVITY_ITEMS.index(name))


def test_card_shows_balanced_for_old_configs(card):
    card.load({'max_cloud_cover_pct': 50, 'min_star_ratio': 0.4})
    assert card.sensitivity() == BALANCED
    assert card.values()['sensitivity'] == BALANCED
    assert "3 bad frames" in card.sensitivity_hint.text()


def test_picking_a_preset_writes_every_key_once(card):
    hits = []
    card.changed.connect(lambda: hits.append(1))
    _pick(card, STRICT)
    assert len(hits) == 1
    values = card.values()
    assert {k: values[k] for k in PRESET_KEYS} == PRESETS[STRICT]
    assert values['sensitivity'] == STRICT
    _pick(card, RELAXED)
    assert {k: card.values()[k] for k in PRESET_KEYS} == PRESETS[RELAXED]


def test_editing_a_number_shows_custom_and_editing_back_restores(card):
    card.load({})
    card.drop_spin.setValue(31)
    assert card.sensitivity() == CUSTOM and card.values()['sensitivity'] == CUSTOM
    card.drop_spin.setValue(40)
    assert card.sensitivity() == BALANCED


def test_cloud_limit_carries_the_star_floor(card):
    card.load({})
    card.cloud_spin.setValue(30)
    assert card.values()['min_star_ratio'] == 0.6
    assert "60%" in card.star_floor_label.text()
    assert card.sensitivity() == CUSTOM  # 30 % cloud alone is not Strict


def test_picking_custom_opens_advanced_and_changes_nothing(card):
    card.load({})
    before = card.values()
    hits = []
    card.changed.connect(lambda: hits.append(1))
    _pick(card, CUSTOM)
    assert hits == [] and card.values() == before
    assert not card.advanced_box.isHidden()
    assert card.sensitivity() == BALANCED  # the numbers still match Balanced


def test_a_hand_edited_ratio_survives_load_and_save(card):
    card.load({'min_star_ratio': 0.33})
    assert card.values()['min_star_ratio'] == 0.33 and card.sensitivity() == CUSTOM
