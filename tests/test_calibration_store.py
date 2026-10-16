"""
calibration_store — automatic saves back up one step; a guided solve is also
kept in its own file that no automatic save touches.

Discussion #105, 2026-09-29 on the reporter's rig: a guided solve (8 anchors,
RMS 1.39 px) was replaced by automatic refinements saved at 22:27 and 22:57,
and the second save's backup overwrote the first's — the guided model was on
disk nowhere.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import services.app_config as app_config
from services.allsky.calibration_store import keep_guided_copy, save_with_backup
from services.allsky.fisheye import FisheyeModel


def _guided_solve():
    return FisheyeModel(
        cx=1776.0, cy=1776.0, a1=1268.5, axis_alt=88.5, axis_az=287.7,
        east_left=True, rms_residual=1.39, n_matches=8, n_images=1,
        span_minutes=0.0, image_width=3552, image_height=3552,
        provenance='guided')


def _joint_fit(cx, rms, n_matches):
    """An automatic refinement admitted against the guided incumbent: it
    inherits the guided stamp but records a chance ratio and many frames."""
    return FisheyeModel(
        cx=cx, cy=1654.2, a1=1282.1, a3=-26.76, a5=-75.96,
        axis_alt=84.05, axis_az=281.47, east_left=True, rms_residual=rms,
        n_matches=n_matches, n_images=60, span_minutes=59.7,
        image_width=3552, image_height=3552, provenance='guided',
        final_tol_px=15.68, chance_ratio=2.09)


@pytest.fixture
def paths(tmp_path, monkeypatch):
    cal = tmp_path / 'allsky_calibration.json'
    monkeypatch.setattr(app_config, 'get_calibration_path', lambda: str(cal))
    monkeypatch.setattr(app_config, 'get_calibration_backup_path',
                        lambda: str(tmp_path / 'allsky_calibration.previous.json'))
    return {'cal': cal,
            'prev': tmp_path / 'allsky_calibration.previous.json',
            'guided': tmp_path / 'allsky_calibration.guided.json'}


def test_guided_copy_written_beside_the_calibration(paths):
    model = _guided_solve()
    model.save(str(paths['cal']))
    assert keep_guided_copy(model, str(paths['cal'])) == str(paths['guided'])
    assert FisheyeModel.load(str(paths['guided'])).rms_residual == pytest.approx(1.39)


def test_an_automatic_fit_is_never_kept_as_guided(paths):
    fit = _joint_fit(1790.3, 8.70, 1047)
    assert keep_guided_copy(fit, str(paths['cal'])) is None
    assert not paths['guided'].exists()


def test_the_sep29_sequence_keeps_the_guided_solve(paths):
    """Guided save at 20:44, automatic saves at 22:27 and 22:57: the backup
    holds only the 22:27 fit, the guided copy still holds the solve."""
    solve = _guided_solve()
    solve.save(str(paths['cal']))
    keep_guided_copy(solve, str(paths['cal']))
    assert save_with_backup(_joint_fit(1790.3, 8.70, 1047)) is None
    assert save_with_backup(_joint_fit(1763.8, 8.26, 1074)) is None

    assert FisheyeModel.load(str(paths['cal'])).cx == pytest.approx(1763.8)
    assert FisheyeModel.load(str(paths['prev'])).cx == pytest.approx(1790.3)
    kept = FisheyeModel.load(str(paths['guided']))
    assert kept.rms_residual == pytest.approx(1.39) and kept.n_images == 1


def test_a_failed_copy_is_a_warning_not_an_error(paths, monkeypatch):
    def fail(self, path):
        raise OSError("disk full")
    monkeypatch.setattr(FisheyeModel, 'save', fail)
    assert keep_guided_copy(_guided_solve(), str(paths['cal'])) is None


def test_the_diagnostics_path_is_the_same_file(tmp_path, monkeypatch):
    monkeypatch.setattr(app_config, 'get_app_data_dir', lambda: str(tmp_path))
    assert app_config.get_guided_calibration_path() == os.path.join(
        str(tmp_path), 'allsky_calibration.guided.json')
