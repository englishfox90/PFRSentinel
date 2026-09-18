"""ScrollSafeSpinBox / ScrollSafeDoubleSpinBox — the wheel must not change a
value the user did not deliberately select (issue #13: Interval and Max
Exposure drifting whenever the Capture page was scrolled)."""
import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent, QWheelEvent
from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget

from ui.components.scroll_safe_spinbox import (
    DoubleSpinBox, ScrollSafeDoubleSpinBox, ScrollSafeSpinBox, SpinBox,
)


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def window(qapp):
    host = QWidget()
    host.setLayout(QVBoxLayout())
    host.show()
    QApplication.setActiveWindow(host)
    qapp.processEvents()
    yield host
    host.close()
    host.deleteLater()
    qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    qapp.processEvents()


def _spin(window, cls=ScrollSafeSpinBox):
    box = cls()
    box.setRange(0, 100)
    box.setValue(50)
    window.layout().addWidget(box)
    # Let the layout show the box first: focus given to a not-yet-visible
    # widget is re-delivered on show with OtherFocusReason, which the guard
    # rightly treats as "the page handed it over", not a click.
    QApplication.processEvents()
    return box


def _wheel_up(box):
    centre = QPointF(box.rect().center())
    event = QWheelEvent(
        centre, box.mapToGlobal(centre), QPoint(0, 0), QPoint(0, 120),
        Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase, False,
    )
    QApplication.sendEvent(box, event)
    return event


def _click(widget):
    centre = QPointF(widget.rect().center())
    for kind in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonRelease):
        QApplication.sendEvent(widget, QMouseEvent(
            kind, centre, widget.mapToGlobal(centre), Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
        ))


def test_drop_in_names_are_the_guarded_classes():
    assert SpinBox is ScrollSafeSpinBox
    assert DoubleSpinBox is ScrollSafeDoubleSpinBox


def test_wheel_does_not_acquire_focus(window):
    assert _spin(window).focusPolicy() == Qt.FocusPolicy.StrongFocus


def test_wheel_over_unfocused_box_is_ignored_and_propagates(window, qapp):
    box = _spin(window)
    qapp.processEvents()
    assert not box.hasFocus()

    event = _wheel_up(box)

    assert box.value() == 50
    assert not event.isAccepted(), "ignored event must reach the scroll area"


def test_focus_handed_over_by_the_page_does_not_arm_the_wheel(window, qapp):
    box = _spin(window)
    box.setFocus(Qt.FocusReason.OtherFocusReason)
    qapp.processEvents()
    assert box.hasFocus()

    _wheel_up(box)

    assert box.value() == 50


@pytest.mark.parametrize("reason", [Qt.FocusReason.MouseFocusReason, Qt.FocusReason.TabFocusReason])
def test_deliberate_focus_lets_the_wheel_step(window, qapp, reason):
    box = _spin(window)
    box.setFocus(reason)
    qapp.processEvents()
    assert box.hasFocus()

    _wheel_up(box)

    assert box.value() == 51


def test_losing_focus_disarms_the_wheel(window, qapp):
    box = _spin(window)
    other = _spin(window)
    box.setFocus(Qt.FocusReason.MouseFocusReason)
    qapp.processEvents()
    other.setFocus(Qt.FocusReason.OtherFocusReason)
    qapp.processEvents()
    assert not box.hasFocus()

    _wheel_up(box)

    assert box.value() == 50


def test_double_spin_box_is_guarded_the_same_way(window, qapp):
    box = _spin(window, ScrollSafeDoubleSpinBox)
    qapp.processEvents()

    _wheel_up(box)
    assert box.value() == 50.0

    box.setFocus(Qt.FocusReason.MouseFocusReason)
    qapp.processEvents()
    _wheel_up(box)
    assert box.value() == 51.0


def test_click_on_a_page_focused_box_arms_the_wheel(window, qapp):
    """Focus handed over by the page, then a click on the line edit: no new
    focusInEvent fires for that click, so the press itself must arm it."""
    box = _spin(window)
    box.setFocus(Qt.FocusReason.OtherFocusReason)
    qapp.processEvents()
    assert box.hasFocus()
    _wheel_up(box)
    assert box.value() == 50

    _click(box.lineEdit())
    qapp.processEvents()

    _wheel_up(box)
    assert box.value() == 51


def test_click_on_an_unfocused_box_focuses_and_arms_it(window, qapp):
    box = _spin(window)
    other = _spin(window)
    other.setFocus(Qt.FocusReason.OtherFocusReason)
    qapp.processEvents()
    assert not box.hasFocus()

    _click(box.lineEdit())
    qapp.processEvents()

    assert box.hasFocus()
    _wheel_up(box)
    assert box.value() == 51
