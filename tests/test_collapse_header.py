"""CollapseHeader / CollapsibleCard / StaticCard: the whole header row
toggles a card (mouse anywhere on it, Space, Enter), the chevron follows the
state, the header is a focusable pointing-hand control with a tooltip, a
card given spare height keeps a compact header, and a StaticCard paints the
same with or without the pointer over it (offscreen Qt)."""
import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QEnterEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget

from ui.components.cards import CollapsibleCard, StaticCard
from ui.components.collapse_header import CollapseHeader
from ui.theme.icons import mdi


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


@pytest.fixture
def card(qapp):
    card = CollapsibleCard("Web Server", mdi('web'))
    card.add_widget(QLabel("content"))
    card.resize(600, 300)
    card.show()
    qapp.processEvents()
    yield card
    card.close()
    card.deleteLater()
    qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_starts_collapsed_with_a_right_chevron(card):
    assert not card.is_expanded() and card.content.isHidden()
    assert card.header.chevron_name() == 'chevron-right'
    assert card.header.toolTip() == "Expand"
    assert card.expand_icon is card.header.chevron
    assert not card.header.chevron.pixmap().isNull()


def test_a_click_anywhere_on_the_header_toggles(qapp, card):
    header = card.header
    assert header.width() >= card.width() - 2
    for x in (8, header.width() // 2, header.width() - 8):
        was = card.is_expanded()
        QTest.mouseClick(header, Qt.LeftButton, Qt.NoModifier, QPoint(x, header.height() // 2))
        assert card.is_expanded() is (not was)
    # On the title text itself, too: the labels let the click through.
    title = header.title_label
    QTest.mouseClick(title, Qt.LeftButton, Qt.NoModifier, QPoint(4, 4))
    qapp.processEvents()
    assert not card.is_expanded()


def test_chevron_and_tooltip_follow_the_state(card):
    card.set_expanded(True)
    assert card.header.chevron_name() == 'chevron-down' and card.header.toolTip() == "Collapse"
    assert not card.content.isHidden()
    card._toggle_expanded(None)   # the old entry point, still used by callers
    assert card.header.chevron_name() == 'chevron-right' and card.content.isHidden()


def test_keyboard_path_and_cursor(qapp, card):
    header = card.header
    assert header.cursor().shape() == Qt.PointingHandCursor
    assert header.focusPolicy() == Qt.StrongFocus
    header.setFocus(Qt.TabFocusReason)
    QTest.keyClick(header, Qt.Key_Space)
    assert card.is_expanded()
    QTest.keyClick(header, Qt.Key_Return)
    assert not card.is_expanded()
    QTest.keyClick(header, Qt.Key_Enter)
    assert card.is_expanded()
    QTest.keyClick(header, Qt.Key_A)
    assert card.is_expanded()


def test_header_stays_compact_in_a_tall_card(qapp, card):
    compact = card.header.sizeHint().height()
    assert compact <= 48
    card.resize(600, 500)
    qapp.processEvents()
    assert card.header.height() == compact and card.header.y() <= 1
    card.set_expanded(True)
    qapp.processEvents()
    assert card.header.height() == compact


def test_header_shows_hover_and_focus_feedback(qapp):
    host = QWidget()
    host.setObjectName("host")
    host.setStyleSheet("#host { background: #1d1d1c; }")
    lay = QVBoxLayout(host)
    lay.setContentsMargins(0, 0, 0, 0)
    header = CollapseHeader("Imaging conditions", mdi('telescope'))
    lay.addWidget(header)
    host.resize(320, 44)
    host.show()
    qapp.processEvents()

    def pixel(y):
        return host.grab().toImage().pixelColor(header.width() // 2, y).name()

    def shade():
        return pixel(8)

    rest = shade()
    header.setDown(True)
    pressed = shade()
    header.setDown(False)
    assert pressed != rest
    header.setFocus(Qt.TabFocusReason)
    qapp.processEvents()
    ring = pixel(4)
    assert ring != rest
    header.set_status("OK · 8% cloud")
    assert not header.status_label.isHidden() and header.status_label.text() == "OK · 8% cloud"
    header.set_status("")
    assert header.status_label.isHidden()
    host.close()
    host.deleteLater()


def test_static_card_does_not_light_up_under_the_pointer(qapp):
    host = QWidget()
    lay = QVBoxLayout(host)
    static = StaticCard()
    static.setFixedSize(200, 80)
    lay.addWidget(static)
    host.show()
    qapp.processEvents()

    def look():
        image = static.grab().toImage()
        return image.pixelColor(100, 40).name(), image.pixelColor(1, 40).name()

    rest = look()
    centre = QPointF(100, 40)
    qapp.sendEvent(static, QEnterEvent(centre, centre, centre))
    QTest.qWait(250)
    assert look() == rest
    QTest.mousePress(static, Qt.LeftButton, Qt.NoModifier, QPoint(100, 40))
    QTest.qWait(250)
    assert look() == rest
    QTest.mouseRelease(static, Qt.LeftButton, Qt.NoModifier, QPoint(100, 40))
    host.close()
    host.deleteLater()
