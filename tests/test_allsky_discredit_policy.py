"""
What a discredited automatic model costs — services/allsky/discredit_policy.py
and its wiring into CalibrationService, the preview renderer and the
controller's notification.

2026-09-28: a single-image fit was discredited by two chance-level scores at
04:14, the badge went preliminary, and the wrong labels stayed on the output
for 17 hours while refinement kept seeding from it and nobody was told.

Helpers and the `fast_refine` fixture come from test_calibration_service_workers.
"""
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

pytest.importorskip("PySide6")

from PIL import Image
from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QApplication

from services.allsky import calibration_service as cs
from services.allsky import discredit_policy as dp
from services.allsky import overlay_renderer
from services.allsky.escape_policy import ESCAPE_EXHAUSTION_THRESHOLD
from services.allsky.incumbent_chance import IncumbentScore
import tests.test_calibration_service_workers as workers_tests
from tests.test_calibration_service_workers import (
    _frames, _model, _pump, _service)


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture(autouse=True)
def _released():
    """The withhold flag is process-wide; every test starts and ends clear."""
    dp.release_overlay()
    yield
    dp.release_overlay()


@pytest.fixture
def fast_refine(monkeypatch):
    return workers_tests._stub_refine(monkeypatch)


def _score(ratio):
    return IncumbentScore(n_matches=int(620 * ratio), expected=620.0,
                          ratio=ratio, rms=11.0, tol_px=15.5, n_frames=60)


def _guided():
    m = _model(rms=4.1, n_matches=7, n_images=1, span_minutes=0.0)
    m.provenance = 'guided'
    return m


# ---------------------------------------------------------------------------
# The episode itself
# ---------------------------------------------------------------------------

class TestDiscreditEpisode:

    def test_begins_once_and_withholds_the_overlay(self):
        ep = dp.DiscreditEpisode()
        assert ep.update(True, _model()) is True
        assert ep.active and dp.overlay_withheld()
        assert 'chance' in dp.withhold_reason()
        assert ep.update(True, _model()) is None      # still the same episode

    def test_ends_once_and_releases_the_overlay(self):
        ep = dp.DiscreditEpisode()
        ep.update(True, _model())
        assert ep.update(False, _model()) is False
        assert not ep.active and not dp.overlay_withheld()
        assert ep.update(False, _model()) is None
        assert ep.end() is None

    def test_a_new_episode_begins_after_a_clearance(self):
        ep = dp.DiscreditEpisode()
        assert ep.update(True, _model()) is True
        assert ep.update(False, _model()) is False
        assert ep.update(True, _model()) is True

    def test_the_guided_solve_never_has_an_episode(self):
        ep = dp.DiscreditEpisode()
        assert ep.update(True, _guided()) is None
        assert not ep.active and not dp.overlay_withheld()

    def test_no_model_ends_an_open_episode(self):
        ep = dp.DiscreditEpisode()
        ep.update(True, _model())
        assert ep.update(True, None) is False
        assert not dp.overlay_withheld()

    def test_notification_text_names_the_model_and_the_remedy(self):
        title, body = dp.notification_text(
            _model(rms=5.8, n_matches=10), "Matched 10 stars vs 9 expected")
        assert 'Attention' in title
        assert '10 stars' in body and '5.8 px' in body
        assert 'Matched 10 stars' in body
        assert 'not drawn' in body and 'unchanged' in body
        assert 'Guided Calibration' in body


# ---------------------------------------------------------------------------
# The renderer treats a withheld model as no model
# ---------------------------------------------------------------------------

class TestPreviewRendererGate:

    def _render(self, monkeypatch):
        from services import observing_window
        calls = []
        monkeypatch.setattr(observing_window, 'is_observing_window',
                            lambda *a, **k: calls.append(1) or False)
        img = Image.new('RGB', (64, 64))
        out = overlay_renderer.render_allsky_for_preview(
            img, {'enabled': True, 'calibration_file': 'x.json'}, {}, {})
        return img, out, calls

    def test_withheld_model_returns_the_frame_untouched(self, monkeypatch):
        dp.withhold_overlay("chance")
        img, out, calls = self._render(monkeypatch)
        assert out is img
        assert calls == [], "withheld before the observing window is asked"

    def test_released_model_renders_again(self, monkeypatch):
        dp.withhold_overlay("chance")
        dp.release_overlay()
        _img, _out, calls = self._render(monkeypatch)
        assert calls == [1]


