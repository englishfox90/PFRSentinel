"""
Tests for ui/controllers/guided_calibration_session.py and the controller's
commit hook (issue #79): a passing solve is HELD until the user accepts it,
and a failing one comes back as per-star rows, not a closed window.
"""
import time
from datetime import datetime, timezone

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QApplication

from services.allsky import guided_calibration as gc
from services.allsky import guided_hints as gh
from services.allsky.calibration import CalibrationError
from services.allsky.fisheye import FisheyeModel
from ui.controllers import guided_calibration_session as gs

DEADLINE_S = 10.0


@pytest.fixture(scope="module")
def qapp():
    # A full QApplication even though nothing here is a widget: whichever Qt
    # test runs first in an xdist worker creates the process-wide instance,
    # and a bare QCoreApplication would crash every widget test after it.
    app = QApplication.instance() or QApplication([])
    yield app


def _pump_until(qapp, predicate, deadline=DEADLINE_S):
    start = time.monotonic()
    while time.monotonic() - start < deadline:
        qapp.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    return False


def _prep():
    return {
        'lat': 31.0, 'lon': -100.0,
        'dt': datetime(2026, 6, 22, 6, 0, tzinfo=timezone.utc),
        'sky_cx': 500.0, 'sky_cy': 500.0, 'sky_r': 450.0,
        'image_width': 1000, 'image_height': 1000,
        'detections': [],
        'candidates': [
            {'name': 'Vega', 'alt': 80.0, 'az': 10.0, 'vmag': 0.0},
            {'name': 'Deneb', 'alt': 60.0, 'az': 50.0, 'vmag': 1.2},
            {'name': 'Below', 'alt': -5.0, 'az': 90.0, 'vmag': 1.0},
        ],
    }


def _anchors(n=5):
    return [(100.0 * i, 50.0 * i, 10.0 * i, 5.0 * i, f"star{i}") for i in range(n)]


def _solved_model(anchors, note=None):
    model = FisheyeModel(cx=500.0, cy=500.0, a1=290.0, a3=-10.0, a5=0.0,
                         roll=0.0, axis_alt=90.0, axis_az=0.0,
                         rms_residual=3.2, n_matches=len(anchors),
                         image_width=1000, image_height=1000)
    model.provenance = 'guided'
    model.guided_residuals = [(a[4], a[4], 2.0 + i) for i, a in enumerate(anchors)]
    model.guided_rms_limit = 12.0
    if note:
        model.guided_note = note
    return model


def _flush(qapp):
    qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    qapp.processEvents()


@pytest.fixture
def session(qapp):
    s = gs.GuidedCalibrationSession(_prep())
    events = {'solving': 0, 'solved': [], 'failed': [], 'hints': []}
    s.solving.connect(lambda: events.__setitem__('solving', events['solving'] + 1))
    s.solved.connect(events['solved'].append)
    s.failed.connect(events['failed'].append)
    s.hints_ready.connect(events['hints'].append)
    yield s, events
    s.close()
    _flush(qapp)


