"""AppBar primary action: while capturing, Stop is the only action — a click
that reaches the hidden Start button (a script, a test harness) stops
capture instead of re-entering start_capture, and a real mouse click on the
visible red Stop emits stop_clicked (offscreen Qt)."""
import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QEvent, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


@pytest.fixture
def bar(qapp):
    from ui.components.app_bar import AppBar
    b = AppBar()
    b.resize(1500, 60)
    b.show()
    hits = []
    b.start_clicked.connect(lambda: hits.append('start'))
    b.stop_clicked.connect(lambda: hits.append('stop'))
    b.connect_clicked.connect(lambda: hits.append('connect'))
    b.hits = hits
    yield b
    b.close()
    b.deleteLater()
    qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_visible_stop_button_stops(bar):
    bar.update_primary_action(False, True)
    bar.set_capturing(True)
    assert bar.stop_btn.isVisible() and not bar.primary_btn.isVisible()
    QTest.mouseClick(bar.stop_btn, Qt.LeftButton, Qt.NoModifier, bar.stop_btn.rect().center())
    assert bar.hits == ['stop']


def test_click_on_hidden_primary_while_capturing_never_starts(bar):
    bar.update_primary_action(False, True)
    bar.set_capturing(True)
    QTest.mouseClick(bar.primary_btn, Qt.LeftButton, Qt.NoModifier,
                     bar.primary_btn.rect().center())
    assert bar.hits == ['stop']


def test_primary_starts_or_connects_when_idle(bar):
    bar.update_primary_action(False, True)
    bar.primary_btn.click()
    bar.update_primary_action(False, False)
    bar.primary_btn.click()
    assert bar.hits == ['start', 'connect']
