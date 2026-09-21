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


def test_results_without_a_static_verdict_behave_as_before(strip):
    strip.update_from_metadata({'_ML_RESULTS': {'roof_status': 'Closed'}})
    assert strip.roof_tile.value.text() == "Closed"
    assert strip.sky_tile.value.text() == "Roof closed"


def test_warning_icon_exists_in_the_bundled_font(qapp):
    # qicon swallows an unknown glyph and returns an empty QIcon, so a typo
    # would ship as a blank tile rather than an error.
    assert not qicon('alert-outline').isNull()
