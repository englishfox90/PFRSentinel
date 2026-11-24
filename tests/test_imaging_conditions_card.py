"""ImagingConditionsCard + ImagingConditionsController + the IMAGING tile:
load is silent, edits emit once and values() carries the controlled keys
(the sensitivity preset with them), the controller saves them into the
config block and refreshes the card and the status strip from the store,
the learning line, and Reset baseline forgets the learned counts (offscreen
Qt)."""
import os

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QApplication

from services.config import Config
from services.imaging_conditions import ConditionsStore, get_imaging_conditions_feed
from ui.components.imaging_conditions_text import (
    as_of_text, baseline_learning_text, detail_lines, secondary_text, signed_pct,
    summarize_conditions)
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
           'min_star_ratio': 0.6, 'bad_frames': 5, 'good_frames': 2,
           'star_drop_pct': 30, 'fwhm_rise_pct': 70}
    card.load(cfg)
    assert hits == []
    assert card.values() == dict(cfg, max_cloud_cover_pct=35.0, star_drop_pct=30.0,
                                 fwhm_rise_pct=70.0, sensitivity='custom')


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
    assert summarize_conditions({'enabled': True, 'stale': True, 'reasons': ['stale']}) == \
        ("Paused · No recent frames", 'warn')
    assert summarize_conditions({'enabled': True, 'reasons': ['warming_up']}) == (
        "Waiting for first frame", 'info')
    assert summarize_conditions({'enabled': True, 'reasons': ['cloud_cover_high']}) == \
        ("Paused · Cloud cover high", 'warn')


def test_learning_line():
    snap = {'enabled': True, 'frames_considered': 12,
            'baseline_status': {'state': 'learning', 'samples': 12,
                                                 'needed': 30, 'bin': 'moon_down'},
            'stars': {'count': 210, 'baseline': None, 'change_pct': -45.0, 'window_frames': 10},
            'roof': {'state': 'Open'}}
    assert baseline_learning_text(snap) == \
        "Learning your sky: 12/30 clear frames (Moon down)"
    snap['baseline_status']['bin'] = 'moon_up_25_50'
    assert baseline_learning_text(snap).endswith("(Moon up, 25–50% lit)")
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
    assert strip.seen == ("Idle", 'muted')
    assert card.verdict_label.text() == "Now: Idle"
    Window.is_capturing = True
    controller.refresh()
    assert strip.seen == ("Waiting for first frame", 'info')
    assert card.verdict_label.text() == "Now: Waiting for first frame"
    # No good-frame count: the first good frame is enough at a start.
    assert card.reasons_label.text() == "No frame judged yet, so NINA is not held back"

    card.cloud_spin.setValue(65)
    block = Config(str(tmp_path / 'config.json')).get('imaging_conditions')
    assert block['max_cloud_cover_pct'] == 65.0 and block['trend_frames'] == 10
    assert block['min_star_ratio'] == 0.25 and block['sensitivity'] == 'custom'

    card.enabled_switch.set_checked(False)
    assert store.snapshot()['reasons'] == ['disabled']
    assert strip.seen == ("Off", 'muted')

    baseline = get_imaging_conditions_feed().baseline
    baseline.add('moon_down', 300)
    controller.save_learning()          # capture stop: the night in progress is written
    assert os.path.isfile(baseline.path)
    card.reset_button.click()
    assert baseline.sample_count('moon_down') == 0 and not os.path.exists(baseline.path)


def test_status_strip_has_an_imaging_tile(qapp):
    from ui.components.status_strip import StatusStrip
    strip = StatusStrip()
    assert strip.imaging_tile.value.text() == "—"
    strip.set_imaging("OK", 'ok')
    assert strip.imaging_tile.value.text() == "OK"
    strip.deleteLater()


def _snap(**kw):
    base = {'enabled': True, 'imaging_ok': False, 'reasons': [], 'stale': False,
            'updated_at': '2026-11-23T02:24:19.000Z', 'age_s': 3.0, 'frames_considered': 40,
            'good_streak': 0, 'stars': {'count': 300, 'baseline': 315.0, 'change_pct': 2.0},
            'baseline_status': {'state': 'ready', 'samples': 40, 'needed': 30,
                                'bin': 'moon_down'},
            'roof': {'state': 'Open'}, 'sky': {'condition': 'Clear'}}
    base.update(kw)
    return base


def test_change_that_rounds_to_zero_reads_plus_minus_zero():
    assert signed_pct(-0.3) == "±0%" and signed_pct(0.4) == "±0%" and signed_pct(0) == "±0%"
    assert signed_pct(-0.6) == "-1%" and signed_pct(12.4) == "+12%"
    snap = _snap(stars={'count': 317, 'change_pct': -0.2},
                 fwhm={'px': 2.4, 'night_median_px': 2.4, 'change_pct': -0.4})
    text = detail_lines(snap)
    assert "-0%" not in text and "+0%" not in text and "sharp nights" not in text
    snap['fwhm'].update(reference_px=2.62, reference_nights=8)
    assert "; your sharp nights 2.6 px" in detail_lines(snap)
    assert "317, ±0% vs recent frames" in text and "(±0%)" in text


