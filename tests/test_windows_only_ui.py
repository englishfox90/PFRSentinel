"""Tests for Windows-only UI gating (issue #37): the ffmpeg install card's
platform-specific install path, and the missing-camera notice's Revive
(USB reset) gating — Revive is a Win32-only capability (tracking issue #1)."""
import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QApplication

from ui.panels.ffmpeg_install_card import FfmpegInstallCard
from ui.panels._missing_camera_notice import MissingCameraNotice
import ui.panels.ffmpeg_install_card as ffmpeg_install_card_module
import ui.panels._missing_camera_notice as missing_camera_notice_module


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _flush(qapp, widget):
    widget.close()
    widget.deleteLater()
    qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    qapp.processEvents()


# --------------------------------------------------------------------- #
#  FfmpegInstallCard                                                     #
# --------------------------------------------------------------------- #

class TestFfmpegInstallCardOffWindows:
    def test_shows_command_line_and_hides_winget_button(self, qapp, monkeypatch):
        monkeypatch.setattr(ffmpeg_install_card_module, 'IS_WINDOWS', False)
        monkeypatch.setattr(ffmpeg_install_card_module, 'is_winget_available', lambda: False)
        monkeypatch.setattr(
            ffmpeg_install_card_module, 'ffmpeg_install_command', lambda: 'brew install ffmpeg'
        )
        monkeypatch.setattr(
            ffmpeg_install_card_module, 'ffmpeg_install_hint',
            lambda: 'Install it with Homebrew, then restart PFR Sentinel:'
        )

        card = FfmpegInstallCard()
        try:
            assert card._winget_btn is None
            assert card._command_input.text() == 'brew install ffmpeg'
            assert card._command_input.isReadOnly()
        finally:
            _flush(qapp, card)


class TestFfmpegInstallCardOnWindowsWithWinget:
    def test_shows_winget_button(self, qapp, monkeypatch):
        monkeypatch.setattr(ffmpeg_install_card_module, 'IS_WINDOWS', True)
        monkeypatch.setattr(ffmpeg_install_card_module, 'is_winget_available', lambda: True)

        card = FfmpegInstallCard()
        try:
            assert card._winget_btn is not None
            assert not card._winget_btn.isHidden()
            assert not hasattr(card, '_command_input')
        finally:
            _flush(qapp, card)


# --------------------------------------------------------------------- #
#  MissingCameraNotice                                                   #
# --------------------------------------------------------------------- #

class TestMissingCameraNoticeRevive:
    def test_off_windows_hides_revive_shows_detect_again(self, qapp, monkeypatch):
        monkeypatch.setattr(missing_camera_notice_module, 'IS_WINDOWS', False)

        notice = MissingCameraNotice()
        try:
            notice.set_warning('ASI676MC', phantom_count=2)
            assert notice._revive_btn.isHidden()
            assert not notice._detect_btn.isHidden()
            assert "Unplug the camera" in notice._label.text()
        finally:
            _flush(qapp, notice)

    def test_on_windows_shows_revive(self, qapp, monkeypatch):
        monkeypatch.setattr(missing_camera_notice_module, 'IS_WINDOWS', True)

        notice = MissingCameraNotice()
        try:
            notice.set_warning('ASI676MC', phantom_count=2)
            assert not notice._revive_btn.isHidden()
            assert notice._detect_btn.isHidden()
            assert "Try Revive" in notice._label.text()
        finally:
            _flush(qapp, notice)

    def test_simple_disconnect_always_shows_detect_again(self, qapp, monkeypatch):
        monkeypatch.setattr(missing_camera_notice_module, 'IS_WINDOWS', True)

        notice = MissingCameraNotice()
        try:
            notice.set_warning('ASI676MC', phantom_count=0)
            assert notice._revive_btn.isHidden()
            assert not notice._detect_btn.isHidden()
        finally:
            _flush(qapp, notice)


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
