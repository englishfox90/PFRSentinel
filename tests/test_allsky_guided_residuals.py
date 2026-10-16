"""
Per-anchor residuals from the guided solver (issue #79).

The dialog marks the stars that didn't fit in place, so the solver has to say
which ones — as data, not only as a sentence in an error message.
"""
from datetime import datetime, timezone

import pytest

pytest.importorskip('scipy')

from services.allsky.calibration import CalibrationError
from services.allsky.guided_calibration import calibrate_from_anchors
import tests.test_allsky_calibration as solver_tests   # module, not the class: importing
# the class by name would make pytest collect its tests a second time here.

LAT, LON = 31.33, -100.46
DT = datetime(2026, 6, 22, 6, 0, tzinfo=timezone.utc)
SKY = dict(sky_cx=1137.0, sky_cy=1306.0, sky_radius=968.0)


def _named_anchors(n):
    helper = solver_tests.TestGuidedCalibration()
    anchors = helper._anchors(helper._true_model(), LAT, LON, DT, n=n)
    return [(*a, f"star{i}") for i, a in enumerate(anchors)]


def test_clean_solve_reports_a_small_residual_for_every_anchor():
    anchors = _named_anchors(6)
    model = calibrate_from_anchors(anchors, LAT, LON, DT, **SKY)

    rows = model.guided_residuals
    assert [given for given, _used, _d in rows] == [a[4] for a in anchors]
    assert all(given == used for given, used, _d in rows)
    assert all(d is not None and d < 5.0 for _g, _u, d in rows)
    assert model.guided_rms_limit > 0
    # Persisted twin of the session-only limit; chance is not applicable.
    assert model.final_tol_px == model.guided_rms_limit
    assert model.chance_ratio == 0.0


def test_excluded_anchor_is_reported_without_a_residual():
    anchors = _named_anchors(7)
    x, y, ra, dec, name = anchors[2]
    # A mis-click far from any bright star: the rescue drops it, not renames it.
    anchors[2] = (x + 260.0, y - 240.0, ra, dec, name)
    model = calibrate_from_anchors(anchors, LAT, LON, DT, **SKY)

    by_name = {given: d for given, _used, d in model.guided_residuals}
    assert len(by_name) == len(anchors), "every input anchor is accounted for"
    if model.n_matches < len(anchors):
        assert by_name[name] is None
    assert all(d is not None for g, d in by_name.items() if g != name)


def test_failed_solve_carries_residuals_worst_first():
    anchors = _named_anchors(5)
    # Two wild clicks among five: past what the rescue can recover.
    for i, (dx, dy) in ((0, (300.0, 250.0)), (3, (-280.0, 310.0))):
        x, y, ra, dec, name = anchors[i]
        anchors[i] = (x + dx, y + dy, ra, dec, name)

    with pytest.raises(CalibrationError) as excinfo:
        calibrate_from_anchors(anchors, LAT, LON, DT, **SKY)

    residuals = excinfo.value.anchor_residuals
    assert {n for n, _d in residuals} == {a[4] for a in anchors}
    distances = [d for _n, d in residuals]
    assert distances == sorted(distances, reverse=True)
    assert excinfo.value.rms_limit > 0
