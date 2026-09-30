"""
Tests for ui/components/star_pick_canvas.py — the zoom/pan view the guided
calibration dialog picks stars on (issue #79: the old view was a fixed
760 px label with no zoom).
"""
import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QColor, QMouseEvent, QPixmap, QWheelEvent
from PySide6.QtWidgets import QApplication

from ui.components.star_pick_canvas import (
    CanvasHint, CanvasMarker, StarPickCanvas)

IMAGE = 3552          # frame edge, px
VIEW = 800            # widget edge, px


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def canvas(qapp):
    widget = StarPickCanvas()
    widget.resize(VIEW, VIEW)
    pix = QPixmap(IMAGE, IMAGE)
    pix.fill(QColor(10, 10, 10))
    widget.set_image(pix, snap_radius_px=40.0)
    widget.show()
    qapp.processEvents()
    clicks = []
    widget.clicked.connect(lambda x, y: clicks.append((x, y)))
    widget.drops = []
    widget.hint_dropped.connect(lambda n, x, y: widget.drops.append((n, x, y)))
    yield widget, clicks
    widget.close()
    widget.deleteLater()
    qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    qapp.processEvents()


def _mouse(widget, kind, pos, button=Qt.LeftButton, buttons=Qt.LeftButton):
    QApplication.sendEvent(widget, QMouseEvent(
        kind, QPointF(pos), QPointF(pos), button, buttons, Qt.NoModifier))


def _click(widget, pos):
    _mouse(widget, QEvent.MouseButtonPress, pos)
    _mouse(widget, QEvent.MouseButtonRelease, pos, buttons=Qt.NoButton)


def _wheel(widget, pos, notches=1):
    QApplication.sendEvent(widget, QWheelEvent(
        QPointF(pos), QPointF(widget.mapToGlobal(pos)), QPoint(0, 0),
        QPoint(0, 120 * notches), Qt.NoButton, Qt.NoModifier,
        Qt.NoScrollPhase, False))


def test_whole_frame_fits_the_widget_whatever_its_size(canvas):
    widget, _clicks = canvas
    assert widget.image_to_widget(0, 0).x() == pytest.approx(0.0)
    assert widget.image_to_widget(IMAGE, IMAGE).x() == pytest.approx(VIEW)
    widget.resize(1600, 1600)
    QApplication.processEvents()
    assert widget.image_to_widget(IMAGE, IMAGE).x() == pytest.approx(1600)


def test_click_reports_original_image_pixels(canvas):
    widget, clicks = canvas
    _click(widget, QPoint(400, 200))
    (x, y), = clicks
    assert x == pytest.approx(400 * IMAGE / VIEW)
    assert y == pytest.approx(200 * IMAGE / VIEW)


def test_wheel_zooms_about_the_cursor(canvas):
    widget, _clicks = canvas
    pos = QPoint(600, 250)
    before = widget.widget_to_image(QPointF(pos))
    _wheel(widget, pos, notches=3)
    assert widget.zoom() > 1.0
    after = widget.widget_to_image(QPointF(pos))
    assert after[0] == pytest.approx(before[0], abs=1.0)
    assert after[1] == pytest.approx(before[1], abs=1.0)


def test_click_still_reports_image_pixels_when_zoomed(canvas):
    widget, clicks = canvas
    _wheel(widget, QPoint(600, 250), notches=4)
    expected = widget.widget_to_image(QPointF(300, 300))
    _click(widget, QPoint(300, 300))
    assert clicks[-1] == pytest.approx(expected)


def test_zoom_never_goes_below_whole_frame(canvas):
    widget, _clicks = canvas
    _wheel(widget, QPoint(400, 400), notches=-5)
    assert widget.zoom() == pytest.approx(1.0)


def test_zoom_is_capped(canvas):
    widget, _clicks = canvas
    _wheel(widget, QPoint(400, 400), notches=60)
    top = widget.zoom()
    _wheel(widget, QPoint(400, 400), notches=5)
    assert widget.zoom() == pytest.approx(top)


