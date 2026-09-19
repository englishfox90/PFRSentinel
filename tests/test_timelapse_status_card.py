"""Tests for the Timelapse Status card — session line + projected window (issue #14)."""
import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QApplication

from ui.panels.timelapse_status_card import TimelapseStatusCard


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def card(qapp):
    widget = TimelapseStatusCard()
    yield widget
    widget.close()
    widget.deleteLater()
    qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    qapp.processEvents()


def test_shows_projected_window_when_not_recording(card):
    card.update_status({
        'recording': False, 'frame_count': 0, 'session_path': '', 'elapsed_seconds': 0,
        'window_forecast': "Next window: today 19:12 → 05:48 (10h 36m)  ·  opens in 3h 02m",
    })

    assert card.status_label.text() == "Not recording"
    assert not card.window_label.isHidden()
    assert "19:12 → 05:48" in card.window_label.text()
    assert not card.open_video_btn.isEnabled()


def test_shows_session_and_window_while_recording(card, tmp_path):
    video = tmp_path / "timelapse_20260917.mp4"
    video.write_bytes(b"")
    card.update_status({
        'recording': True, 'frame_count': 12, 'session_path': str(video), 'elapsed_seconds': 544,
        'window_forecast': "Window open: 19:12 → 05:48 (10h 36m)  ·  closes in 9h 50m",
    })

    assert card.status_label.text() == (
        "● Recording  ·  12 frames  ·  00:09:04 elapsed  ·  timelapse_20260917.mp4"
    )
    assert "closes in 9h 50m" in card.window_label.text()
    assert card.open_video_btn.text() == "Show in folder"
    assert card.open_video_btn.isEnabled()


def test_hides_window_line_when_status_has_no_forecast(card):
    card.update_status({'recording': False, 'session_path': ''})

    assert card.window_label.isHidden()


def test_open_button_reveals_file_while_recording(card, tmp_path, monkeypatch):
    video = tmp_path / "timelapse_recording.mp4"
    video.write_bytes(b"")
    card.update_status({
        'recording': True, 'frame_count': 3, 'session_path': str(video), 'elapsed_seconds': 10,
    })

    reveal_calls = []
    open_calls = []
    monkeypatch.setattr(
        "ui.panels.timelapse_status_card.reveal_path",
        lambda path: reveal_calls.append(path),
    )
    monkeypatch.setattr(
        "ui.panels.timelapse_status_card.open_path",
        lambda path: open_calls.append(path),
    )

    card._open_current_video()

    assert reveal_calls == [str(video)]
    assert open_calls == []


def test_open_button_opens_file_when_not_recording(card, tmp_path, monkeypatch):
    video = tmp_path / "timelapse_finished.mp4"
    video.write_bytes(b"")
    card.update_status({
        'recording': False, 'frame_count': 3, 'session_path': str(video), 'elapsed_seconds': 10,
    })

    reveal_calls = []
    open_calls = []
    monkeypatch.setattr(
        "ui.panels.timelapse_status_card.reveal_path",
        lambda path: reveal_calls.append(path),
    )
    monkeypatch.setattr(
        "ui.panels.timelapse_status_card.open_path",
        lambda path: open_calls.append(path),
    )

    card._open_current_video()

    assert open_calls == [str(video)]
    assert reveal_calls == []
