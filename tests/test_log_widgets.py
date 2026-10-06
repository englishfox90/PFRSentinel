"""Tests for the bounded log widgets.

Both log views used to grow all night: they appended one HTML block per line
and then trimmed with a cursor + removeSelectedText(). With undo/redo enabled
that frees nothing — QTextDocument keeps the insert *and* the removal on the
undo stack — so memory climbed with every line. The fix is
setUndoRedoEnabled(False) + document().setMaximumBlockCount(cap).

Constructs real widgets against an offscreen QApplication, mirroring the
pattern in tests/test_library_live_ui.py (no display needed).
"""
import os

import pytest

pytest.importorskip("PySide6")


@pytest.fixture(scope="module")
def qt_app():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def _dispose(widget, app):
    from PySide6.QtCore import QEvent
    widget.close()
    widget.deleteLater()
    app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()


@pytest.fixture
def logs_panel(qt_app):
    from ui.panels.logs_panel import LogsPanel
    panel = LogsPanel()
    yield panel
    _dispose(panel, qt_app)


@pytest.fixture
def activity_log(qt_app):
    from ui.panels.live_monitoring import ActivityLog
    widget = ActivityLog()
    yield widget
    _dispose(widget, qt_app)


def _lines(level, count, start=0):
    return [f"[00:00:{i % 60:02d}] {level}: message {i}" for i in range(start, start + count)]


