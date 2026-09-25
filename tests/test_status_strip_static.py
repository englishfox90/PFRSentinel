"""StatusStrip — the "too much static" warning on the roof and sky tiles.

The warning flags a frame that is only sensor noise. It must never replace the
roof model's reading: the static score is unproven, so the tile keeps saying
what the model said and marks it unreliable.
"""
import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QApplication

from ui.components.status_strip import StatusStrip
from ui.theme.icons import qicon


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def strip(qapp):
    widget = StatusStrip()
    yield widget
    widget.close()
    widget.deleteLater()
    qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    qapp.processEvents()


def _frame(roof, static, sky='Clear'):
    return {'_ML_RESULTS': {'roof_status': roof, 'sky_condition': sky,
                            'frame_is_static': static, 'static_ratio': 0.26},
            'STAR_COUNT': 41}


def test_static_frame_keeps_the_roof_reading_and_flags_it(strip):
    strip.update_from_metadata(_frame('Open', static=True))
    assert strip.roof_tile.value.text() == "Open · unreliable"
    assert strip.roof_tile._tone == 'warn'
    assert strip.sky_tile.value.text() == "Too much static"
    assert strip.sky_tile._tone == 'warn'
    assert "sensor noise" in strip.roof_tile.toolTip()
    assert "sensor noise" in strip.sky_tile.toolTip()


def test_static_frame_shows_no_star_count(strip):
    strip.update_from_metadata(_frame('Open', static=True))
    assert "stars" not in strip.seeing_tile.value.text()


def test_static_closed_roof_is_flagged_too(strip):
    strip.update_from_metadata(_frame('Closed', static=True))
    assert strip.roof_tile.value.text() == "Closed · unreliable"
    assert strip.seeing_tile.value.text() == "Roof closed"


def test_warning_clears_on_the_next_good_frame(strip):
    strip.update_from_metadata(_frame('Open', static=True))
    strip.update_from_metadata(_frame('Open', static=False))
    assert strip.roof_tile.value.text() == "Open"
    assert strip.roof_tile._tone == 'ok'
    assert strip.roof_tile._icon_name == 'home-roof'
    assert strip.sky_tile.value.text() == "Clear"
    assert strip.sky_tile._icon_name == 'weather-partly-cloudy'
    assert strip.roof_tile.toolTip() == ""
    assert strip.seeing_tile.value.text() == "41 stars"


def _no_verdict_frame(static=False):
    # What ml_service reports when the roof classifier is off or its
    # prediction threw: no roof verdict, and the sky is never judged.
    return {'_ML_RESULTS': {'roof_status': 'N/A', 'sky_condition': 'N/A',
                            'frame_is_static': static, 'static_ratio': 0.9}}


def test_warning_clears_when_the_next_good_frame_has_no_roof_verdict(strip):
    strip.update_from_metadata(_frame('Open', static=True))
    strip.update_from_metadata(_no_verdict_frame())
    assert strip.roof_tile.value.text() == "—"
    assert strip.roof_tile._tone == 'muted'
    assert strip.roof_tile._icon_name == 'home-roof'
    assert strip.sky_tile.value.text() == "—"
    assert strip.sky_tile._icon_name == 'weather-partly-cloudy'
    assert strip.roof_tile.toolTip() == ""
    assert strip.sky_tile.toolTip() == ""


def test_warning_clears_when_the_sky_classifier_is_off(strip):
    # Roof back with a real reading, but sky stays N/A because the sky model
    # is disabled: the Sky tile must not keep saying "Too much static".
    strip.update_from_metadata(_frame('Open', static=True))
    strip.update_from_metadata(_frame('Open', static=False, sky='N/A'))
    assert strip.roof_tile.value.text() == "Open"
    assert strip.roof_tile._tone == 'ok'
    assert strip.sky_tile.value.text() == "—"
    assert strip.sky_tile._tone == 'muted'


def test_warning_clears_on_a_frame_with_no_ml_results_at_all(strip):
    strip.update_from_metadata(_frame('Closed', static=True))
    strip.update_from_metadata({'STAR_COUNT': 'N/A'})
    assert strip.roof_tile.value.text() == "—"
    assert strip.sky_tile.value.text() == "—"


def test_a_frame_without_a_verdict_keeps_an_earlier_good_reading(strip):
    # The reset is for leaving the static state only. Between good frames a
    # no-verdict frame still leaves the last reading up, as it always has.
    strip.update_from_metadata(_frame('Open', static=False))
    strip.update_from_metadata(_no_verdict_frame())
    assert strip.roof_tile.value.text() == "Open"
    assert strip.sky_tile.value.text() == "Clear"

    strip.update_from_metadata(_frame('Open', static=True))
    strip.update_from_metadata(_no_verdict_frame())
    strip.update_from_metadata(_frame('Open', static=False))
    strip.update_from_metadata(_no_verdict_frame())
    assert strip.roof_tile.value.text() == "Open"
    assert strip.sky_tile.value.text() == "Clear"


def test_results_without_a_static_verdict_behave_as_before(strip):
    strip.update_from_metadata({'_ML_RESULTS': {'roof_status': 'Closed'}})
    assert strip.roof_tile.value.text() == "Closed"
    assert strip.sky_tile.value.text() == "Roof closed"


def test_warning_icon_exists_in_the_bundled_font(qapp):
    # qicon swallows an unknown glyph and returns an empty QIcon, so a typo
    # would ship as a blank tile rather than an error.
    assert not qicon('alert-outline').isNull()


# --- observable-sky gate reasons on the Sky tile (issue #93) ---------------

def _gated(reason, roof='Open', sky='Clear', ml=True):
    metadata = {'_observing_window_reason': reason, 'STAR_COUNT': 'N/A'}
    if ml:
        metadata['_ML_RESULTS'] = {'roof_status': roof, 'sky_condition': sky,
                                   'frame_is_static': False}
    return metadata


def test_no_stars_takes_the_sky_tile(strip):
    strip.update_from_metadata(_frame('Open', static=False))
    strip.update_from_metadata(_gated('no_stars'))
    assert strip.sky_tile.value.text() == "No stars detected"
    assert strip.sky_tile._tone == 'muted'
    assert strip.seeing_tile.value.text() == "—"
    assert strip.roof_tile.value.text() == "Open"


def test_exposure_too_short_takes_the_sky_tile(strip):
    strip.update_from_metadata(_gated('exposure'))
    assert strip.sky_tile.value.text() == "Exposure too short"
    assert strip.seeing_tile.value.text() == "—"


def test_the_reason_shows_in_watch_mode_without_ml_results(strip):
    strip.update_from_metadata(_gated('no_stars', ml=False))
    assert strip.sky_tile.value.text() == "No stars detected"


def test_a_closed_roof_is_still_reported_as_the_roof(strip):
    strip.update_from_metadata(_gated('roof', roof='Closed'))
    assert strip.sky_tile.value.text() == "Roof closed"
    assert strip.seeing_tile.value.text() == "Roof closed"


def test_an_observable_frame_restores_the_sky_condition_and_stars(strip):
    strip.update_from_metadata(_gated('no_stars'))
    strip.update_from_metadata(_frame('Open', static=False))
    assert strip.sky_tile.value.text() == "Clear"
    assert strip.seeing_tile.value.text() == "41 stars"


def test_static_still_wins_over_the_gate_reason(strip):
    metadata = _gated('static')
    metadata['_ML_RESULTS']['frame_is_static'] = True
    strip.update_from_metadata(metadata)
    assert strip.sky_tile.value.text() == "Too much static"
