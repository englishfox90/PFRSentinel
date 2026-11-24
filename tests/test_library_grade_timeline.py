"""The imaging-conditions lane of the Library night scrubber, and the grade
block following the frame shown: every way of moving between frames updates
the block, the lane colours each frame OK / not OK / not graded with the
clear-sky ratio as a trace and a marker on the frame shown, a night with no
graded frames has no lane, and scrubbing never re-renders the lane."""
from datetime import datetime

import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QColor, QMouseEvent
from PySide6.QtTest import QTest

from services.library import events as E
from services.library import grade_timeline as GT
from services.library import sessions as S
from services.library.grade_summary import summarize_grades
from ui.theme.tokens import Colors

NIGHT = int(datetime(2026, 7, 3, 22, 0).timestamp())
TRACK_W = 1000


def row(id_, **grade):
    base = {'id': id_, 'captured_at': NIGHT + id_ * 30, 'roof': 'Open',
            'condition': 'Clear', 'clouds': 0}
    base.update(grade)
    return base


def graded(id_, ok=True, reasons='', ratio=0.9):
    return row(id_, imaging_ok=int(ok), frame_ok=int(ok), imaging_reasons=reasons,
               clear_sky_ratio=ratio, moon_bin='moon_down')


def night(n=100):
    """Clear, then cloud for the middle 40 %, clear again, the last tenth ungraded."""
    rows = []
    for i in range(n):
        if i >= n * 0.9:
            rows.append(row(i))
        elif n * 0.3 <= i < n * 0.7:
            rows.append(graded(i, ok=False, reasons='cloud_cover_high,stars_low', ratio=0.2))
        else:
            rows.append(graded(i, ratio=0.95))
    return rows


@pytest.fixture(scope="module")
def qt_app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


@pytest.fixture
def make_widget(qt_app):
    created = []

    def _make(ctor):
        widget = ctor()
        created.append(widget)
        return widget

    yield _make
    for widget in created:
        timer = getattr(widget, "_timer", None)
        if timer is not None:
            timer.stop()
        widget.close()
        widget.deleteLater()
    qt_app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    qt_app.processEvents()


def open_night(make_widget, frames, gaps=()):
    from ui.panels.library_night_view import NightView
    view = make_widget(NightView)
    session = {'key': S.night_key(NIGHT), 'gaps': list(gaps), 'max_gap_seconds': 0,
               **summarize_grades(frames)}
    view.begin_load(session)
    view.set_frames({'session': session, 'frames': frames, 'filmstrip': []})
    return view


def make_scrubber(make_widget, frames, events=()):
    from ui.panels import library_scrubber as SC
    scrubber = make_widget(SC.Scrubber)
    scrubber.set_data(frames, [], list(events))
    scrubber.resize(TRACK_W + 2 * SC._PAD_X, scrubber.height())
    return scrubber


def x_of(index, count):
    from ui.panels.library_scrubber import _PAD_X
    return _PAD_X + round(index / (count - 1) * TRACK_W)


def pixel(widget, x, y):
    return widget.grab().toImage().pixelColor(int(x), int(y))


def near(color, token, tolerance=40):
    want = QColor(token)
    return all(abs(a - b) <= tolerance for a, b in (
        (color.red(), want.red()), (color.green(), want.green()), (color.blue(), want.blue())))


def headline(view):
    return view.grade_block.headline.text()


# -- the timeline data -----------------------------------------------------------

def test_states_segments_and_trace():
    rows = [graded(0), graded(1), graded(2, ok=False, reasons='stars_low', ratio=None),
            graded(3, ok=False, reasons='stars_low', ratio=0.3), row(4)]
    timeline = GT.build_grade_timeline(rows)
    assert timeline['graded'] is True
    assert timeline['states'] == [GT.GRADE_OK, GT.GRADE_OK, GT.GRADE_NOT_OK,
                                  GT.GRADE_NOT_OK, GT.GRADE_UNGRADED]
    assert [s for _, _, s in timeline['segments']] == [
        GT.GRADE_OK, GT.GRADE_NOT_OK, GT.GRADE_UNGRADED]
    assert timeline['segments'][0][0] == 0.0 and timeline['segments'][-1][1] == 1.0
    for (_, end, _), (start, _, _) in zip(timeline['segments'], timeline['segments'][1:]):
        assert end == start
    assert timeline['ratios'] == [0.9, 0.9, None, 0.3, None]
    assert timeline['runs'] == [[(0, 0.9), (1, 0.9)], [(3, 0.3)]]  # no line across the unknown


