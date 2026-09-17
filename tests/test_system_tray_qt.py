"""Tests for the Qt system tray (issue #37 — cross-platform Phase 1)."""
import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QApplication, QMainWindow, QSystemTrayIcon

from ui.system_tray_qt import SystemTrayQt, TrayUnavailableError


class FakeWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.is_capturing = False
        self.calls = []

    def start_capture(self):
        self.calls.append('start')
        self.is_capturing = True

    def stop_capture(self):
        self.calls.append('stop')
        self.is_capturing = False

    def quit_application(self):
        self.calls.append('quit')


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def tray_available(monkeypatch):
    monkeypatch.setattr(
        QSystemTrayIcon, 'isSystemTrayAvailable', staticmethod(lambda: True)
    )


@pytest.fixture
def window(qapp):
    win = FakeWindow()
    win.show()
    yield win
    win.close()
    win.deleteLater()
    qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    qapp.processEvents()


@pytest.fixture
def make_tray(qapp, window):
    trays = []

    def _make(**kwargs):
        tray = SystemTrayQt(window, qapp, **kwargs)
        trays.append(tray)
        return tray

    yield _make
    for tray in trays:
        tray.shutdown()
        tray.deleteLater()
    qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    qapp.processEvents()


def test_start_hidden_hides_the_window(tray_available, make_tray, window):
    tray = make_tray(start_hidden=True)

    assert not window.isVisible()
    assert tray._is_visible is False


def test_start_visible_leaves_the_window_up(tray_available, make_tray, window):
    tray = make_tray(start_hidden=False)

    assert window.isVisible()
    assert tray._is_visible is True


def test_menu_tracks_visibility_and_capture_state(tray_available, make_tray, window):
    tray = make_tray(start_hidden=False)
    tray.menu.aboutToShow.emit()

    assert tray._show_hide_action.text() == "Hide Window"
    assert tray._start_action.isEnabled()
    assert not tray._stop_action.isEnabled()
    assert tray.menu.defaultAction() is tray._show_hide_action

    window.hide()
    window.is_capturing = True
    tray.menu.aboutToShow.emit()

    assert tray._show_hide_action.text() == "Show Window"
    assert not tray._start_action.isEnabled()
    assert tray._stop_action.isEnabled()


def test_show_and_hide_actions_move_the_window(tray_available, make_tray, window):
    tray = make_tray(start_hidden=True)

    tray._show_hide_action.trigger()
    assert window.isVisible()
    assert tray._is_visible is True

    tray._show_hide_action.trigger()
    assert not window.isVisible()
    assert tray._is_visible is False


def test_activation_by_click_restores_the_window(tray_available, make_tray, window):
    tray = make_tray(start_hidden=True)

    tray._on_activated(QSystemTrayIcon.ActivationReason.Trigger)

    assert window.isVisible()
    assert tray._is_visible is True


def test_start_capture_action_only_fires_when_idle(tray_available, make_tray, window):
    tray = make_tray(start_hidden=False)

    tray._start_action.trigger()
    assert window.calls == ['start']

    tray._do_start_capture()
    assert window.calls == ['start']


def test_stop_capture_action_only_fires_while_capturing(tray_available, make_tray, window):
    tray = make_tray(start_hidden=False)

    tray._do_stop_capture()
    assert window.calls == []

    window.is_capturing = True
    tray.menu.aboutToShow.emit()
    tray._stop_action.trigger()
    assert window.calls == ['stop']


def test_exit_action_quits_through_the_window(tray_available, make_tray, window):
    tray = make_tray(start_hidden=False)

    tray._exit_action.trigger()

    assert window.calls == ['quit']


def test_missing_tray_raises_and_leaves_the_window_visible(qapp, window, monkeypatch):
    monkeypatch.setattr(
        QSystemTrayIcon, 'isSystemTrayAvailable', staticmethod(lambda: False)
    )

    with pytest.raises(TrayUnavailableError):
        SystemTrayQt(window, qapp, start_hidden=True)

    assert window.isVisible()


def test_quit_on_last_window_closed_is_restored_by_shutdown(tray_available, qapp, window):
    qapp.setQuitOnLastWindowClosed(True)
    tray = SystemTrayQt(window, qapp, start_hidden=False)
    try:
        assert qapp.quitOnLastWindowClosed() is False
    finally:
        tray.shutdown()
        qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        qapp.processEvents()

    assert qapp.quitOnLastWindowClosed() is True
