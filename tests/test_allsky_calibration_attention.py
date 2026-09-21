"""
Tests for services/allsky/calibration_attention.py and its wiring into
CalibrationService — the caution shown beside the quality badge (issue #79:
a green "Good" over a log of back-to-back refinement rejections).
"""
import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QApplication

from services.allsky import calibration_attention as ca
from services.allsky import calibration_service as cs
from services.allsky.fisheye import FisheyeModel


def _model():
    return FisheyeModel(cx=960.0, cy=540.0, a1=320.0, a3=-10.0, a5=0.0,
                        roll=0.1, axis_alt=88.0, axis_az=10.0,
                        rms_residual=6.0, n_matches=200,
                        n_images=20, span_minutes=90.0)


@pytest.fixture
def health(monkeypatch):
    """Pin incumbent_anchor_health's verdict; the real one has its own tests."""
    verdict = {'value': None}
    monkeypatch.setattr(ca, 'incumbent_anchor_health',
                        lambda model, frames: verdict['value'])
    return verdict


class TestCalibrationAttention:

    def test_no_model_never_needs_attention(self, health):
        health['value'] = False
        assert ca.calibration_attention(None, 10, []) == ('', '')

    def test_a_couple_of_rejections_are_not_news(self, health):
        health['value'] = False
        level, _msg = ca.calibration_attention(
            _model(), ca.ATTENTION_MIN_FAILURES - 1, [])
        assert level == ''

    def test_healthy_model_stays_silent_however_many_rejections(self, health):
        """2026-09-05: 26 rejections while the overlay was visibly right."""
        health['value'] = True
        assert ca.calibration_attention(_model(), 26, []) == ('', '')

    def test_model_missing_its_bright_stars_is_flagged_misaligned(self, health):
        health['value'] = False
        level, msg = ca.calibration_attention(_model(), 4, [])
        assert level == ca.LEVEL_MISALIGNED
        assert '4' in msg
        assert 'Guided Calibration' in msg

    def test_unverifiable_model_is_flagged_unconfirmed_not_misaligned(self, health):
        health['value'] = None
        level, msg = ca.calibration_attention(_model(), 3, [])
        assert level == ca.LEVEL_UNCONFIRMED
        assert 'unchanged' in msg

    def test_message_says_when_auto_calibration_has_paused(self, health):
        health['value'] = None
        _level, msg = ca.calibration_attention(_model(), 5, [], escape_paused=True)
        assert 'paused' in msg

    def test_cloud_is_offered_as_an_innocent_explanation(self, health):
        """The anchor check fails under cloud too; the text must not accuse."""
        for verdict in (False, None):
            health['value'] = verdict
            _level, msg = ca.calibration_attention(_model(), 3, [])
            assert 'Cloud' in msg


class TestServiceWiring:

    @pytest.fixture
    def service(self, health):
        # Full QApplication, never QCoreApplication: the first Qt test in an
        # xdist worker decides the instance every later widget test gets.
        app = QApplication.instance() or QApplication([])
        svc = cs.CalibrationService()
        svc._save_model = lambda m, **kw: None   # never touch the real cal file
        svc._model = _model()
        svc._quality = 'good'
        # As if the failing refinements were seeded by this model; failures
        # from a superseded seed deliberately don't count against it.
        svc._refine_gen = svc._model_generation
        seen = []
        svc.attention_changed.connect(lambda level, msg: seen.append(level))
        yield svc, seen
        svc.deleteLater()
        app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        app.processEvents()

    def test_repeated_refine_failures_raise_the_caution(self, service, health):
        svc, seen = service
        health['value'] = False
        for _ in range(ca.ATTENTION_MIN_FAILURES):
            svc._on_refine_failed("bright-anchor gate failed")
        assert seen == [ca.LEVEL_MISALIGNED]

    def test_nothing_is_emitted_below_the_threshold(self, service, health):
        svc, seen = service
        health['value'] = False
        for _ in range(ca.ATTENTION_MIN_FAILURES - 1):
            svc._on_refine_failed("rejected")
        assert seen == []

    def test_failures_from_a_superseded_seed_do_not_count(self, service, health):
        svc, seen = service
        health['value'] = False
        svc._refine_gen = svc._model_generation - 1
        for _ in range(ca.ATTENTION_MIN_FAILURES + 2):
            svc._on_refine_failed("rejected")
        assert seen == []

    def test_new_model_clears_the_caution(self, service, health):
        svc, seen = service
        health['value'] = False
        for _ in range(ca.ATTENTION_MIN_FAILURES):
            svc._on_refine_failed("rejected")
        svc.set_model(_model())
        assert seen[-1] == ''

    def test_reset_clears_the_caution(self, service, health):
        svc, seen = service
        health['value'] = False
        for _ in range(ca.ATTENTION_MIN_FAILURES):
            svc._on_refine_failed("rejected")
        svc.clear_model()
        assert seen[-1] == ''