class TestSolve:

    def test_passing_solve_is_held_not_saved(self, session, qapp, monkeypatch):
        s, events = session
        monkeypatch.setattr(gc, 'calibrate_from_anchors',
                            lambda a, *args, **kw: _solved_model(a))
        s.solve(_anchors())
        assert _pump_until(qapp, lambda: events['solved'])

        assert events['solving'] == 1
        assert s.pending_model is not None
        result = events['solved'][0]
        assert result['n_used'] == 5 and result['n_anchors'] == 5
        assert result['rms'] == pytest.approx(3.2)
        assert [r['state'] for r in result['anchors']] == ['ok'] * 5

    def test_review_overlay_predicts_only_stars_in_the_frame(
            self, session, qapp, monkeypatch):
        s, events = session
        monkeypatch.setattr(gc, 'calibrate_from_anchors',
                            lambda a, *args, **kw: _solved_model(a))
        s.solve(_anchors())
        assert _pump_until(qapp, lambda: events['solved'])
        names = [p['name'] for p in events['solved'][0]['predicted']]
        assert 'Vega' in names and 'Deneb' in names
        assert 'Below' not in names

    def test_rescued_anchors_are_reported_as_renamed_or_excluded(
            self, session, qapp, monkeypatch):
        s, events = session

        def solve(anchors, *args, **kw):
            model = _solved_model(anchors, note="Corrected identification")
            rows = list(model.guided_residuals)
            rows[1] = (rows[1][0], 'Sirius', 4.0)
            rows[3] = (rows[3][0], rows[3][1], None)
            model.guided_residuals = rows
            return model

        monkeypatch.setattr(gc, 'calibrate_from_anchors', solve)
        s.solve(_anchors())
        assert _pump_until(qapp, lambda: events['solved'])
        result = events['solved'][0]
        states = [r['state'] for r in result['anchors']]
        assert states == ['ok', 'renamed', 'ok', 'excluded', 'ok']
        assert result['anchors'][1]['used_as'] == 'Sirius'
        assert result['note'] == "Corrected identification"

    def test_failure_names_the_star_that_did_not_fit(
            self, session, qapp, monkeypatch):
        s, events = session

        def solve(anchors, *args, **kw):
            err = CalibrationError("Guided calibration RMS 55.0px exceeds limit 12px")
            err.anchor_residuals = [('star2', 140.0), ('star0', 9.0),
                                    ('star1', 7.0), ('star3', 6.0), ('star4', 4.0)]
            err.rms_limit = 12.0
            raise err

        monkeypatch.setattr(gc, 'calibrate_from_anchors', solve)
        s.solve(_anchors())
        assert _pump_until(qapp, lambda: events['failed'])

        result = events['failed'][0]
        assert s.pending_model is None
        assert 'star2' in result['message']
        assert 'kept' in result['message']
        states = {r['name']: r['state'] for r in result['anchors']}
        assert states['star2'] == 'suspect'
        assert all(v == 'ok' for k, v in states.items() if k != 'star2')

    def test_wrong_basin_failure_does_not_paint_every_star_red(
            self, session, qapp, monkeypatch):
        """All residuals large and alike: only the worst is singled out."""
        s, events = session

        def solve(anchors, *args, **kw):
            err = CalibrationError("RMS exceeds limit")
            err.anchor_residuals = [('star0', 210.0), ('star1', 200.0),
                                    ('star2', 190.0), ('star3', 185.0),
                                    ('star4', 180.0)]
            err.rms_limit = 12.0
            raise err

        monkeypatch.setattr(gc, 'calibrate_from_anchors', solve)
        s.solve(_anchors())
        assert _pump_until(qapp, lambda: events['failed'])
        states = [r['state'] for r in events['failed'][0]['anchors']]
        assert states.count('suspect') == 1

    def test_failure_without_residuals_still_reports(
            self, session, qapp, monkeypatch):
        s, events = session

        def solve(*args, **kw):
            raise CalibrationError(
                "Guided calibration converged to an implausible model (a3). More.")

        monkeypatch.setattr(gc, 'calibrate_from_anchors', solve)
        s.solve(_anchors())
        assert _pump_until(qapp, lambda: events['failed'])
        result = events['failed'][0]
        assert result['anchors'] == []
        assert 'kept' in result['message']

    def test_discard_drops_the_held_model(self, session, qapp, monkeypatch):
        s, events = session
        monkeypatch.setattr(gc, 'calibrate_from_anchors',
                            lambda a, *args, **kw: _solved_model(a))
        s.solve(_anchors())
        assert _pump_until(qapp, lambda: events['solved'])
        s.discard()
        assert s.pending_model is None

    def test_second_solve_while_one_runs_is_ignored(
            self, session, qapp, monkeypatch):
        s, events = session

        def slow(anchors, *args, **kw):
            time.sleep(0.2)
            return _solved_model(anchors)

        monkeypatch.setattr(gc, 'calibrate_from_anchors', slow)
        s.solve(_anchors())
        s.solve(_anchors())
        assert _pump_until(qapp, lambda: events['solved'])
        _pump_until(qapp, lambda: False, deadline=0.4)
        assert events['solving'] == 1 and len(events['solved']) == 1


class TestHints:

    def test_too_few_anchors_clears_hints_without_a_worker(self, session):
        s, events = session
        s.request_hints(_anchors(2))
        assert events['hints'] == [None]

    def test_only_the_latest_request_is_delivered(self, session, qapp, monkeypatch):
        s, events = session

        def suggest(anchors, *args, **kw):
            # The older request finishes LAST; it must still be dropped.
            time.sleep(0.3 if len(anchors) == 3 else 0.0)
            return gh.HintResult(trusted=True, message=str(len(anchors)))

        monkeypatch.setattr(gh, 'suggest_stars', suggest)
        s.request_hints(_anchors(3))
        s.request_hints(_anchors(4))
        assert _pump_until(qapp, lambda: events['hints'])
        _pump_until(qapp, lambda: False, deadline=0.6)
        assert [h.message for h in events['hints']] == ['4']