def test_headline_reason_is_not_repeated():
    snap = _snap(reasons=['cloud_cover_high', 'stars_dropping'])
    assert summarize_conditions(snap)[0] == "Paused · Cloud cover high"
    assert secondary_text(snap) == "Also: Stars dropping"
    assert secondary_text(_snap(reasons=['cloud_cover_high'])) == ""
    recovering = _snap(reasons=['recovering'], good_streak=2)
    assert summarize_conditions(recovering) == ("Recovering", 'info')
    assert "Recovering" not in secondary_text(recovering)
    assert secondary_text(recovering).startswith("2 good frames in a row")
    assert secondary_text(recovering, good_needed=3) == "2 of 3 good frames in a row to resume"
    stale = _snap(stale=True, reasons=['stale'])
    assert "No recent frames" not in secondary_text(stale)
    assert secondary_text(_snap(imaging_ok=True)) == ""


def test_stale_reads_not_ok_and_dates_the_measurements():
    stale = _snap(stale=True, reasons=['stale'], age_s=150.0)
    assert summarize_conditions(stale, capturing=True) == ("Paused · No recent frames", 'warn')
    assert summarize_conditions(stale, capturing=False) == ("Paused · Capture stopped", 'muted')
    note = as_of_text(stale)
    assert note.startswith("From the last frame at ") and "(2 min ago)" in note
    assert detail_lines(stale).splitlines()[0] == note
    assert as_of_text(_snap()) == "" and not detail_lines(_snap()).startswith("From")


def test_idle_until_capture_runs():
    cold = {'enabled': True, 'imaging_ok': False, 'reasons': ['warming_up'], 'stale': False,
            'updated_at': None, 'frames_considered': 0}
    assert summarize_conditions(cold, capturing=False) == ("Idle", 'muted')
    assert secondary_text(cold, capturing=False) == ""
    assert detail_lines(cold) == ""
    assert summarize_conditions(cold, capturing=True) == ("Waiting for first frame", 'info')
    assert summarize_conditions(cold) == ("Waiting for first frame", 'info')
    for text in (secondary_text(cold, capturing=True), secondary_text(cold)):
        assert text == "No frame judged yet, so NINA is not held back"
        assert "good frame" not in text
    # A start under a bad sky is `recovering`, and that keeps its count.
    recovering = dict(cold, reasons=['recovering'], frames_considered=2, good_streak=2,
                      updated_at='2026-11-23T02:24:19Z')
    assert summarize_conditions(recovering, capturing=True) == ("Recovering", 'info')
    assert secondary_text(recovering, True, 3) == "2 of 3 good frames in a row to resume"


def test_off_and_stale_are_no_verdict_for_nina_not_a_refusal(qapp):
    """The plugin reads `disabled` and `stale` as no verdict, which its own
    "No verdict = bad" setting decides — never "told not to image"."""
    card = ImagingConditionsCard()
    from qfluentwidgets import CaptionLabel
    texts = " ".join(label.text() for label in card.findChildren(CaptionLabel))
    assert "NINA gets no verdict (reason: disabled)" in texts and "No verdict = bad" in texts
    assert "told not to image" not in texts
    stale = _snap(stale=True, reasons=['stale'])
    assert secondary_text(stale) == "NINA gets no verdict until frames arrive again"
    card.deleteLater()


def test_learning_line_hidden_in_daylight_and_without_a_star_count():
    learning = {'state': 'learning', 'samples': 0, 'needed': 30, 'bin': 'moon_down'}
    day = _snap(reasons=['daylight', 'roof_not_open'], baseline_status=learning,
                stars={'count': None})
    assert baseline_learning_text(day) == "" and "Learning" not in detail_lines(day)
    night_no_stars = _snap(baseline_status=learning, stars={'count': None})
    assert baseline_learning_text(night_no_stars) == ""
    assert baseline_learning_text(_snap(baseline_status=learning)) != ""


def test_card_hides_empty_lines_and_explains_the_star_count(card):
    card.show_conditions(_snap(imaging_ok=True, cloud_cover_pct=5.0))
    assert card.reasons_label.isHidden() and card.reasons_label.text() == ""
    assert "same count as the SEEING tile" in " ".join(card.cloud_spin.toolTip().split())
    card.show_conditions(_snap(reasons=['cloud_cover_high', 'stars_dropping']))
    assert not card.reasons_label.isHidden()
    assert card.reasons_label.text() == "Also: Stars dropping"
    card.show_conditions({'enabled': False})
    assert card.reasons_label.isHidden()


def test_row_labels_fit_their_column(card):
    from qfluentwidgets import BodyLabel
    from ui.components.cards import FormRow
    rows = card.findChildren(FormRow)
    assert len(rows) >= 6
    for row in rows:
        label = row.findChild(BodyLabel)
        metrics = label.fontMetrics()
        if metrics.horizontalAdvance(label.text()) <= label.width():
            continue
        # Wider fonts (Windows' Segoe UI) wrap the label onto more lines; it is
        # never clipped as long as each word fits the column.
        assert label.wordWrap(), f"{label.text()!r} would be clipped"
        for word in label.text().split():
            width = metrics.horizontalAdvance(word)
            assert width <= label.width(), f"{word!r} is {width}px in {label.width()}px"
