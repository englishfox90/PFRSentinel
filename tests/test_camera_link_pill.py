"""Camera pill during an outage (QA, issue #43): a session's ``link_state``
('reconnecting' / 'waiting') replaces the green "Capturing · frame N" on either
backend, and an 'ok' link (or a session without the attribute) keeps it
(offscreen Qt)."""
from types import SimpleNamespace

import pytest


@pytest.fixture
def bar(qapp):
    from ui.components.app_bar import AppBar
    from ui.components.status_strip import StatusStrip
    b = AppBar()
    strip = StatusStrip()
    b.set_status_strip(strip)
    b.set_camera_connected(True)
    b.set_capturing(True)
    b._is_capturing = True
    yield b, strip
    b.deleteLater()
    strip.deleteLater()


@pytest.fixture
def qapp():
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def _tick(b, session, count=7):
    b.update_status(True, count, SimpleNamespace(zwo_camera=session))


@pytest.mark.parametrize("backend", ["zwo", "alpaca"])
@pytest.mark.parametrize("state, text", [
    ("reconnecting", "Reconnecting to camera…"),
    ("waiting", "Camera lost · retrying"),
    ("unconfirmed", "Reconnected · waiting for a frame"),
    ("stalled", "Camera not finishing exposures"),
])
def test_outage_shows_on_the_pill(bar, backend, state, text):
    b, strip = bar
    _tick(b, SimpleNamespace(backend=backend, link_state=state))
    assert strip.pill.value.text() == text
    assert strip.pill._state_key[0] == 'reconnecting'


def test_frames_flowing_again_show_the_count(bar):
    b, strip = bar
    session = SimpleNamespace(link_state='reconnecting')
    _tick(b, session)
    session.link_state = 'ok'
    _tick(b, session, count=9)
    assert strip.pill.value.text() == "Capturing · frame 9"


def test_a_session_without_link_state_reads_as_ok(bar):
    b, strip = bar
    _tick(b, SimpleNamespace(), count=3)
    assert strip.pill.value.text() == "Capturing · frame 3"


def test_refresh_pushes_the_idle_label_at_once(qapp):
    from ui.components.app_bar import AppBar
    from ui.components.status_strip import StatusStrip
    b = AppBar()
    strip = StatusStrip()
    b.set_status_strip(strip)
    b.set_camera_connected(True, "Alpaca · not connected")
    b.refresh_camera_pill()
    assert strip.pill.value.text() == "Alpaca · not connected"
    b.set_camera_connected(True)
    b.refresh_camera_pill()
    assert strip.pill.value.text() == "Connected · idle"
    b.deleteLater()
    strip.deleteLater()


def test_refresh_also_updates_the_primary_action(qapp):
    """QA: back on the Alpaca segment the button still read "Connect Camera"
    until the next 1 s tick, so a quick click ran discovery instead of Start."""
    from ui.components.app_bar import AppBar
    b = AppBar()
    b.update_primary_action(False, False)
    assert b.primary_btn.text() == "Connect Camera"
    b.set_camera_connected(True)
    b.refresh_camera_pill()
    assert b.primary_btn.text() == "Start Capture"
    b.deleteLater()
