"""
AllSkyController.remember_display_stretch (PR #127 review): the guided
dialog's Stretch slider is persisted under allsky_overlay, and like every
other controller write to that section it must announce itself through
settings_changed. The All-Sky panel writes the whole section back from its
own loaded snapshot on its next edit, and that snapshot is only refreshed by
the signal — without it, any later unrelated All-Sky edit reverted the
saved stretch.
Offscreen Qt.
"""
import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QApplication

from services.config_defaults import DEFAULT_CONFIG


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


class _FakeConfig:
    def __init__(self):
        self._data = {}
        self.saves = 0

    def get(self, key, default=None):
        return self._data.get(key, default)

    def set(self, key, value):
        self._data[key] = value

    def save(self):
        self.saves += 1


class _FakeMainWindow:
    def __init__(self):
        self.config = _FakeConfig()


@pytest.fixture
def controller(qapp, tmp_path, monkeypatch):
    import services.app_config as app_config
    monkeypatch.setattr(app_config, 'get_obstruction_map_path',
                        lambda: str(tmp_path / "allsky_obstruction.npz"))
    monkeypatch.setattr(app_config, 'get_calibration_path',
                        lambda: str(tmp_path / "allsky_calibration.json"))
    from ui.controllers.allsky_controller import AllSkyController
    ctrl = AllSkyController(_FakeMainWindow())
    ctrl._cal_service._save_model = lambda m: None
    yield ctrl
    ctrl.deleteLater()
    qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    qapp.processEvents()


def test_a_new_strength_is_saved_and_announced(controller):
    announced = []
    controller.settings_changed.connect(lambda: announced.append(True))
    controller._mw.config.set('allsky_overlay', {'top_n': 20})

    controller.remember_display_stretch(0.8)

    cfg = controller._mw.config.get('allsky_overlay')
    assert cfg['guided_display_stretch'] == 0.8
    assert cfg['top_n'] == 20
    assert controller._mw.config.saves == 1
    assert announced == [True]


def test_the_same_strength_again_is_neither_saved_nor_announced(controller):
    announced = []
    controller.settings_changed.connect(lambda: announced.append(True))
    controller.remember_display_stretch(0.8)
    saves = controller._mw.config.saves

    controller.remember_display_stretch(0.8)

    assert controller._mw.config.saves == saves
    assert len(announced) == 1


def test_the_strength_is_clamped_and_rounded(controller):
    controller.remember_display_stretch(1.7)
    assert controller._mw.config.get('allsky_overlay')['guided_display_stretch'] == 1.0
    controller.remember_display_stretch(0.123456)
    assert controller._mw.config.get('allsky_overlay')['guided_display_stretch'] == 0.12


def test_the_default_lives_in_the_config_defaults():
    assert DEFAULT_CONFIG['allsky_overlay']['guided_display_stretch'] == 0.5
