"""
Wiring for the all-sky equipment map and label memory (issue #93, package 1):
the Reset Equipment Map button, the controller's reset / save paths, and the
label-state resets on a model change and at capture start.
Offscreen Qt.
"""
import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QApplication

import numpy as np

from services.allsky.fisheye import FisheyeModel
from services.allsky.label_stability import get_label_stabilizer, reset_label_stability
from services.allsky.obstruction_map import get_obstruction_map


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture(autouse=True)
def _clean_state():
    get_obstruction_map().reset()
    reset_label_stability()
    yield
    get_obstruction_map().reset()
    reset_label_stability()


@pytest.fixture
def map_path(tmp_path, monkeypatch):
    import services.app_config as app_config
    path = tmp_path / "allsky_obstruction.npz"
    monkeypatch.setattr(app_config, 'get_obstruction_map_path', lambda: str(path))
    monkeypatch.setattr(app_config, 'get_calibration_path',
                        lambda: str(tmp_path / "allsky_calibration.json"))
    return path


def _teach_map(frames: int = 3) -> None:
    mask = np.zeros((200, 200), dtype=np.uint8)
    mask[:, 100:] = 255
    for _ in range(frames):
        get_obstruction_map().update(mask, n_detections=60, frame_is_observable=True,
                                     reach_mask=mask > 0,
                                     sky_region=np.ones((200, 200), dtype=bool))


def _model() -> FisheyeModel:
    return FisheyeModel(cx=960.0, cy=540.0, a1=600.0, rms_residual=4.0, n_matches=50,
                        n_images=5, span_minutes=30.0,
                        calibrated_at="2026-01-01T00:00:00+00:00")


class _FakeConfig:
    def __init__(self):
        self._data = {}

    def get(self, key, default=None):
        return self._data.get(key, default)

    def set(self, key, value):
        self._data[key] = value

    def save(self):
        pass


class _FakeMainWindow:
    def __init__(self):
        self.config = _FakeConfig()


@pytest.fixture
def controller(qapp, map_path):
    from ui.controllers.allsky_controller import AllSkyController
    ctrl = AllSkyController(_FakeMainWindow())
    ctrl._cal_service._save_model = lambda m: None
    yield ctrl
    ctrl.deleteLater()
    qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    qapp.processEvents()


class TestPanel:
    def test_reset_map_button_asks_then_emits_the_action(self, qapp, monkeypatch):
        from ui.panels import allsky_settings
        panel = allsky_settings.AllSkySettingsPanel()
        try:
            monkeypatch.setattr(allsky_settings.MessageBox, 'exec', lambda self: True)
            actions = []
            panel.settings_changed.connect(actions.append)
            panel._reset_map_btn.click()
            assert actions == [{'_action': 'reset_equipment_map'}]
        finally:
            panel.deleteLater()
            qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            qapp.processEvents()

    def test_cancelled_confirmation_does_nothing(self, qapp, monkeypatch):
        from ui.panels import allsky_settings
        panel = allsky_settings.AllSkySettingsPanel()
        try:
            monkeypatch.setattr(allsky_settings.MessageBox, 'exec', lambda self: False)
            actions = []
            panel.settings_changed.connect(actions.append)
            panel._reset_map_btn.click()
            assert actions == []
        finally:
            panel.deleteLater()
            qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            qapp.processEvents()


class TestController:
    def test_map_is_loaded_at_startup(self, qapp, map_path):
        _teach_map(3)
        get_obstruction_map().save(str(map_path))
        get_obstruction_map().reset()
        from ui.controllers.allsky_controller import AllSkyController
        ctrl = AllSkyController(_FakeMainWindow())
        try:
            assert get_obstruction_map().frames_seen == 3
        finally:
            ctrl.deleteLater()

    def test_reset_equipment_map_clears_memory_and_file(self, controller, map_path):
        _teach_map(3)
        get_obstruction_map().save(str(map_path))
        assert map_path.exists()
        statuses = []
        controller.status_changed.connect(statuses.append)
        controller.reset_equipment_map()
        assert get_obstruction_map().frames_seen == 0
        assert not map_path.exists()
        assert statuses and 'Equipment map reset' in statuses[-1]

    def test_reset_calibration_also_clears_the_map(self, controller, map_path):
        _teach_map(3)
        get_obstruction_map().save(str(map_path))
        controller.reset_calibration()
        assert get_obstruction_map().frames_seen == 0
        assert not map_path.exists()

    def test_capture_stop_saves_what_was_learned(self, controller, map_path):
        _teach_map(3)
        assert not map_path.exists()
        controller.on_capture_stopped()
        assert map_path.exists()
        controller.on_capture_stopped()   # nothing new: no rewrite needed
        assert map_path.exists()

    def test_shutdown_saves_too(self, controller, map_path):
        _teach_map(2)
        controller.shutdown()
        assert map_path.exists()


class TestLabelStateResets:
    def _dirty_labels(self):
        stab = get_label_stabilizer()
        stab.smooth_mask(np.full((4, 4), 255, dtype=np.uint8))
        stab.select(['a', 'b'], 2)
        stab.slot_memory['a'] = 1
        assert stab.masks.depth == 1 and stab.selection.shown

    def test_set_model_forgets_label_memory(self):
        from services.allsky.calibration_service import CalibrationService
        svc = CalibrationService()
        svc._save_model = lambda m: None
        self._dirty_labels()
        svc.set_model(_model())
        stab = get_label_stabilizer()
        assert stab.masks.depth == 0 and not stab.selection.shown and not stab.slot_memory

    def test_clear_model_forgets_label_memory(self):
        from services.allsky.calibration_service import CalibrationService
        svc = CalibrationService()
        svc._model = _model()
        self._dirty_labels()
        svc.clear_model()
        assert get_label_stabilizer().masks.depth == 0

    def test_capture_start_forgets_label_memory(self, qapp):
        """A session's label memory starts empty (H12: the singleton used to
        carry the previous session's vote and picks across a restart)."""
        from unittest.mock import MagicMock
        from ui.main_window.capture import _MainWindowCaptureMixin
        self._dirty_labels()
        window = MagicMock()
        window.config.get.return_value = 'watch'
        _MainWindowCaptureMixin.start_capture(window)
        window._start_watch_mode.assert_called_once()
        assert get_label_stabilizer().masks.depth == 0

    def test_capture_stop_reaches_the_allsky_controller(self, qapp):
        from unittest.mock import MagicMock
        from ui.main_window.capture import _MainWindowCaptureMixin
        window = MagicMock()
        window.config.get.return_value = 'watch'
        _MainWindowCaptureMixin.stop_capture(window)
        window.allsky_controller.on_capture_stopped.assert_called_once()