def test_drag_pans_and_does_not_pick(canvas):
    widget, clicks = canvas
    _wheel(widget, QPoint(400, 400), notches=4)
    before = widget.widget_to_image(QPointF(400, 400))
    _mouse(widget, QEvent.MouseButtonPress, QPoint(400, 400))
    _mouse(widget, QEvent.MouseMove, QPoint(300, 340))
    _mouse(widget, QEvent.MouseButtonRelease, QPoint(300, 340), buttons=Qt.NoButton)
    assert clicks == []
    # The image point that was under the cursor followed it.
    assert widget.widget_to_image(QPointF(300, 340)) == pytest.approx(before, abs=1.0)


def test_pan_cannot_leave_the_frame(canvas):
    widget, _clicks = canvas
    _wheel(widget, QPoint(400, 400), notches=4)
    _mouse(widget, QEvent.MouseButtonPress, QPoint(100, 100))
    _mouse(widget, QEvent.MouseMove, QPoint(5000, 5000))
    _mouse(widget, QEvent.MouseButtonRelease, QPoint(5000, 5000), buttons=Qt.NoButton)
    x, y = widget.widget_to_image(QPointF(0, 0))
    assert x >= -1e-6 and y >= -1e-6


def test_double_click_returns_to_whole_frame(canvas):
    widget, _clicks = canvas
    _wheel(widget, QPoint(400, 400), notches=4)
    _mouse(widget, QEvent.MouseButtonDblClick, QPoint(400, 400))
    assert widget.zoom() == pytest.approx(1.0)


def test_picking_can_be_switched_off_while_zoom_stays(canvas):
    widget, clicks = canvas
    widget.set_interactive(False)
    _click(widget, QPoint(400, 400))
    assert clicks == []
    _wheel(widget, QPoint(400, 400), notches=2)
    assert widget.zoom() > 1.0


def test_centre_on_brings_a_star_into_view(canvas):
    widget, _clicks = canvas
    widget.centre_on(3000.0, 500.0, min_zoom=4.0)
    pt = widget.image_to_widget(3000.0, 500.0)
    assert 0 <= pt.x() <= VIEW and 0 <= pt.y() <= VIEW
    assert widget.zoom() >= 4.0


def test_view_is_rendered_at_the_display_pixel_ratio(canvas, qapp):
    """A logical-size cache is upscaled 2x on a 200 % display — half the
    resolution of the monitor the dialog was enlarged for."""
    widget, _clicks = canvas
    widget.grab()                       # forces a paint, which fills the cache
    dpr = widget.devicePixelRatioF()
    assert widget._view.devicePixelRatio() == pytest.approx(dpr)
    assert widget._view.width() == round(widget.width() * dpr)


HINT_IMAGE = (2000.0, 2000.0)          # one hint, in image pixels


def _with_hint(widget):
    widget.set_overlays([], [CanvasHint(*HINT_IMAGE, "Arcturus")], None)
    pt = widget.image_to_widget(*HINT_IMAGE)
    return QPoint(round(pt.x()), round(pt.y()))


