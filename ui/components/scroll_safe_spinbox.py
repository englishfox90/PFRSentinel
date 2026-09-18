"""Spin boxes that ignore the scroll wheel unless the user deliberately
selected them.

Qt spin boxes step on every wheel event that passes over them, focused or
not, so scrolling a settings page with a trackpad silently rewrites whatever
value the pointer happens to cross (issue #13: Interval and Max Exposure kept
drifting between nights). Sliders already guard against this with
``ClickSlider``; these are the spin box equivalent.

A wheel event is honoured only while the box holds focus the user gave it on
purpose: a click, a Tab, or a keyboard shortcut. Focus a page hands over on
its own when it is shown does not count, so landing on the Capture page with
the pointer resting on Interval still scrolls the page. A click on a box that
already holds such hand-me-down focus arms it too, since no focus event fires
for that click. Ignored events propagate to the enclosing scroll area, which
is what the user was trying to move.

The classes keep the Fluent names so a panel swaps one import line and no
call site changes.
"""
from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import QWidget
from qfluentwidgets import DoubleSpinBox as _FluentDoubleSpinBox
from qfluentwidgets import SpinBox as _FluentSpinBox

DELIBERATE_FOCUS_REASONS = (
    Qt.FocusReason.MouseFocusReason,
    Qt.FocusReason.TabFocusReason,
    Qt.FocusReason.BacktabFocusReason,
    Qt.FocusReason.ShortcutFocusReason,
)


class _WheelGuard:
    """Mixin: honour wheel events only under deliberately given focus."""

    _wheel_armed = False

    def _init_wheel_guard(self):
        # StrongFocus is WheelFocus minus the wheel, so hovering and scrolling
        # can never *acquire* focus and arm the box by accident.
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        # Clicks land on the line edit and the step buttons, not on the box, and
        # a click on an already-focused box fires no focusInEvent; watch the
        # children for the press that says "I am using this one".
        for child in self.findChildren(QWidget):
            child.installEventFilter(self)

    def _arm_from_press(self):
        self._wheel_armed = True
        if not self.hasFocus():
            self.setFocus(Qt.FocusReason.MouseFocusReason)

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.MouseButtonPress:
            self._arm_from_press()
        return super().eventFilter(watched, event)

    def mousePressEvent(self, event):
        self._arm_from_press()
        super().mousePressEvent(event)

    def focusInEvent(self, event):
        self._wheel_armed = event.reason() in DELIBERATE_FOCUS_REASONS
        super().focusInEvent(event)

    def focusOutEvent(self, event):
        self._wheel_armed = False
        super().focusOutEvent(event)

    def wheelEvent(self, event):
        if self._wheel_armed and self.hasFocus():
            super().wheelEvent(event)
        else:
            event.ignore()


class ScrollSafeSpinBox(_WheelGuard, _FluentSpinBox):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._init_wheel_guard()


class ScrollSafeDoubleSpinBox(_WheelGuard, _FluentDoubleSpinBox):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._init_wheel_guard()


# Drop-in names: `from ..components.scroll_safe_spinbox import SpinBox, DoubleSpinBox`
SpinBox = ScrollSafeSpinBox
DoubleSpinBox = ScrollSafeDoubleSpinBox
