"""
#93 — the model on disk is scored against chance on the live buffer.

Wiring tests for incumbent_chance: _RefineWorker emits the score on its own
signal before the run's result (most runs on a rig that needs this end in
`failed`), and CalibrationService turns two consecutive chance-level scores
into a Preliminary badge, a 'misaligned' caution and a status line that says
why — without rewriting the calibration file. One credible score clears it;
an unscored (cloudy) run is neither a strike nor a clearance.

Helpers and the `fast_refine` fixture come from test_calibration_service_workers.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QApplication

from services.allsky import calibration_service as cs
from services.allsky import calibration_workers as cw
import tests.test_calibration_service_workers as workers_tests
from tests.test_calibration_service_workers import (
    _model, _pump, _run_one_refinement, _service)


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def fast_refine(monkeypatch):
    return workers_tests._stub_refine(monkeypatch)

def _chance_score(ratio):
    from services.allsky.incumbent_chance import IncumbentScore
    return IncumbentScore(n_matches=int(620 * ratio), expected=620.0,
                          ratio=ratio, rms=11.0, tol_px=15.5, n_frames=60)


@pytest.fixture
def scored(monkeypatch):
    """Pin the score _RefineWorker hands the service; the real
    score_incumbent has its own tests. Also records the arguments."""
    state = {'score': None, 'calls': []}

    def fake_score(model, frames, tol_px):
        state['calls'].append((model, frames, tol_px))
        return state['score']

    monkeypatch.setattr(cw, 'score_incumbent', fake_score)
    return state


class TestWorkerScoresTheIncumbent:

    def test_score_is_emitted_on_its_own_signal_before_the_result(
            self, qapp, fast_refine, scored):
        """The score must reach the service on runs that end in `failed`
        too (#93: 62 of 62), so it rides its own signal, emitted first."""
        svc = _service()
        scored['score'] = _chance_score(1.0)
        order = []
        # Captured through the service's slots, which are connected before
        # the QThread starts: with every callee stubbed, run() is over in
        # microseconds and a connect made after _maybe_refine() can miss it.
        svc._on_incumbent_scored = lambda s: order.append(('score', s))
        svc._on_refine_done = lambda *a: order.append(('result',))
        _run_one_refinement(svc)
        assert [o[0] for o in order] == ['score', 'result']
        assert order[0][1].ratio == 1.0

    def test_scored_on_the_same_frames_at_the_final_tolerance(
            self, qapp, fast_refine, scored):
        from services.allsky.incumbent_chance import score_tolerance_px
        svc = _service()
        _run_one_refinement(svc)
        model, frames, tol = scored['calls'][-1]
        assert model is svc._model
        assert frames is fast_refine[-1]
        assert tol == pytest.approx(score_tolerance_px(500.0))

    def test_score_still_arrives_when_the_refinement_fails(
            self, qapp, fast_refine, scored, monkeypatch):
        def fail(*a, **kw):
            raise cw.CalibrationError("at chance level")
        monkeypatch.setattr(cw, 'refine_from_detections', fail)
        scored['score'] = _chance_score(1.1)
        svc = _service()
        seen = []
        svc._on_incumbent_scored = seen.append
        _run_one_refinement(svc)
        assert seen and seen[0].ratio == 1.1


class TestServiceChanceStreak:

    @pytest.fixture
    def service(self, qapp, monkeypatch):
        monkeypatch.setattr(cs, 'incumbent_anchor_health', lambda m, f: None)
        svc = _service(model=_model(rms=10.9, n_matches=606, n_images=60,
                                    span_minutes=62.6))
        svc._quality = 'good'
        svc._refine_gen = svc._model_generation
        seen = {'quality': [], 'note': [], 'attention': [], 'status': [],
                'saved': []}
        svc.badge_quality_changed.connect(
            lambda q, note: (seen['quality'].append(q), seen['note'].append(note)))
        # quality_upgraded means "a model was saved" to the controller (it
        # re-points the config's calibration_file); a live verdict must
        # never travel on it.
        svc.quality_upgraded.connect(lambda q, m: seen['saved'].append(q))
        svc.attention_changed.connect(lambda lvl, msg: seen['attention'].append(lvl))
        svc.status_changed.connect(seen['status'].append)
        yield svc, seen
        svc.deleteLater()
        qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        qapp.processEvents()

    def test_two_chance_level_runs_drop_the_badge_and_raise_misaligned(self, service):
        """Even with anchor health None — the verdict that kept #93 silent."""
        svc, seen = service
        svc._on_incumbent_scored(_chance_score(1.04))
        assert seen['quality'] == [] and seen['attention'] == []
        svc._on_incumbent_scored(_chance_score(0.97))
        assert seen['quality'] == ['preliminary']
        assert seen['attention'] == ['misaligned']
        assert svc.current_quality == 'good'          # the file's rating
        assert seen['saved'] == []
        note = seen['note'][-1]
        assert note.startswith("Matched 601 stars vs 620 expected by chance "
                               "at 15.5 px in 2 consecutive runs")
        assert note.endswith("(rating from when it was saved: good)")

    def test_the_restored_status_says_why(self, service):
        svc, seen = service
        for _ in range(2):
            svc._on_incumbent_scored(_chance_score(1.0))
        svc._on_refine_failed("at chance level")
        assert seen['status'][-1] == (
            "Calibrated: 606 stars, RMS=10.9px "
            "(preliminary — matches at chance level)")
        svc._on_incumbent_scored(_chance_score(3.0))
        svc._on_refine_failed("rejected")
        assert seen['status'][-1] == "Calibrated: 606 stars, RMS=10.9px (good)"

    def test_one_credible_run_clears_it(self, service):
        svc, seen = service
        for _ in range(2):
            svc._on_incumbent_scored(_chance_score(1.0))
        svc._on_incumbent_scored(_chance_score(2.5))
        assert seen['quality'] == ['preliminary', 'good']
        assert seen['note'] == [seen['note'][0], '']
        assert seen['attention'] == ['misaligned', '']
        assert seen['saved'] == []

    def test_an_unscored_run_is_not_a_strike(self, service):
        svc, seen = service
        svc._on_incumbent_scored(_chance_score(1.0))
        svc._on_incumbent_scored(None)
        svc._on_incumbent_scored(None)
        assert seen['quality'] == []
        svc._on_incumbent_scored(_chance_score(1.0))
        assert seen['quality'] == ['preliminary']

    def test_the_file_is_never_rewritten(self, service):
        svc, seen = service
        saved = []
        svc._save_model = lambda m, **kw: saved.append(m)
        for _ in range(2):
            svc._on_incumbent_scored(_chance_score(1.0))
        assert saved == []

    def test_a_score_from_a_superseded_seed_is_ignored(self, service):
        svc, seen = service
        svc._model_generation += 1
        for _ in range(3):
            svc._on_incumbent_scored(_chance_score(1.0))
        assert seen['quality'] == []

    def test_a_new_model_starts_with_a_clean_slate(self, service):
        svc, seen = service
        for _ in range(2):
            svc._on_incumbent_scored(_chance_score(1.0))
        svc.set_model(_model())
        assert not svc._chance_streak.discredited
        assert seen['attention'][-1] == ''

    def test_a_loaded_model_starts_with_a_clean_slate(self, service, tmp_path):
        svc, _seen = service
        for _ in range(2):
            svc._on_incumbent_scored(_chance_score(1.0))
        path = tmp_path / "allsky_calibration.json"
        _model().save(str(path))
        svc.load_model(str(path))
        assert not svc._chance_streak.discredited

    def test_the_verdict_reaches_the_replacement_decision(
            self, service, monkeypatch):
        calls = []

        def fake_should_replace(*a, **kw):
            calls.append(kw)
            return False, 'recorded'

        monkeypatch.setattr(cs, 'should_replace', fake_should_replace)
        svc, _seen = service
        for _ in range(2):
            svc._on_incumbent_scored(_chance_score(1.0))
        svc._on_refine_done(_model(), 30, 60.0)
        assert calls[-1]['incumbent_chance_level_twice'] is True

    def test_an_admitted_replacement_resets_the_streak(self, service):
        svc, seen = service
        for _ in range(2):
            svc._on_incumbent_scored(_chance_score(1.0))
        svc._on_refine_done(_model(rms=3.0, n_matches=2000), 30, 60.0)
        assert svc._model.rms_residual == 3.0
        assert not svc._chance_streak.discredited
        assert seen['attention'][-1] == ''