def _drag(widget, start, end):
    _mouse(widget, QEvent.MouseButtonPress, start)
    _mouse(widget, QEvent.MouseMove, QPoint((start.x() + end.x()) // 2,
                                            (start.y() + end.y()) // 2))
    _mouse(widget, QEvent.MouseMove, end)
    _mouse(widget, QEvent.MouseButtonRelease, end, buttons=Qt.NoButton)


class TestHintDrag:

    def test_dragging_a_hint_reports_its_label_at_the_drop_point(self, canvas):
        widget, clicks = canvas
        start = _with_hint(widget)
        end = QPoint(start.x() + 60, start.y() - 40)
        _drag(widget, QPoint(start.x() + 6, start.y() - 4), end)
        (name, x, y), = widget.drops
        assert name == "Arcturus"
        assert (x, y) == pytest.approx(widget.widget_to_image(QPointF(end)))
        assert clicks == []

    def test_a_hint_drag_does_not_pan_the_view(self, canvas):
        widget, _clicks = canvas
        _wheel(widget, QPoint(400, 400), notches=4)
        start = _with_hint(widget)
        origin = widget.widget_to_image(QPointF(0, 0))
        _drag(widget, start, QPoint(start.x() - 120, start.y() - 90))
        assert widget.widget_to_image(QPointF(0, 0)) == pytest.approx(origin)
        assert len(widget.drops) == 1

    def test_a_drag_that_starts_on_empty_sky_still_pans(self, canvas):
        widget, _clicks = canvas
        _wheel(widget, QPoint(400, 400), notches=4)
        _with_hint(widget)
        start = QPoint(100, 100)
        before = widget.widget_to_image(QPointF(start))
        _drag(widget, start, QPoint(220, 160))
        assert widget.drops == []
        assert widget.widget_to_image(QPointF(220, 160)) == pytest.approx(before, abs=1.0)

    def test_a_click_on_a_hint_is_still_a_click(self, canvas):
        widget, clicks = canvas
        start = _with_hint(widget)
        _click(widget, start)
        assert widget.drops == []
        assert len(clicks) == 1

    def test_hints_can_be_dragged_while_picking_is_off(self, canvas):
        """The review step shows the solved model's predictions as hints and
        switches picking off; dragging one onto the star it missed must work."""
        widget, clicks = canvas
        widget.set_interactive(False)
        start = _with_hint(widget)
        _drag(widget, start, QPoint(start.x() + 50, start.y() + 30))
        assert [d[0] for d in widget.drops] == ["Arcturus"]
        assert clicks == []

    def test_a_drop_outside_the_frame_is_ignored(self, canvas):
        widget, _clicks = canvas
        start = _with_hint(widget)
        _drag(widget, start, QPoint(VIEW + 200, VIEW + 200))
        assert widget.drops == []

    def test_ghost_paints_while_dragging(self, canvas):
        widget, _clicks = canvas
        start = _with_hint(widget)
        _mouse(widget, QEvent.MouseButtonPress, start)
        _mouse(widget, QEvent.MouseMove, QPoint(start.x() + 40, start.y() + 40))
        assert not widget.grab().toImage().isNull()
        _mouse(widget, QEvent.MouseButtonRelease,
               QPoint(start.x() + 40, start.y() + 40), buttons=Qt.NoButton)


def test_paints_with_every_overlay_kind(canvas, qapp):
    """Smoke: markers in each state, both hint styles and a pending pick."""
    widget, _clicks = canvas
    widget.set_overlays(
        [CanvasMarker(500, 500, "Vega", 'ok'),
         CanvasMarker(900, 700, "Deneb", 'suspect'),
         CanvasMarker(1200, 900, "Altair", 'excluded'),
         CanvasMarker(1500, 1100, "Pollux → Sirius", 'renamed')],
        [CanvasHint(2000, 2000, "Arcturus"),
         CanvasHint(2200, 2100, "Spica", supported=False),
         CanvasHint(2400, 2300, "Regulus", emphasised=True)],
        (1800.0, 1700.0))
    _mouse(widget, QEvent.MouseMove, QPoint(200, 200), Qt.NoButton, Qt.NoButton)
    image = widget.grab().toImage()
    assert not image.isNull()


def test_replace_image_keeps_zoom_and_pan(canvas):
    widget, _ = canvas
    widget.zoom_in()
    widget.centre_on(3000.0, 2900.0, min_zoom=widget.zoom())
    zoom, ox, oy = widget.zoom(), widget._ox, widget._oy
    swap = QPixmap(IMAGE, IMAGE)
    swap.fill(QColor(90, 90, 90))
    widget.replace_image(swap)
    assert widget._full is swap
    assert widget.zoom() == pytest.approx(zoom)
    assert (widget._ox, widget._oy) == (ox, oy)

    other = QPixmap(IMAGE // 2, IMAGE // 2)
    other.fill(QColor(30, 30, 30))
    widget.replace_image(other)              # a different frame: whole-frame view
    assert widget._full is other
    assert widget.zoom() == 1.0
