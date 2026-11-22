"""Logs page fits the narrow pane beside the preview (QA, issue #43): the
toolbar no longer clips "Clear" and a long log path does not widen the page,
which has no horizontal scrolling (offscreen Qt)."""
import pytest


@pytest.fixture
def qapp():
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def test_logs_page_needs_less_than_the_narrow_pane(qapp):
    from ui.panels.logs_panel import LogsPanel
    panel = LogsPanel()
    needed = panel.widget().minimumSizeHint().width()
    # The QA run's pane at a 1400 px window was 655 px wide.
    assert needed <= 640, needed
    panel.resize(655, 700)
    panel.show()
    qapp.processEvents()
    assert panel.clear_btn.width() >= panel.clear_btn.sizeHint().width()
    assert panel.export_diag_btn.geometry().right() <= panel.viewport().width()
    panel.hide()
    panel.deleteLater()