class TestSave:

    def _held(self, qapp, monkeypatch, commit):
        s = gs.GuidedCalibrationSession(_prep(), commit=commit)
        outcomes = []
        s.saved.connect(lambda ok, msg: outcomes.append((ok, msg)))
        s._pending_model = _solved_model(_anchors())
        return s, outcomes

    def test_save_hands_the_held_model_to_the_commit_hook_once(self, qapp, monkeypatch):
        committed = []
        s, outcomes = self._held(qapp, monkeypatch,
                                 lambda m: (committed.append(m), (True, ""))[1])
        held = s.pending_model
        try:
            s.save()
            assert committed == [held]
            assert outcomes == [(True, "")]
            assert s.pending_model is None
            s.save()                                  # nothing left to save
            assert committed == [held]
            assert outcomes[-1][0] is False
        finally:
            s.close()
            _flush(qapp)

    def test_refused_save_keeps_the_model_for_a_retry(self, qapp, monkeypatch):
        s, outcomes = self._held(qapp, monkeypatch, lambda m: (False, "disk full"))
        try:
            s.save()
            assert outcomes == [(False, "disk full")]
            assert s.pending_model is not None
        finally:
            s.close()
            _flush(qapp)

    def test_commit_that_raises_still_answers_the_dialog(self, qapp, monkeypatch):
        """The dialog won't close while it waits; silence would strand it."""
        def boom(model):
            raise OSError("share went away")

        s, outcomes = self._held(qapp, monkeypatch, boom)
        try:
            s.save()
            assert len(outcomes) == 1 and outcomes[0][0] is False
            assert "share went away" in outcomes[0][1]
        finally:
            s.close()
            _flush(qapp)


def test_result_that_cannot_be_shown_becomes_a_failure_not_a_hang(
        session, qapp, monkeypatch):
    s, events = session
    monkeypatch.setattr(gc, 'calibrate_from_anchors',
                        lambda a, *args, **kw: _solved_model(a))
    monkeypatch.setattr(gs.GuidedCalibrationSession, '_predict',
                        lambda self, model: 1 / 0)
    s.solve(_anchors())
    assert _pump_until(qapp, lambda: events['failed'])
    assert events['solved'] == []
    assert s.pending_model is None


class TestControllerCommit:

    @pytest.fixture
    def controller(self, qapp, tmp_path, monkeypatch):
        import services.app_config as app_config
        from ui.controllers.allsky_controller import AllSkyController

        self.cal = tmp_path / "allsky_calibration.json"
        monkeypatch.setattr(app_config, 'get_calibration_path', lambda: str(self.cal))

        class Config(dict):
            def set(self, k, v):
                self[k] = v

            def save(self):
                pass

        class Notifier:
            def notify(self, event):
                pass

        class MainWindow:
            config = Config()
            notifier = Notifier()

        ctrl = AllSkyController(MainWindow())
        yield ctrl
        ctrl.shutdown()
        ctrl.deleteLater()
        _flush(qapp)

    def _guided(self):
        model = _solved_model(_anchors())
        model.calibrated_at = '2026-09-20T06:00:00+00:00'
        return model

    def test_commit_writes_the_file_and_reports_success(self, controller):
        ok, _msg = controller.commit_guided_calibration(self._guided())
        assert ok and self.cal.exists()

    def test_commit_reports_failure_when_the_file_cannot_be_written(
            self, controller, monkeypatch):
        """It used to log the error and announce 'saved' regardless."""
        def fail(self, path):
            raise OSError("disk full")

        monkeypatch.setattr(FisheyeModel, 'save', fail)
        statuses = []
        controller.status_changed.connect(statuses.append)
        before = controller._model
        ok, msg = controller.commit_guided_calibration(self._guided())
        assert not ok and 'kept' in msg
        assert controller._model is before, "an unsaved model must not go live"
        assert any('save failed' in s.lower() for s in statuses)

    def test_commit_waits_for_a_calibrate_now_run(self, controller):
        class Running:
            def isRunning(self):
                return True

            def quit(self):
                pass

            def wait(self, ms):
                return True

        controller._worker = Running()
        ok, msg = controller.commit_guided_calibration(self._guided())
        assert not ok and 'Calibrate Now' in msg
        assert not self.cal.exists()
        controller._worker = None


def test_closed_session_ignores_late_results(qapp, monkeypatch):
    s = gs.GuidedCalibrationSession(_prep())
    got = []
    s.solved.connect(got.append)

    def slow(anchors, *args, **kw):
        time.sleep(0.15)
        return _solved_model(anchors)

    monkeypatch.setattr(gc, 'calibrate_from_anchors', slow)
    s.solve(_anchors())
    s.close()                      # waits the worker out
    _pump_until(qapp, lambda: False, deadline=0.3)
    assert got == []
    assert s.pending_model is None
    _flush(qapp)