def test_trace_scale_is_capped_and_ignores_garbage():
    rows = [graded(0, ratio=4.0), graded(1, ratio=float('nan')), graded(2, ratio=-1),
            graded(3, ratio='0.5'), row(4, clear_sky_ratio=0.8)]
    timeline = GT.build_grade_timeline(rows)
    assert timeline['ratios'] == [GT.RATIO_CEILING, None, None, None, None]
    assert timeline['scale_max'] == GT.RATIO_CEILING


def test_ungraded_and_empty_nights_have_no_timeline():
    assert GT.build_grade_timeline([row(0), row(1)])['graded'] is False
    empty = GT.build_grade_timeline([])
    assert empty['graded'] is False and empty['segments'] == [] and empty['runs'] == []
    assert GT.build_grade_timeline([graded(0)])['segments'] == [(0.0, 1.0, GT.GRADE_OK)]


# -- the lane in the scrubber -----------------------------------------------------

def test_lane_colours_ok_not_ok_and_ungraded(qt_app, make_widget):
    from ui.panels import library_scrubber as SC
    from ui.panels.library_grade_lane import LANE_H
    frames = night(100)
    scrubber = make_scrubber(make_widget, frames)
    scrubber.set_index(0)
    strip_y = SC._LANE_TOP + LANE_H - 2
    assert near(pixel(scrubber, x_of(15, 100), strip_y), Colors.success_default)
    assert near(pixel(scrubber, x_of(50, 100), strip_y), Colors.warning_default)
    assert near(pixel(scrubber, x_of(96, 100), strip_y), Colors.gray_6)
    # Above the strip the lane is neutral, not a second green / amber band
    # under the condition band.
    for index in (15, 50):
        for y in range(SC._LANE_TOP + 1, SC._LANE_TOP + LANE_H - 7, 3):
            c = pixel(scrubber, x_of(index, 100) + 3, y)
            assert max(c.red(), c.green(), c.blue()) - min(c.red(), c.green(), c.blue()) <= 8
    for index in range(1, 100, 3):  # a pause is never painted as an error
        assert not near(pixel(scrubber, x_of(index, 100), strip_y), Colors.error_default, 25)


def test_trace_sits_high_when_clear_and_low_under_cloud(qt_app, make_widget):
    scrubber = make_scrubber(make_widget, night(100))
    lane = scrubber.grade_lane()
    clear_y, cloudy_y = lane.marker_y(10), lane.marker_y(50)
    assert clear_y < cloudy_y
    assert lane.marker_y(95) is None  # ungraded frame: no point on the trace


def test_marker_follows_the_frame_shown(qt_app, make_widget):
    from ui.panels import library_scrubber as SC
    frames = night(100)
    scrubber = make_scrubber(make_widget, frames)
    from ui.panels.library_grade_lane import LANE_H
    strip_y = SC._LANE_TOP + LANE_H - 2  # below the trace, clear of its marker ring

    def playhead_at(index):
        return near(pixel(scrubber, x_of(index, 100), strip_y), Colors.accent_text, 30)

    scrubber.set_index(20)
    assert playhead_at(20) and not playhead_at(60)
    scrubber.set_index(60)
    assert playhead_at(60) and not playhead_at(20)
    # ...and a dot sits on the trace at that frame's clear-sky ratio.
    ring_y = SC._LANE_TOP + scrubber.grade_lane().marker_y(60)
    assert near(pixel(scrubber, x_of(60, 100) + 2, ring_y), Colors.accent_text, 30)
    scrubber.set_index(20)
    assert not near(pixel(scrubber, x_of(60, 100) + 2, ring_y), Colors.accent_text, 30)


