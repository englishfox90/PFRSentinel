"""ImageProcessingPanel — the Auto Stretch rows after issue #13: Target Median
reaches the engine floor, and Dark Threshold is tied to the switch that is
the only thing that reads it."""
import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QApplication

from services.image_stretch import TARGET_MEDIAN_MIN
from ui.panels.image_processing import ImageProcessingPanel


class _FakeConfig:
    def __init__(self, data=None):
        self.data = dict(data or {})

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value):
        self.data[key] = value


class _FakeMainWindow:
    def __init__(self, config):
        self.config = config


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def panel(qapp):
    widget = ImageProcessingPanel()
    widget.main_window = _FakeMainWindow(_FakeConfig({'auto_stretch': {}}))
    yield widget
    widget.close()
    widget.deleteLater()
    qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    qapp.processEvents()


def test_target_median_slider_reaches_the_engine_floor(panel):
    assert panel.target_median_slider.minimum() == round(TARGET_MEDIAN_MIN * 100)

    panel.target_median_slider.setValue(panel.target_median_slider.minimum())

    assert panel.target_median_label.text() == f"{TARGET_MEDIAN_MIN:.2f}"
    assert panel.main_window.config.get('auto_stretch')['target_median'] == pytest.approx(TARGET_MEDIAN_MIN)


def test_dark_threshold_follows_the_color_fix_switch(panel):
    panel.normalize_channels_switch.set_checked(True)
    assert panel.dark_threshold_row.isEnabled()

    panel.normalize_channels_switch.set_checked(False)

    assert not panel.dark_threshold_row.isEnabled()
    assert panel.main_window.config.get('auto_stretch')['normalize_channels'] is False


def test_loading_config_with_color_fix_off_disables_dark_threshold(panel):
    panel.load_from_config(_FakeConfig({'auto_stretch': {'normalize_channels': False}}))

    assert not panel.dark_threshold_row.isEnabled()


def test_shadow_hint_says_higher_is_darker(panel):
    tip = panel.shadow_slider.toolTip().lower()
    assert "higher clips less and keeps the sky dark" in tip
    assert "lower clips more" in tip and "brighter sky" in tip
