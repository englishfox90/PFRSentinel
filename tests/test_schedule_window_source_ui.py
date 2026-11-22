"""Tests for the Scheduled Capture window-source rows (GitHub issue #14)."""
import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication

from ui.panels._schedule_window_source import ScheduleWindowSourceRows


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


class _StubConfig:
    """Minimal stand-in for services.config.Config: get/set over a dict."""

    def __init__(self, data=None):
        self._data = data or {}

    def get(self, key, default=None):
        return self._data.get(key, default)

    def set(self, key, value):
        self._data[key] = value


def _fixed_config():
    return _StubConfig({
        "scheduled_window_source": "fixed",
        "scheduled_window_margin_min": 15,
    })


def _timelapse_config(margin=15):
    return _StubConfig({
        "scheduled_window_source": "timelapse",
        "scheduled_window_margin_min": margin,
        "timelapse": {"window_mode": "always"},
        "weather": {},
    })


def test_load_fixed_source_hides_margin_and_preview(qapp):
    rows = ScheduleWindowSourceRows()
    rows.load(_fixed_config())

    assert rows.source() == "fixed"
    assert not rows.uses_timelapse()
    assert rows.margin_row.isHidden()
    assert rows.preview_label.isHidden()


def test_load_timelapse_source_shows_margin_and_preview(qapp):
    rows = ScheduleWindowSourceRows()
    rows.load(_timelapse_config(margin=20))

    assert rows.source() == "timelapse"
    assert rows.uses_timelapse()
    assert not rows.margin_row.isHidden()
    assert not rows.preview_label.isHidden()
    assert rows.margin_spin.value() == 20
    assert "timelapse window" in rows.preview_label.text()


def test_load_does_not_emit_signals(qapp):
    rows = ScheduleWindowSourceRows()
    seen = []
    rows.source_changed.connect(lambda s: seen.append(("source", s)))
    rows.margin_changed.connect(lambda v: seen.append(("margin", v)))

    rows.load(_timelapse_config())

    assert seen == []


def test_changing_combo_emits_source_changed_and_shows_margin(qapp):
    rows = ScheduleWindowSourceRows()
    rows.load(_fixed_config())

    seen = []
    rows.source_changed.connect(seen.append)
    rows.source_combo.setCurrentIndex(1)  # "Same as Timelapse"

    assert seen == ["timelapse"]
    assert rows.uses_timelapse()
    assert not rows.margin_row.isHidden()


def test_changing_spin_emits_margin_changed(qapp):
    rows = ScheduleWindowSourceRows()
    rows.load(_timelapse_config())

    seen = []
    rows.margin_changed.connect(seen.append)
    rows.margin_spin.setValue(45)

    assert seen == [45]


@pytest.mark.parametrize("mode, needle", [
    ("always", "Time between captures"),
    ("gated", "inside the Scheduled Capture window"),
    ("variable", "in-window interval applies"),
])
def test_interval_hint_says_when_the_schedule_sets_the_rate(qapp, tmp_path, mode, needle):
    """QA #43: with "Different rate within time window" the typed Interval only
    applies outside the window; the Interval row now says so (display only)."""
    from PySide6.QtWidgets import QWidget
    from qfluentwidgets import CaptionLabel
    from services.config import Config
    from ui.panels._camera_settings_widget import CameraSettingsWidget
    config = Config(str(tmp_path / "config.json"))
    config.set('scheduled_capture_mode', mode)
    host = QWidget()
    host.config = config
    widget = CameraSettingsWidget(host)
    widget.load_from_config(config)
    hint = widget.interval_row.findChildren(CaptionLabel)[0]
    assert needle in hint.text()
    combo = widget.schedule_card.schedule_mode_combo
    combo.setCurrentIndex(0)
    assert hint.text() == "Time between captures"
    host.deleteLater()
