"""target_moon_text — "Target M 15 is 18° from an 87% Moon": shown with the
Moon up (or unknown), hidden with it down or with no target, cautioned only
inside the distance that grows with the Moon; the stored line of a library
frame; the Moon-bin names old and new; the views that show it (offscreen Qt)."""
import pytest

from ui.components import target_moon_text as T
from ui.components.conditions_live_text import learning_progress
from ui.components.imaging_conditions_text import (
    baseline_learning_text, detail_lines, moon_bin_text,
)
from ui.components.library_grade_text import frame_grade_details


def snap(sep=18.0, name='M 15', up=True, illum=87.0, moon_alt=62.0, target_alt=71.0,
         **extra):
    base = {
        'enabled': True, 'updated_at': '2026-11-24T03:00:00Z', 'frames_considered': 5,
        'imaging_ok': True, 'reasons': [], 'stale': False,
        'stars': {'count': 120, 'baseline': 140.0},
        'moon': {'up': up, 'illumination_pct': illum, 'altitude_deg': moon_alt},
        'baseline_bin': 'moon_up_75_100_high',
        'baseline_status': {'state': 'ready', 'samples': 90, 'needed': 30, 'nights': 3,
                            'bin': 'moon_up_75_100_high'},
        'target': None if sep is None else {
            'name': name, 'moon_separation_deg': sep, 'altitude_deg': target_alt},
    }
    base.update(extra)
    return base


def test_the_line_and_its_caution():
    assert T.target_moon_text(snap()) == ("Target M 15 is 18° from an 87% Moon", True)
    assert T.target_moon_text(snap(sep=95.4)) == ("Target M 15 is 95° from an 87% Moon", False)
    assert T.target_moon_text(snap(illum=62.0, sep=30.0)) == \
        ("Target M 15 is 30° from a 62% Moon", True)


@pytest.mark.parametrize("pct, article", [(8, 'an'), (11, 'an'), (18, 'an'), (80, 'an'),
                                           (89, 'an'), (7, 'a'), (12, 'a'), (90, 'a'),
                                           (100, 'a'), (81, 'an')])
def test_article(pct, article):
    assert f"from {article} {pct}% Moon" in T.target_moon_text(snap(illum=float(pct)))[0]


def test_nothing_to_say():
    assert T.target_moon_text(snap(sep=None)) == ("", False)          # no target
    assert T.target_moon_text(snap(up=False)) == ("", False)          # Moon down
    assert T.target_moon_text(snap(enabled=False)) == ("", False)
    assert T.target_moon_text(None) == ("", False)
    assert T.target_moon_text({'target': {'name': 'x'}}) == ("", False)


def test_unknown_moon_state_is_shown_without_a_caution():
    text, close = T.target_moon_text(snap(up=None, illum=None, sep=10.0, name=''))
    assert text == "The target is 10° from the Moon" and close is False


def test_detail_line_adds_the_altitudes():
    assert T.target_moon_detail(snap()) == (
        "Target M 15 is 18° from an 87% Moon (target at 71°, Moon at 62° altitude) — close")
    assert T.target_moon_detail(snap(sep=95.0, target_alt=None, moon_alt=None)) == \
        "Target M 15 is 95° from an 87% Moon"
    assert T.target_moon_detail(snap(sep=None)) == ""
    assert "Target M 15 is 18°" in detail_lines(snap())
    assert "Target" not in detail_lines(snap(sep=None))


def test_stored_separation_line():
    assert T.stored_separation_text(18.4, 'moon_up_75_100_high') == "Target 18° from the Moon"
    assert T.stored_separation_text(18.4, 'moon_down') == ""
    assert T.stored_separation_text(None, 'moon_up_0_25') == ""
    assert T.stored_separation_text(True) == ""


def test_library_frame_shows_it_and_old_rows_do_not():
    row = {'imaging_ok': 1, 'frame_ok': 1, 'imaging_reasons': '', 'clear_sky_ratio': 0.8,
           'moon_bin': 'moon_up_75_100_high', 'moon_separation_deg': 18.2}
    assert frame_grade_details(row) == [
        "Stars at 80% of clear sky · Moon high, 75–100% lit", "Target 18° from the Moon"]
    old = dict(row, moon_bin='moon_up_75_100')
    del old['moon_separation_deg']
    assert frame_grade_details(old) == ["Stars at 80% of clear sky · Moon up, 75–100% lit"]


def test_every_bin_has_wording_old_names_included():
    from services.imaging_conditions.moon_bins import MOON_BINS
    for name in MOON_BINS:
        assert moon_bin_text(name), name
    assert moon_bin_text('moon_up_50_75_low') == "Moon low, 50–75% lit"
    assert moon_bin_text('moon_up_75_100') == "Moon up, 75–100% lit"   # stored before the split


def test_learning_lines_read_naturally_for_a_split_bin():
    learning = snap(baseline_status={'state': 'learning', 'samples': 12, 'needed': 30,
                                     'nights': 0, 'bin': 'moon_up_75_100_high'})
    assert baseline_learning_text(learning) == (
        "Learning your sky: 12/30 clear frames (Moon high, 75–100% lit)")
    assert learning_progress(learning)[0] == (
        "Learning your clear sky with the Moon high, 75–100% lit — 12 of 30 clear frames")
    assert learning_progress(snap())[0].startswith(
        "Clear-sky baseline learned with the Moon high, 75–100% lit: 140 stars")


# -- widgets -----------------------------------------------------------------

@pytest.fixture(scope="module")
def qt_app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def test_live_panel_shows_the_target_line_with_a_gentle_caution(qt_app):
    from ui.panels.conditions_live_panel import ConditionsLivePanel
    from ui.theme.tokens import Colors
    panel = ConditionsLivePanel()
    panel.set_view(snap())
    assert panel.target_label.text() == "Target M 15 is 18° from an 87% Moon"
    assert not panel.target_label.isHidden()
    assert Colors.warning_text in panel.target_label.styleSheet()
    assert panel.target_label.toolTip() == T.CLOSE_HINT
    panel.set_view(snap(sep=95.0))
    assert Colors.warning_text not in panel.target_label.styleSheet()
    assert panel.target_label.toolTip() == ""
    panel.set_view(snap(sep=None))
    assert panel.target_label.isHidden()
    panel.deleteLater()


def test_card_shows_the_target_line(qt_app):
    from ui.panels.imaging_conditions_card import ImagingConditionsCard
    card = ImagingConditionsCard()
    card.show_conditions(snap())
    assert card.target_label.text() == "Target M 15 is 18° from an 87% Moon"
    assert not card.target_label.isHidden()
    card.show_conditions(snap(up=False))
    assert card.target_label.isHidden()
    card.deleteLater()


def test_imaging_tile_tooltip_carries_the_detail(qt_app):
    from ui.components.status_strip import StatusStrip
    strip = StatusStrip()
    base = strip.imaging_tile.toolTip()
    strip.set_imaging("OK", 'ok')
    strip.set_imaging_detail(T.target_moon_detail(snap()))
    assert "Target M 15 is 18° from an 87% Moon" in strip.imaging_tile.toolTip()
    assert base in strip.imaging_tile.toolTip()
    strip.set_imaging_detail("")
    assert strip.imaging_tile.toolTip() == base
    strip.deleteLater()