# ---------------------------------------------------------------------------
# Service wiring
# ---------------------------------------------------------------------------

class TestServiceEpisode:

    @pytest.fixture
    def service(self, qapp, monkeypatch):
        monkeypatch.setattr(cs, 'incumbent_anchor_health', lambda m, f: None)
        svc = _service(model=_model(rms=5.8, n_matches=10, n_images=1,
                                    span_minutes=0.0))
        svc._quality = 'preliminary'
        svc._refine_gen = svc._model_generation
        notified = []
        svc.model_discredited.connect(lambda t, b: notified.append((t, b)))
        yield svc, notified
        svc.deleteLater()
        qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        qapp.processEvents()

    def _discredit(self, svc):
        svc._on_incumbent_scored(_score(1.15))
        svc._on_incumbent_scored(_score(1.13))

    def test_two_chance_level_runs_withhold_the_overlay(self, service):
        svc, _ = service
        svc._on_incumbent_scored(_score(1.15))
        assert not svc.overlay_withheld and not dp.overlay_withheld()
        svc._on_incumbent_scored(_score(1.13))
        assert svc.overlay_withheld and dp.overlay_withheld()

    def test_the_guided_solve_is_never_withheld(self, qapp, monkeypatch):
        monkeypatch.setattr(cs, 'incumbent_anchor_health', lambda m, f: None)
        svc = _service(model=_guided())
        svc._refine_gen = svc._model_generation
        for _ in range(3):
            svc._on_incumbent_scored(_score(1.0))
        assert not svc.overlay_withheld and not dp.overlay_withheld()
        svc.deleteLater()

    def test_notified_once_per_episode(self, service):
        svc, notified = service
        for _ in range(4):
            svc._on_incumbent_scored(_score(1.0))
        assert len(notified) == 1
        title, body = notified[0]
        assert 'Attention' in title
        assert '10 stars' in body and 'Guided Calibration' in body
        assert 'Matched 620 stars vs 620 expected' in body

    def test_a_credible_run_releases_and_a_relapse_notifies_again(self, service):
        svc, notified = service
        self._discredit(svc)
        svc._on_incumbent_scored(_score(2.5))
        assert not svc.overlay_withheld and not dp.overlay_withheld()
        self._discredit(svc)
        assert svc.overlay_withheld
        assert len(notified) == 2

    def test_an_admitted_replacement_releases(self, service):
        svc, notified = service
        self._discredit(svc)
        svc._on_refine_done(_model(rms=3.0, n_matches=2000), 30, 60.0)
        assert svc._model.rms_residual == 3.0
        assert not svc.overlay_withheld and not dp.overlay_withheld()
        assert len(notified) == 1

    def test_set_model_releases(self, service):
        svc, _ = service
        self._discredit(svc)
        svc.set_model(_model())
        assert not svc.overlay_withheld and not dp.overlay_withheld()

    def test_clear_model_releases(self, service):
        svc, _ = service
        self._discredit(svc)
        svc.clear_model()
        assert not svc.overlay_withheld and not dp.overlay_withheld()

    def test_load_model_releases(self, service, tmp_path):
        svc, _ = service
        self._discredit(svc)
        path = tmp_path / "allsky_calibration.json"
        _model().save(str(path))
        svc.load_model(str(path))
        assert not svc.overlay_withheld and not dp.overlay_withheld()

    def test_the_file_is_never_rewritten(self, service):
        svc, _ = service
        saved = []
        svc._save_model = lambda m, **kw: saved.append(m)
        self._discredit(svc)
        assert saved == []


