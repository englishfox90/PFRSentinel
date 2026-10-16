"""Tests for ui/dialogs/hemisphere_dialog.py (offscreen Qt)."""
import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QApplication, QWidget

from ui.dialogs.hemisphere_dialog import HemisphereDialog


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def host_window(qapp):
    widget = QWidget()
    widget.resize(900, 700)
    yield widget
    widget.close()
    widget.deleteLater()
    qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    qapp.processEvents()


def _close(qapp, dialog):
    dialog.close()
    dialog.deleteLater()
    qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    qapp.processEvents()


def _radios(dialog, field):
    return {b.property("hemisphere"): b for b in dialog._groups[field].buttons()}


def test_defaults_are_preselected_and_read_back(qapp, host_window):
    dialog = HemisphereDialog(host_window,
                              {"latitude": "31 19 49", "longitude": "100 27 25"},
                              {"latitude": "N", "longitude": "W"})
    try:
        assert set(dialog._groups) == {"latitude", "longitude"}
        assert _radios(dialog, "latitude")["N"].isChecked()
        assert _radios(dialog, "longitude")["W"].isChecked()
        assert dialog.choices() == {"latitude": "N", "longitude": "W"}
        assert dialog.yesButton.text() == "Confirm"
        assert dialog.cancelButton.text() == "Ask me later"
    finally:
        _close(qapp, dialog)


def test_only_pending_fields_get_a_question(qapp, host_window):
    dialog = HemisphereDialog(host_window, {"longitude": "100 27 25"}, {"longitude": "E"})
    try:
        assert list(dialog._groups) == ["longitude"]
        assert dialog.choices() == {"longitude": "E"}
    finally:
        _close(qapp, dialog)


def test_changing_the_selection_changes_the_answer(qapp, host_window):
    dialog = HemisphereDialog(host_window, {"longitude": "100 27 25"}, {"longitude": "E"})
    try:
        _radios(dialog, "longitude")["W"].setChecked(True)
        assert dialog.choices() == {"longitude": "W"}
    finally:
        _close(qapp, dialog)


def test_the_typed_text_is_shown_so_the_user_recognises_it(qapp, host_window):
    dialog = HemisphereDialog(host_window, {"longitude": "100 27 25"}, {"longitude": "W"})
    try:
        texts = []
        for i in range(dialog.viewLayout.count()):
            w = dialog.viewLayout.itemAt(i).widget()
            if w is not None and hasattr(w, "text"):
                texts.append(w.text())
        assert any("100 27 25" in t for t in texts)
    finally:
        _close(qapp, dialog)