class TestLogsPanel:
    def test_undo_redo_disabled(self, logs_panel):
        assert logs_panel.log_text.isUndoRedoEnabled() is False
        assert logs_panel.log_text.document().isUndoRedoEnabled() is False

    def test_max_block_count_set_to_cap(self, logs_panel):
        assert logs_panel.log_text.document().maximumBlockCount() == logs_panel._max_lines

    def test_block_count_stays_capped_past_three_times_the_cap(self, logs_panel):
        cap = logs_panel._max_lines
        logs_panel.append_logs(_lines("INFO", cap * 3))
        assert logs_panel.log_text.document().blockCount() <= cap + 1

    def test_block_count_capped_across_many_small_batches(self, logs_panel):
        cap = logs_panel._max_lines
        for batch in range(30):
            logs_panel.append_logs(_lines("INFO", cap // 10, start=batch * (cap // 10)))
        assert logs_panel.log_text.document().blockCount() <= cap + 1

    def test_newest_lines_survive_the_trim(self, logs_panel):
        cap = logs_panel._max_lines
        logs_panel.append_logs(_lines("INFO", cap * 2))
        text = logs_panel.log_text.toPlainText()
        assert f"message {cap * 2 - 1}" in text
        assert "message 0\n" not in text

    def test_level_filter_still_applies(self, logs_panel):
        logs_panel.level_filter.setCurrentText("INFO")
        logs_panel.append_logs(_lines("DEBUG", 5))
        assert logs_panel.log_text.document().blockCount() == 1  # empty doc
        logs_panel.level_filter.setCurrentText("DEBUG")
        logs_panel.append_logs(_lines("DEBUG", 5))
        assert "DEBUG" in logs_panel.log_text.toPlainText()

    def test_search_filter_still_applies(self, logs_panel):
        logs_panel.search_input.setText("keep-me")
        logs_panel.append_logs(["[00:00:00] INFO: drop this", "[00:00:01] INFO: keep-me please"])
        text = logs_panel.log_text.toPlainText()
        assert "keep-me please" in text
        assert "drop this" not in text

    def test_search_refilters_lines_already_shown(self, logs_panel):
        logs_panel.level_filter.setCurrentText("DEBUG")
        logs_panel.append_logs([
            "[00:00:00] DEBUG: HTTP 127.0.0.1 GET /latest",
            "[00:00:01] INFO: NINA target set via HTTP API: M31",
        ])
        logs_panel.search_input.setText("nina")
        logs_panel._refilter_timer.timeout.emit()
        text = logs_panel.log_text.toPlainText()
        assert "NINA target set" in text
        assert "GET /latest" not in text
        assert logs_panel.match_label.text() == "1 match"

    def test_clearing_search_brings_history_back(self, logs_panel):
        logs_panel.level_filter.setCurrentText("DEBUG")
        logs_panel.search_input.setText("nina")
        logs_panel.append_logs(["[00:00:00] INFO: other line", "[00:00:01] INFO: NINA up"])
        assert "other line" not in logs_panel.log_text.toPlainText()
        logs_panel.search_input.setText("")
        logs_panel._refilter_timer.timeout.emit()
        assert "other line" in logs_panel.log_text.toPlainText()
        assert logs_panel.match_label.text() == ""

    def test_search_reaches_lines_past_the_visible_cap(self, logs_panel):
        logs_panel.level_filter.setCurrentText("DEBUG")
        logs_panel.append_logs(["[00:00:00] INFO: rare needle"])
        logs_panel.append_logs(_lines("DEBUG", logs_panel._max_lines * 2))
        assert "rare needle" not in logs_panel.log_text.toPlainText()
        logs_panel.search_input.setText("needle")
        logs_panel._refilter_timer.timeout.emit()
        assert "rare needle" in logs_panel.log_text.toPlainText()

    def test_level_change_refilters_history(self, logs_panel):
        logs_panel.level_filter.setCurrentText("INFO")
        logs_panel.append_logs(_lines("DEBUG", 3))
        assert "DEBUG" not in logs_panel.log_text.toPlainText()
        logs_panel.level_filter.setCurrentText("DEBUG")
        assert "DEBUG" in logs_panel.log_text.toPlainText()

    def test_new_matches_counted_and_shown(self, logs_panel):
        logs_panel.level_filter.setCurrentText("DEBUG")
        logs_panel.search_input.setText("nina")
        logs_panel._refilter_timer.timeout.emit()
        logs_panel.append_logs(["[00:00:00] INFO: NINA a", "[00:00:01] INFO: nope", "[00:00:02] DEBUG: nina b"])
        assert logs_panel.match_label.text() == "2 matches"
        assert "nope" not in logs_panel.log_text.toPlainText()

    def test_debug_level_shows_every_level(self, logs_panel):
        logs_panel.level_filter.setCurrentText("DEBUG")
        logs_panel.append_logs([
            "[00:00:00] DEBUG: d", "[00:00:01] INFO: NINA target set",
            "[00:00:02] WARN: w", "[00:00:03] ERROR: e",
        ])
        text = logs_panel.log_text.toPlainText()
        for part in ("DEBUG: d", "INFO: NINA target set", "WARN: w", "ERROR: e"):
            assert part in text

    def test_warn_level_hides_info_and_debug(self, logs_panel):
        logs_panel.level_filter.setCurrentText("WARN")
        logs_panel.append_logs(["[00:00:00] INFO: i", "[00:00:01] WARN: w", "[00:00:02] ERROR: e"])
        text = logs_panel.log_text.toPlainText()
        assert "INFO: i" not in text
        assert "WARN: w" in text and "ERROR: e" in text

    def test_legacy_saved_levels_load_as_thresholds(self, logs_panel):
        logs_panel.load_from_config({'ui_log_level': 'All'})
        assert logs_panel.level_filter.currentText() == "DEBUG"
        logs_panel.load_from_config({'ui_log_level': 'Info+'})
        assert logs_panel.level_filter.currentText() == "INFO"

    def test_angle_brackets_are_shown_not_parsed(self, logs_panel):
        logs_panel.append_logs(["[00:00:00] INFO: called <lambda> ok"])
        assert "<lambda>" in logs_panel.log_text.toPlainText()

    def test_clear_forgets_history(self, logs_panel):
        logs_panel.append_logs(["[00:00:00] INFO: gone"])
        logs_panel._clear_logs()
        logs_panel.level_filter.setCurrentText("DEBUG")
        assert "gone" not in logs_panel.log_text.toPlainText()

    def test_empty_batch_is_a_noop(self, logs_panel):
        before = logs_panel.log_text.document().blockCount()
        logs_panel.append_logs([])
        assert logs_panel.log_text.document().blockCount() == before

    def test_updates_are_re_enabled_after_a_batch(self, logs_panel):
        logs_panel.append_logs(_lines("INFO", 3))
        assert logs_panel.log_text.updatesEnabled() is True


class TestActivityLog:
    def test_undo_redo_disabled(self, activity_log):
        assert activity_log.text_area.isUndoRedoEnabled() is False
        assert activity_log.text_area.document().isUndoRedoEnabled() is False

    def test_max_block_count_set_to_cap(self, activity_log):
        assert activity_log.text_area.document().maximumBlockCount() == activity_log._max_lines

    def test_block_count_stays_capped_past_three_times_the_cap(self, activity_log):
        cap = activity_log._max_lines
        activity_log.append_logs(_lines("INFO", cap * 3))
        assert activity_log.text_area.document().blockCount() <= cap + 1

    def test_debug_lines_are_dropped(self, activity_log):
        activity_log.append_logs(_lines("DEBUG", 20))
        assert activity_log.text_area.toPlainText().strip() == ""

    def test_info_and_above_are_kept(self, activity_log):
        activity_log.append_logs([
            "[00:00:00] INFO: kept info",
            "[00:00:01] DEBUG: dropped debug",
            "[00:00:02] WARN: kept warn",
            "[00:00:03] ERROR: kept error",
        ])
        text = activity_log.text_area.toPlainText()
        assert "kept info" in text
        assert "kept warn" in text
        assert "kept error" in text
        assert "dropped debug" not in text

    def test_single_append_log_drops_debug(self, activity_log):
        activity_log.append_log("[00:00:00] DEBUG: nope")
        assert activity_log.text_area.toPlainText().strip() == ""
        activity_log.append_log("[00:00:01] INFO: yep")
        assert "yep" in activity_log.text_area.toPlainText()

    def test_message_body_containing_the_word_debug_is_kept(self, activity_log):
        activity_log.append_log("[00:00:00] INFO: DEBUG mode enabled")
        assert "DEBUG mode enabled" in activity_log.text_area.toPlainText()

    def test_unformatted_message_is_kept(self, activity_log):
        activity_log.append_log("plain message with no level")
        assert "plain message" in activity_log.text_area.toPlainText()

    def test_empty_batch_is_a_noop(self, activity_log):
        before = activity_log.text_area.document().blockCount()
        activity_log.append_logs([])
        assert activity_log.text_area.document().blockCount() == before

    def test_updates_are_re_enabled_after_a_batch(self, activity_log):
        activity_log.append_logs(_lines("INFO", 3))
        assert activity_log.text_area.updatesEnabled() is True