class TestDiscreditedModelEscapesSeedless:
    """No waiting for three rejections, no anchor-health veto: the chance
    verdict is the direct measurement. Seeded refinement resumes while the
    escape back-off holds, so the incumbent keeps being scored."""

    def _discredited_service(self, monkeypatch, health=None):
        monkeypatch.setattr(cs, 'incumbent_anchor_health', lambda m, f: health)
        svc = _service(frames=_frames(n=16, span_minutes=40.0))
        svc._refine_gen = svc._model_generation
        svc._on_incumbent_scored(_score(1.0))
        svc._on_incumbent_scored(_score(1.0))
        assert svc.overlay_withheld
        svc._last_refine_time = -svc._refine_cooldown()
        svc._last_escape_time = -cs.ESCAPE_COOLDOWN_S
        return svc

    def test_escapes_with_no_refinement_failures(self, qapp, fast_refine, monkeypatch):
        svc = self._discredited_service(monkeypatch)
        assert svc._consecutive_refine_failures == 0
        svc._maybe_refine()
        worker = svc._refine_worker
        assert worker is not None and worker._seed is None
        assert svc._escape_attempt is True
        _pump(svc, worker)
        svc.deleteLater()

    def test_a_healthy_anchor_reading_does_not_veto(self, qapp, fast_refine, monkeypatch):
        svc = self._discredited_service(monkeypatch, health=True)
        svc._maybe_refine()
        worker = svc._refine_worker
        assert worker._seed is None and svc._escape_attempt is True
        _pump(svc, worker)
        svc.deleteLater()

    def test_falls_back_to_seeded_refinement_while_escapes_are_exhausted(
            self, qapp, fast_refine, monkeypatch):
        svc = self._discredited_service(monkeypatch)
        for _ in range(ESCAPE_EXHAUSTION_THRESHOLD):
            svc._escape_backoff.record_fruitless(time.monotonic())
        svc._maybe_refine()
        worker = svc._refine_worker
        assert worker._seed is svc._model and svc._escape_attempt is False
        _pump(svc, worker)
        svc.deleteLater()

    def test_an_undiscredited_model_still_needs_three_failures(
            self, qapp, fast_refine, monkeypatch):
        monkeypatch.setattr(cs, 'incumbent_anchor_health', lambda m, f: None)
        svc = _service(frames=_frames(n=16, span_minutes=40.0))
        svc._last_refine_time = -svc._refine_cooldown()
        svc._last_escape_time = -cs.ESCAPE_COOLDOWN_S
        svc._maybe_refine()
        worker = svc._refine_worker
        assert worker._seed is svc._model
        _pump(svc, worker)
        svc.deleteLater()


# ---------------------------------------------------------------------------
# Controller: the signal becomes a notification event
# ---------------------------------------------------------------------------

class _Notifier:
    def __init__(self):
        self.events = []

    def notify(self, event):
        self.events.append(event)


class _Config:
    def get(self, key, default=None):
        return default

    def set(self, key, value):
        pass

    def save(self):
        pass


class _MainWindow:
    def __init__(self):
        self.config = _Config()
        self.notifier = _Notifier()


class TestControllerNotification:

    @pytest.fixture
    def controller(self, qapp, tmp_path, monkeypatch):
        import services.app_config as app_config
        monkeypatch.setattr(app_config, 'get_calibration_path',
                            lambda: str(tmp_path / "allsky_calibration.json"))
        from ui.controllers.allsky_controller import AllSkyController
        ctrl = AllSkyController(_MainWindow())
        yield ctrl
        ctrl.shutdown()
        ctrl.deleteLater()
        qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        qapp.processEvents()

    def test_discredit_signal_becomes_a_warning_event(self, controller):
        from services.notifications import CALIBRATION_DISCREDITED
        controller._model = _model(rms=5.8, n_matches=10)
        controller._cal_service.model_discredited.emit("Needs Attention", "why")
        events = controller._mw.notifier.events
        assert len(events) == 1
        ev = events[0]
        assert ev.type == CALIBRATION_DISCREDITED
        assert ev.level == 'warning'
        assert ev.title == "Needs Attention" and ev.body == "why"
        assert ev.data['model_info']['n_matches'] == 10