def test_scrubbing_never_renders_the_lane_again(qt_app, make_widget):
    scrubber = make_scrubber(make_widget, night(1200))
    scrubber.grab()
    lane = scrubber.grade_lane()
    assert lane.renders == 1
    for index in range(0, 1200, 7):
        scrubber.set_index(index)
        scrubber.grab()
    assert lane.renders == 1
    scrubber.update_data(night(1201), [])  # a live frame: one new rendering
    scrubber.grab()
    assert lane.renders == 2


def test_night_without_grades_has_no_lane(qt_app, make_widget):
    from ui.panels import library_scrubber as SC
    plain = make_scrubber(make_widget, [row(i) for i in range(50)])
    assert not plain.grade_lane().has_grades()
    assert plain.height() == SC._TOTAL_H
    with_grades = make_scrubber(make_widget, night(50))
    assert with_grades.height() == SC._TOTAL_H + SC._LANE_EXTRA
    with_grades.set_data([row(i) for i in range(50)], [], [])  # another night opened
    assert with_grades.height() == SC._TOTAL_H


def test_lane_tooltip_words():
    from ui.panels.library_grade_lane import lane_tooltip
    text = lane_tooltip(graded(4, ok=False, reasons='cloud_cover_high', ratio=0.31))
    assert text.splitlines() == ["22:02 · Not OK · Cloud cover high",
                                 "Stars at 31% of clear sky"]
    assert lane_tooltip(row(4)) == "22:02 · not graded"
    assert "Not OK" not in lane_tooltip(row(4))


# -- the grade block follows every way of moving between frames ---------------------

def test_block_follows_next_previous_and_playback(qt_app, make_widget):
    view = open_night(make_widget, night(100))
    assert headline(view) == "—"            # opens on the latest frame, ungraded here
    view._seek_to_id(29)
    assert headline(view) == "OK"
    view.next_btn.click()
    assert headline(view) == "Not OK · Cloud cover high"
    assert "Stars at 20% of clear sky" in view.grade_block.details.text()
    view.prev_btn.click()
    assert headline(view) == "OK"
    view._set_speed(8)
    view._advance()                          # one playback tick
    assert view.scrubber.current_index() == 37
    assert headline(view) == "Not OK · Cloud cover high"


