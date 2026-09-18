"""
Tests for the Step 6 match-count floor in services/allsky/calibration.py.

Split out from tests/test_allsky_calibration.py to stay under the file-size
cap. Issue #33: a grid fit whose iterative refinement ends below
min_matches must not fall through to the multi-parameter model getting
saved — but it must still get a shot at the triangle-hash fallback, which
re-derives the orientation from scratch rather than reusing the grid
search's guess.
"""
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from services.allsky.fisheye import FisheyeModel


def _thin_fit_image_and_matches():
    """Synthetic frame + five matches: past the grid-search gate (3), short
    of min_matches (8)."""
    rng = np.random.default_rng(7)
    arr = np.zeros((1080, 1920), dtype=np.uint8)
    for _ in range(30):
        x, y = int(rng.integers(60, 1860)), int(rng.integers(60, 1020))
        ys, xs = np.mgrid[y - 8:y + 9, x - 8:x + 9]
        arr[ys, xs] = np.clip(
            arr[ys, xs].astype(float)
            + 220 * np.exp(-((xs - x) ** 2 + (ys - y) ** 2) / (2 * 2.5 ** 2)),
            0, 255).astype(np.uint8)
    from PIL import Image as PILImage
    image = PILImage.fromarray(arr, mode='L')

    seed = FisheyeModel(cx=960.0, cy=540.0, a1=600.0)
    matches = [((960.0 + 40 * i, 540.0 + 30 * i),
                {'name': f'S{i}', 'vmag': 1.0 + i},
                (80.0 - 6.0 * i, 30.0 * i))
               for i in range(5)]
    return image, seed, matches


class TestGridFitMatchFloorFallsThroughToTriangle:
    def test_thin_grid_fit_hands_off_to_triangle_calibrate(self, monkeypatch):
        """The thin grid fit (5 matches < min_matches=8) must reach Step 6
        and fall through to triangle_calibrate rather than raising
        outright — the fallback re-hypothesises the orientation, so a thin
        grid fit is not evidence the frame can't be solved."""
        pytest.importorskip('scipy')
        from datetime import datetime, timezone
        from services.allsky import calibration as cal
        from services.allsky import triangle_match as tm

        image, seed, matches = _thin_fit_image_and_matches()
        monkeypatch.setattr(cal, '_find_best_initial_model',
                             lambda *a, **kw: (seed, list(matches)))
        monkeypatch.setattr(cal, '_brightness_match',
                             lambda *a, **kw: list(matches))

        sentinel = FisheyeModel(cx=1.0, cy=2.0, a1=3.0)
        calls = []

        def fake_triangle_calibrate(*args, **kwargs):
            calls.append((args, kwargs))
            return sentinel

        # calibrate() imports triangle_calibrate lazily inside the function
        # body, so it must be patched on the module, not re-imported here.
        monkeypatch.setattr(tm, 'triangle_calibrate', fake_triangle_calibrate)

        result = cal.calibrate(
            image, lat_deg=51.5, lon_deg=-0.1,
            dt=datetime(2026, 1, 1, 22, 0, tzinfo=timezone.utc),
            min_matches=8, max_residual_px=30.0)

        assert result is sentinel
        assert len(calls) == 1

    def test_raises_with_both_reasons_when_fallback_also_fails(self, monkeypatch):
        """When the triangle-hash fallback also fails, calibrate() raises
        CalibrationError carrying both the grid fit's match-count reason
        and the fallback's own failure."""
        pytest.importorskip('scipy')
        from datetime import datetime, timezone
        from services.allsky import calibration as cal
        from services.allsky import triangle_match as tm

        image, seed, matches = _thin_fit_image_and_matches()
        monkeypatch.setattr(cal, '_find_best_initial_model',
                             lambda *a, **kw: (seed, list(matches)))
        monkeypatch.setattr(cal, '_brightness_match',
                             lambda *a, **kw: list(matches))

        def fake_triangle_calibrate(*args, **kwargs):
            raise cal.CalibrationError("triangle: no dice")

        monkeypatch.setattr(tm, 'triangle_calibrate', fake_triangle_calibrate)

        with pytest.raises(cal.CalibrationError) as exc_info:
            cal.calibrate(
                image, lat_deg=51.5, lon_deg=-0.1,
                dt=datetime(2026, 1, 1, 22, 0, tzinfo=timezone.utc),
                min_matches=8, max_residual_px=30.0)

        message = str(exc_info.value)
        assert "matched only 5" in message
        assert "triangle: no dice" in message
