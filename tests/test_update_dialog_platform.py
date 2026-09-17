"""Tests for ui/dialogs/update_dialog.py — the non-Windows install gate (issue #37)."""
import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QApplication, QWidget

import ui.dialogs.update_dialog as update_dialog_module
from services.update_checker import UpdateInfo


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def host_window(qapp):
    # MessageBoxBase reads parent.width()/height() while sizing itself, so it
    # needs a real widget rather than None.
    widget = QWidget()
    widget.resize(900, 700)
    yield widget
    widget.close()
    widget.deleteLater()
    qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    qapp.processEvents()


def _make_update_info():
    return UpdateInfo(
        current_version="3.7.0",
        latest_version="3.8.0",
        release_name="v3.8.0",
        release_notes="Notes",
        download_url="https://example.com/PFRSentinel-setup.exe",
        installer_name="PFRSentinel-setup.exe",
        installer_size_mb=42.0,
        published_at="2026-09-01T00:00:00Z",
        html_url="https://github.com/englishfox90/PFRSentinel/releases/tag/v3.8.0",
    )


def _close(qapp, dialog):
    dialog.close()
    dialog.deleteLater()
    qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    qapp.processEvents()


def _view_layout_texts(dialog):
    texts = []
    for i in range(dialog.viewLayout.count()):
        w = dialog.viewLayout.itemAt(i).widget()
        if w is not None and hasattr(w, "text"):
            texts.append(w.text())
    return texts


def test_download_hidden_and_caption_shown_off_windows(qapp, host_window, monkeypatch):
    monkeypatch.setattr(update_dialog_module, "IS_WINDOWS", False)

    dialog = update_dialog_module.UpdateDialog(host_window, _make_update_info())
    try:
        assert dialog.download_btn.isHidden()
        assert any(
            "arrive in a later release" in t for t in _view_layout_texts(dialog)
        )
    finally:
        _close(qapp, dialog)


def test_download_visible_on_windows(qapp, host_window, monkeypatch):
    monkeypatch.setattr(update_dialog_module, "IS_WINDOWS", True)

    dialog = update_dialog_module.UpdateDialog(host_window, _make_update_info())
    try:
        assert not dialog.download_btn.isHidden()
        assert not any(
            "arrive in a later release" in t for t in _view_layout_texts(dialog)
        )
    finally:
        _close(qapp, dialog)


def test_run_installer_is_a_no_op_off_windows(qapp, host_window, monkeypatch, tmp_path):
    monkeypatch.setattr(update_dialog_module, "IS_WINDOWS", False)

    dialog = update_dialog_module.UpdateDialog(host_window, _make_update_info())
    try:
        installer = tmp_path / "setup.exe"
        installer.write_bytes(b"")
        dialog._downloaded_path = str(installer)

        # subprocess.DETACHED_PROCESS / CREATE_NEW_PROCESS_GROUP don't exist
        # off Windows, so reaching that line would raise AttributeError —
        # the early return (gated on IS_WINDOWS) must fire before it.
        dialog._on_run_installer()
    finally:
        _close(qapp, dialog)