def test_block_follows_clicks_and_drags_on_strip_band_and_lane(qt_app, make_widget):
    from ui.panels import library_scrubber as SC
    from ui.panels.library_grade_lane import LANE_H
    view = open_night(make_widget, night(100))
    scrubber = view.scrubber
    scrubber.resize(TRACK_W + 2 * SC._PAD_X, scrubber.height())
    rows_y = {'filmstrip': SC._STRIP_TOP + 20, 'band': SC._BAND_TOP + 4,
              'lane': SC._LANE_TOP + LANE_H // 2}
    for (where, y), (index, want) in zip(rows_y.items(), (
            (10, "OK"), (50, "Not OK · Cloud cover high"), (80, "OK"))):
        QTest.mouseClick(scrubber, Qt.LeftButton, Qt.NoModifier, QPoint(x_of(index, 100), y))
        assert scrubber.current_index() == index, where
        assert headline(view) == want, where

    y = rows_y['lane']
    QTest.mousePress(scrubber, Qt.LeftButton, Qt.NoModifier, QPoint(x_of(80, 100), y))
    seen = []
    for index in (70, 60, 40, 25, 95):
        pos = QPointF(x_of(index, 100), y)
        qt_app.sendEvent(scrubber, QMouseEvent(
            QEvent.MouseMove, pos, scrubber.mapToGlobal(pos), Qt.NoButton,
            Qt.LeftButton, Qt.NoModifier))
        seen.append(headline(view))
    QTest.mouseRelease(scrubber, Qt.LeftButton, Qt.NoModifier, QPoint(x_of(95, 100), y))
    assert seen == ["OK", "Not OK · Cloud cover high", "Not OK · Cloud cover high", "OK", "—"]


def test_block_follows_the_keyboard(qt_app, make_widget):
    view = open_night(make_widget, night(100))
    view._seek_to_id(30)
    assert headline(view) == "Not OK · Cloud cover high"
    QTest.keyClick(view.scrubber, Qt.Key_Left)
    assert view.scrubber.current_index() == 29 and headline(view) == "OK"
    QTest.keyClick(view.scrubber, Qt.Key_Right)
    assert headline(view) == "Not OK · Cloud cover high"
    QTest.keyClick(view.scrubber, Qt.Key_Home)
    assert view.scrubber.current_index() == 0 and headline(view) == "OK"
    QTest.keyClick(view.scrubber, Qt.Key_End)
    assert view.scrubber.current_index() == 99 and headline(view) == "—"
    QTest.keyClick(view.scrubber, Qt.Key_Right)  # already at the end
    assert view.scrubber.current_index() == 99


def test_block_follows_event_pins_and_the_events_list(qt_app, make_widget):
    from ui.panels import library_scrubber as SC
    frames = night(100)
    for r in frames[:40]:
        r['roof'] = 'Closed'
    view = open_night(make_widget, frames)
    scrubber = view.scrubber
    scrubber.resize(TRACK_W + 2 * SC._PAD_X, scrubber.height())
    opened = [e for e in E.build_timeline(frames, [], []) if e['type'] == E.EVENT_ROOF_OPEN]
    assert opened and opened[0]['image_id'] == 40

    QTest.mouseClick(scrubber, Qt.LeftButton, Qt.NoModifier,
                     QPoint(x_of(40, 100), SC._PINS_TOP + 4))
    assert scrubber.current_index() == 40
    assert headline(view) == "Not OK · Cloud cover high"

    view._seek_to_id(5)
    assert headline(view) == "OK"
    links = [view.events_box.itemAt(i).widget() for i in range(view.events_box.count())]
    links[0].clicked.emit()
    assert scrubber.current_index() == 40
    assert headline(view) == "Not OK · Cloud cover high"


def test_block_follows_a_live_frame_at_the_edge_and_stays_put_mid_scrub(qt_app, make_widget):
    frames = [graded(i) for i in range(10)]
    view = open_night(make_widget, frames)
    assert headline(view) == "OK"
    view.append_frame(graded(10, ok=False, reasons='stars_dropping', ratio=0.5))
    assert view.scrubber.current_index() == 10
    assert headline(view) == "Not OK · Stars dropping"
    view._seek_to_id(3)
    view.append_frame(graded(11, ok=False, reasons='stars_low', ratio=0.4))
    assert view.scrubber.current_index() == 3 and headline(view) == "OK"


def test_block_is_cleared_between_nights_and_for_an_empty_night(qt_app, make_widget):
    view = open_night(make_widget, [graded(0, ok=False, reasons='stars_low')])
    assert headline(view) == "Not OK · Too few stars"
    other = {'key': '2026-07-01', 'gaps': [], 'max_gap_seconds': 0}
    view.begin_load(other)
    assert headline(view) == "—" and view.grade_block.details.isHidden()
    view.set_frames({'session': other, 'frames': [], 'filmstrip': []})
    assert headline(view) == "—"
    assert view.lane_legend.isHidden() and view.grade_label.text() == ""


def test_legend_is_shown_only_with_a_lane(qt_app, make_widget):
    view = open_night(make_widget, night(50))
    assert not view.lane_legend.isHidden()
    for word in ("imaging OK", "not OK", "not graded", "stars vs clear sky"):
        assert word in view.lane_legend.text()
    assert Colors.error_default not in view.lane_legend.text()
    plain = open_night(make_widget, [row(i) for i in range(5)])
    assert plain.lane_legend.isHidden()
    plain.append_frame(graded(5))  # the first graded frame of the night arrives live
    assert not plain.lane_legend.isHidden()
    assert plain.scrubber.grade_lane().has_grades()
