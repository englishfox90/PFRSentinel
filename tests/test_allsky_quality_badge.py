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


class TestQualityBadgeNote:
    """#93: a 60-frame chance fit is capped at Preliminary, whose stock
    description reads "Single image — rough overlay". The tooltip must say
    why instead."""

    NOTE = ("matches no better than chance: RMS 10.9 px is 0.66 of the "
            "16.5 px match tolerance")

    def test_note_reaches_the_tooltip(self, badge):
        badge.set_quality('preliminary')
        badge.set_note(self.NOTE)
        assert badge._label.text() == 'Preliminary'
        assert self.NOTE in badge.toolTip()
        assert badge.toolTip().startswith(
            CalibrationQuality.description('preliminary'))

    def test_note_survives_a_quality_update_and_clears(self, badge):
        badge.set_note(self.NOTE)
        badge.set_quality('preliminary')
        assert self.NOTE in badge.toolTip()
        badge.set_note('')
        assert badge.toolTip() == CalibrationQuality.description('preliminary')

    def test_no_calibration_carries_no_note(self, badge):
        badge.set_note(self.NOTE)
        badge.set_quality('none')
        assert self.NOTE not in badge.toolTip()

    def test_panel_forwards_the_note(self, panel):
        panel.set_quality('preliminary')
        panel.set_quality_note(self.NOTE)
        assert self.NOTE in panel._quality_badge.toolTip()

    def test_live_verdict_reads_capped_level_reason_and_saved_rating(self, panel):
        """The streak path: badge_quality_changed(level, note) with the
        'misaligned' caution. The note carries the measurement and names the
        saved rating itself, so the generic suffix is not added on top."""
        verdict = ("Matched 601 stars vs 620 expected by chance at 15.5 px in "
                   "2 consecutive runs (rating from when it was saved: good)")
        panel.set_quality('good')
        panel.set_attention('misaligned', "no better than chance")
        panel.set_badge_quality('preliminary', verdict)
        badge = panel._quality_badge
        assert badge._label.text() == 'Preliminary — check alignment'
        assert badge.toolTip() == (
            f"{CalibrationQuality.description('preliminary')}. {verdict}")
        panel.set_badge_quality('good', '')
        panel.set_attention('', '')
        assert badge._label.text() == 'Good'
        assert badge.toolTip() == CalibrationQuality.description('good')


class TestControllerQualityNote:

    @pytest.fixture
    def controller(self, qapp, tmp_path, monkeypatch):
        import services.app_config as app_config
        monkeypatch.setattr(app_config, 'get_calibration_path',
                            lambda: str(tmp_path / "allsky_calibration.json"))
        from ui.controllers.allsky_controller import AllSkyController
        ctrl = AllSkyController(_FakeMainWindow())
        seen = {'quality': [], 'note': []}
        ctrl.quality_changed.connect(seen['quality'].append)
        ctrl.quality_note_changed.connect(seen['note'].append)
        yield ctrl, seen
        ctrl.shutdown()
        ctrl.deleteLater()
        qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        qapp.processEvents()

    def _reporter_model(self):
        return FisheyeModel(cx=1858.0, cy=1669.0, a1=1097.0, roll=0.0349,
                            axis_alt=84.16, axis_az=48.40, rms_residual=10.90,
                            n_matches=606, n_images=60, span_minutes=62.6,
                            final_tol_px=16.5, chance_ratio=1.04)

    def test_a_saved_chance_fit_loads_as_preliminary_with_the_reason(
            self, controller, tmp_path):
        ctrl, seen = controller
        path = tmp_path / "saved.json"
        self._reporter_model().save(str(path))
        ctrl._mw.config.set('allsky_overlay', {'calibration_file': str(path)})
        ctrl._update_status()
        assert seen['quality'][-1] == 'preliminary'
        assert 'chance' in seen['note'][-1]
        # The note is in place before the level, so the badge never shows
        # the level with a stale tooltip.
        assert len(seen['note']) == len(seen['quality'])

    def test_a_credible_model_carries_no_note(self, controller):
        ctrl, seen = controller
        ctrl._on_calibration_done(_guided_model())
        assert seen['quality'][-1] == 'preliminary'
        assert seen['note'][-1] == ''
