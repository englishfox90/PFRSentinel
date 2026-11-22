"""A refused RAW16 change puts the Capture tab's switch back (offscreen Qt).

The switch lives on the camera settings widget, not on CaptureSettingsPanel;
the revert used to raise AttributeError inside the slot (issue #43 plan,
§1.11 item 2).
"""
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def capture_panel():
    try:
        from PySide6.QtWidgets import QApplication
    except ImportError:
        pytest.skip("PySide6 not installed")
    _app = QApplication.instance() or QApplication([])
    from ui.panels.capture_settings import CaptureSettingsPanel
    panel = CaptureSettingsPanel(None)
    config = MagicMock()
    config.get = MagicMock(side_effect=lambda key, default=None: {} if key == 'dev_mode' else default)
    panel.camera_widget.main_window = SimpleNamespace(config=config)
    panel.camera_widget.raw16_switch.setEnabled(True)
    yield panel
    panel.deleteLater()


def _window(panel):
    return SimpleNamespace(capture_panel=panel)


def test_refused_raw16_change_unticks_the_switch(capture_panel):
    from ui.main_window.capture import _MainWindowCaptureMixin
    switch = capture_panel.camera_widget.raw16_switch
    requests = []
    capture_panel.camera_widget.raw16_mode_changed.connect(requests.append)
    switch.set_checked(True)
    assert requests == [True]

    _MainWindowCaptureMixin._on_raw16_mode_done(_window(capture_panel), True, False)

    assert switch.is_checked() is False
    assert requests == [True], "the revert must not ask for another mode change"
    assert capture_panel.camera_widget._loading_config is False


def test_accepted_raw16_change_leaves_the_switch(capture_panel):
    from ui.main_window.capture import _MainWindowCaptureMixin
    switch = capture_panel.camera_widget.raw16_switch
    switch.set_checked(True)

    _MainWindowCaptureMixin._on_raw16_mode_done(_window(capture_panel), True, True)

    assert switch.is_checked() is True
