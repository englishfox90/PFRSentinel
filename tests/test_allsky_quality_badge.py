"""
Tests for the calibration quality badge's caution state and the controller's
status line after a guided save (issue #79: a green "Good" over a calibration
that kept failing; a guided result announced as a bare "(preliminary)").
"""
import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QApplication

from services.allsky.calibration_quality import CalibrationQuality
from services.allsky.fisheye import FisheyeModel
from ui.panels.allsky_settings import AllSkySettingsPanel, QualityBadge


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _teardown(qapp, widget):
    widget.close()
    widget.deleteLater()
    qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    qapp.processEvents()


@pytest.fixture
def badge(qapp):
    widget = QualityBadge()
    yield widget
    _teardown(qapp, widget)


@pytest.fixture
def panel(qapp):
    widget = AllSkySettingsPanel()
    widget.show()
    qapp.processEvents()
    yield widget
    _teardown(qapp, widget)


class TestQualityBadge:

    def test_plain_rating_uses_the_rating_colours(self, badge):
        badge.set_quality('good')
        assert badge._label.text() == 'Good'
        assert CalibrationQuality.badge_colors('good')[1] in badge._label.styleSheet()

    def test_attention_turns_a_green_badge_amber_and_says_why(self, badge):
        badge.set_quality('good')
        badge.set_attention('unconfirmed')
        assert badge._label.text() == 'Good — unconfirmed'
        green = CalibrationQuality.badge_colors('good')[1]
        assert green not in badge._label.styleSheet()

    def test_misaligned_asks_for_an_alignment_check(self, badge):
        badge.set_quality('excellent')
        badge.set_attention('misaligned')
        assert badge._label.text() == 'Excellent — check alignment'

    def test_attention_survives_a_quality_update(self, badge):
        badge.set_attention('unconfirmed')
        badge.set_quality('acceptable')
        assert badge._label.text() == 'Acceptable — unconfirmed'

    def test_clearing_attention_restores_the_rating(self, badge):
        badge.set_quality('good')
        badge.set_attention('misaligned')
        badge.set_attention('')
        assert badge._label.text() == 'Good'
        assert CalibrationQuality.badge_colors('good')[1] in badge._label.styleSheet()

    def test_no_calibration_has_nothing_to_caution(self, badge):
        badge.set_quality('none')
        badge.set_attention('misaligned')
        assert badge._label.text() == 'None'

    def test_unknown_level_is_ignored(self, badge):
        badge.set_quality('good')
        badge.set_attention('something-else')
        assert badge._label.text() == 'Good'


class TestPanelAttention:

    def test_message_is_shown_beside_the_badge_and_hidden_when_cleared(self, panel, qapp):
        panel.set_quality('good')
        panel.set_attention('misaligned', "The saved calibration missed the bright stars.")
        qapp.processEvents()
        assert panel._attention_label.isVisible()
        assert 'missed the bright stars' in panel._attention_label.text()

        panel.set_attention('', '')
        qapp.processEvents()
        assert not panel._attention_label.isVisible()
        assert panel._quality_badge._label.text() == 'Good'


class _FakeConfig:
    def __init__(self):
        self.d = {}

    def get(self, key, default=None):
        return self.d.get(key, default)

    def set(self, key, value):
        self.d[key] = value

    def save(self):
        pass


class _FakeNotifier:
    def notify(self, event):
        pass


class _FakeMainWindow:
    def __init__(self):
        self.config = _FakeConfig()
        self.notifier = _FakeNotifier()


def _guided_model():
    model = FisheyeModel(cx=960.0, cy=540.0, a1=320.0, a3=-10.0, a5=0.0,
                         roll=0.1, axis_alt=88.0, axis_az=10.0,
                         rms_residual=4.2, n_matches=7)
    model.provenance = 'guided'
    return model


class TestGuidedSaveStatus:

    @pytest.fixture
    def controller(self, qapp, tmp_path, monkeypatch):
        import services.app_config as app_config
        monkeypatch.setattr(app_config, 'get_calibration_path',
                            lambda: str(tmp_path / "allsky_calibration.json"))
        from ui.controllers.allsky_controller import AllSkyController
        ctrl = AllSkyController(_FakeMainWindow())
        seen = {'status': [], 'quality': []}
        ctrl.status_changed.connect(seen['status'].append)
        ctrl.quality_changed.connect(seen['quality'].append)
        yield ctrl, seen
        ctrl.shutdown()
        ctrl.deleteLater()
        qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        qapp.processEvents()

    def test_guided_save_says_what_it_is_and_what_happens_next(self, controller):
        ctrl, seen = controller
        ctrl._on_calibration_done(_guided_model())
        status = seen['status'][-1]
        assert status.startswith("Guided calibration saved: 7 stars")
        assert "refinement" in status

    def test_badge_follows_the_new_guided_model_not_the_old_rating(self, controller):
        """The reported 'Good persists': whatever the previous model rated,
        the badge must end on the guided model's own level."""
        ctrl, seen = controller
        ctrl._cal_service._quality = 'good'
        ctrl._on_calibration_done(_guided_model())
        assert seen['quality'][-1] == 'preliminary'

    def test_rescue_note_reaches_the_status_line(self, controller):
        ctrl, seen = controller
        model = _guided_model()
        model.guided_note = "Corrected identification: 'Pollux' is actually Sirius."
        ctrl._on_calibration_done(model)
        assert "Sirius" in seen['status'][-1]
