"""
The refinement seed is expressed in the buffer's resolution.

A guided or Calibrate Now model is solved on the raw frame (3552 px on the
reference rig) while the calibration buffer holds preview-resolution
detections (2628 px). Seeding the joint fit with the unscaled model put every
prediction ~35 % too far from the centre: on the rig's 2026-10-05 buffer the
guided model scored 24x chance at 6 px once rescaled, yet every refinement
seeded from it walked off to chance level (1.1-1.4x) and none succeeded in a
week of logs. Scoring, admission and the pole fit already rescaled; the seed
was the one consumer that did not.

Helpers come from test_calibration_service_workers.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from PySide6.QtWidgets import QApplication

from services.allsky import calibration_workers as cw
import tests.test_calibration_service_workers as workers_tests
from tests.test_calibration_service_workers import (
    _model, _run_one_refinement, _service)


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def seeds(monkeypatch):
    """Run the real worker with its callees stubbed; record each seed."""
    workers_tests._stub_refine(monkeypatch)
    seen = []

    def fake_refine(frames, seed, **kw):
        seen.append(seed)
        return _model()

    monkeypatch.setattr(cw, 'refine_from_detections', fake_refine)
    return seen


def _raw_resolution_model():
    """The default 1920x1080 test model, solved at twice the buffer's size."""
    m = _model()
    m.cx, m.cy, m.a1, m.a3, m.a5 = 1920.0, 1080.0, 1200.0, -20.0, -40.0
    m.image_width, m.image_height = 3840, 2160
    m.provenance = 'guided'
    return m


def test_raw_resolution_seed_is_scaled_into_the_buffer(qapp, seeds):
    incumbent = _raw_resolution_model()
    svc = _service(model=incumbent)
    _run_one_refinement(svc)
    seed = seeds[-1]
    assert (seed.cx, seed.cy) == pytest.approx((960.0, 540.0))
    assert (seed.a1, seed.a3, seed.a5) == pytest.approx((600.0, -10.0, -20.0))
    assert (seed.image_width, seed.image_height) == (1920, 1080)
    assert seed.provenance == 'guided'


def test_the_incumbent_itself_is_not_rewritten(qapp, seeds):
    incumbent = _raw_resolution_model()
    svc = _service(model=incumbent)
    _run_one_refinement(svc)
    assert seeds[-1] is not incumbent
    assert (incumbent.cx, incumbent.a1, incumbent.image_width) == (1920.0, 1200.0, 3840)


def test_seed_already_at_buffer_resolution_is_passed_through(qapp, seeds):
    incumbent = _model()
    incumbent.image_width, incumbent.image_height = 1920, 1080
    svc = _service(model=incumbent)
    _run_one_refinement(svc)
    assert seeds[-1] is incumbent


def test_legacy_seed_without_a_resolution_is_passed_through(qapp, seeds):
    incumbent = _model()
    assert not incumbent.image_width
    svc = _service(model=incumbent)
    _run_one_refinement(svc)
    assert seeds[-1] is incumbent


def test_cold_start_stays_seedless(qapp, seeds):
    svc = _service(model=_raw_resolution_model())
    worker = cw._RefineWorker(svc._frames, None, 6, 30.0, lat=39.0,
                              incumbent=svc._model)
    worker.run()
    assert seeds[-1] is None
    worker.release()
    worker.deleteLater()
