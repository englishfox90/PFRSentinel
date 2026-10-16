"""
Restoring a calibration from the history: CalibrationHistoryController,
the Calibration History dialog, the Lens Calibration card's button and the
main window's wiring (offscreen Qt).

The headline case is discussion #105's: a guided solve replaced overnight by
automatic refinements, wanted back the next evening.
"""
import gc
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("PySide6")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from PySide6.QtCore import QEvent, QTimer
from PySide6.QtWidgets import QApplication, QWidget

import services.app_config as app_config
from services.allsky import calibration_history as ch
from services.allsky.calibration_store import save_with_backup
from services.allsky.fisheye import FisheyeModel
from ui.controllers.calibration_history_controller import CalibrationHistoryController
from ui.main_window.settings import _MainWindowSettingsMixin
from ui.panels import allsky_calibration_history_dialog as hd

T0 = datetime(2026, 9, 29, 20, 44, tzinfo=timezone.utc)


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _flush(qapp):
    for _ in range(3):
        gc.collect()
        qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        qapp.processEvents()


def _guided_solve():
    return FisheyeModel(
        cx=1776.0, cy=1776.0, a1=1268.5, axis_alt=88.5, axis_az=287.7,
        east_left=True, rms_residual=1.39, n_matches=8, n_images=1,
        span_minutes=0.0, image_width=3552, image_height=3552,
        provenance='guided', calibrated_at='2026-09-29T20:44:00+00:00')


def _joint_fit(cx=1790.3):
    return FisheyeModel(
        cx=cx, cy=1654.2, a1=1282.1, a3=-26.76, a5=-75.96,
        axis_alt=84.05, axis_az=281.47, east_left=True, rms_residual=8.70,
        n_matches=1047, n_images=60, span_minutes=59.7,
        image_width=3552, image_height=3552, provenance='guided',
        final_tol_px=15.68, chance_ratio=2.09,
        calibrated_at='2026-09-29T22:57:00+00:00')


class _AllSky:
    def __init__(self):
        self.adopted = []
        self.files = []

    def adopt_model(self, model, status):
        self.adopted.append((model, status))
        return {}

    def remember_calibration_file(self, path):
        self.files.append(path)


@pytest.fixture
def paths(tmp_path, monkeypatch):
    cal = tmp_path / 'allsky_calibration.json'
    monkeypatch.setattr(app_config, 'get_calibration_path', lambda: str(cal))
    monkeypatch.setattr(app_config, 'get_calibration_backup_path',
                        lambda: str(tmp_path / 'allsky_calibration.previous.json'))
    return {'cal': cal, 'prev': tmp_path / 'allsky_calibration.previous.json',
            'guided': tmp_path / 'allsky_calibration.guided.json'}


@pytest.fixture
def overwritten_guided(paths):
    """The #105 night: guided solve, then automatic refinements over it."""
    save_with_backup(_guided_solve(), stamp_time=False, source=ch.SOURCE_GUIDED)
    save_with_backup(_joint_fit(), stamp_time=False, backup=True, source=ch.SOURCE_REFINEMENT)
    save_with_backup(_joint_fit(1795.0), stamp_time=False, backup=True,
                     source=ch.SOURCE_REFINEMENT)
    return paths


# --- controller ------------------------------------------------------------

def test_restore_puts_the_guided_solve_back_live(qapp, overwritten_guided):
    allsky = _AllSky()
    ctrl = CalibrationHistoryController(allsky)
    finished = []
    ctrl.restore_finished.connect(lambda ok, msg: finished.append((ok, msg)))
    guided = ctrl.newest_guided()
    ok, msg = ctrl.restore(guided.entry_id)
    assert ok and finished == [(True, msg)]
    on_disk = FisheyeModel.load(str(overwritten_guided['cal']))
    assert on_disk.rms_residual == pytest.approx(1.39)
    assert on_disk.calibrated_at == '2026-09-29T20:44:00+00:00'
    # The model it replaced is backed up and kept in the history.
    assert FisheyeModel.load(str(overwritten_guided['prev'])).cx == pytest.approx(1795.0)
    assert ctrl.entries()[0].source == ch.SOURCE_RESTORED
    assert ctrl.entries()[0].restored_from == guided.entry_id
    # Seeded exactly as a fresh result is, and the guided copy refreshed.
    [(model, status)] = allsky.adopted
    assert model.rms_residual == pytest.approx(1.39)
    assert 'Restored the calibration from' in status and 'Guided Calibration' in status
    assert allsky.files == [str(overwritten_guided['cal'])]
    assert FisheyeModel.load(str(overwritten_guided['guided'])).rms_residual == pytest.approx(1.39)
    ctrl.deleteLater()
    _flush(qapp)


def test_a_restore_can_be_undone_from_the_history(qapp, overwritten_guided):
    ctrl = CalibrationHistoryController(_AllSky())
    ctrl.restore(ctrl.newest_guided().entry_id)
    replaced = next(e for e in ctrl.entries() if e.source == ch.SOURCE_REFINEMENT)
    assert ctrl.restore(replaced.entry_id)[0]
    assert FisheyeModel.load(str(overwritten_guided['cal'])).n_matches == 1047
    ctrl.deleteLater()
    _flush(qapp)


def test_restore_of_a_missing_entry_changes_nothing(qapp, overwritten_guided):
    allsky = _AllSky()
    ctrl = CalibrationHistoryController(allsky)
    before = overwritten_guided['cal'].read_text()
    ok, msg = ctrl.restore('cal_nope')
    assert not ok and 'could not be read' in msg
    assert overwritten_guided['cal'].read_text() == before
    assert allsky.adopted == []
    ctrl.deleteLater()
    _flush(qapp)


# --- dialog -----------------------------------------------------------------

def _entries(hdir):
    ch.record(_guided_solve(), ch.SOURCE_GUIDED, out_dir=hdir, now=T0)
    ch.record(_joint_fit(), ch.SOURCE_REFINEMENT, out_dir=hdir, now=T0 + timedelta(hours=2))
    return ch.list_entries(hdir)


@pytest.fixture
def dialog(qapp, tmp_path, monkeypatch):
    dialogs = []

    def make(entries):
        dlg = hd.CalibrationHistoryDialog(entries)
        requested = []
        dlg.restore_requested.connect(requested.append)
        dlg.requested = requested
        dialogs.append(dlg)
        return dlg

    yield make
    for dlg in dialogs:
        dlg.close()
        dlg.deleteLater()
    _flush(qapp)


def test_dialog_lists_entries_newest_first_with_guided_marked(dialog, tmp_path):
    dlg = dialog(_entries(str(tmp_path / 'h')))
    assert dlg._table.rowCount() == 2
    assert 'Automatic refinement' in dlg._table.item(0, 1).text()
    assert dlg._table.item(1, 1).text().startswith('Guided Calibration')
    assert '★' in dlg._table.item(1, 1).text()
    assert dlg._table.item(0, 3).text() == '8.70 px'
    assert not dlg._empty.isVisibleTo(dlg)


def test_restore_selected_confirms_then_asks_for_that_entry(dialog, tmp_path, monkeypatch):
    entries = _entries(str(tmp_path / 'h'))
    dlg = dialog(entries)
    assert not dlg._restore_btn.isEnabled()
    dlg._table.selectRow(0)
    assert dlg._restore_btn.isEnabled()
    monkeypatch.setattr(dlg, 'confirm', lambda entry: False)
    dlg._restore_btn.click()
    assert dlg.requested == []
    monkeypatch.setattr(dlg, 'confirm', lambda entry: True)
    dlg._restore_btn.click()
    assert dlg.requested == [entries[0].entry_id]


def test_restore_last_guided_picks_the_newest_guided_solve(dialog, tmp_path, monkeypatch):
    entries = _entries(str(tmp_path / 'h'))
    dlg = dialog(entries)
    monkeypatch.setattr(dlg, 'confirm', lambda entry: True)
    assert dlg._guided_btn.isEnabled()
    dlg._guided_btn.click()
    assert dlg.requested == [entries[1].entry_id]


def test_no_guided_entry_disables_the_shortcut_and_empty_says_so(dialog):
    dlg = dialog([])
    assert not dlg._guided_btn.isEnabled()
    assert dlg._empty.isVisibleTo(dlg)


def test_show_result_closes_on_success_and_explains_a_failure(dialog, tmp_path):
    dlg = dialog(_entries(str(tmp_path / 'h')))
    dlg.show_result(False, "Restore failed: disk full")
    assert dlg._result.isVisibleTo(dlg) and 'disk full' in dlg._result.text()
    assert not dlg.restored
    dlg.show_result(True, "ok")
    assert dlg.restored


# --- panel + main window ----------------------------------------------------

def test_lens_calibration_card_has_a_history_button(qapp):
    from ui.panels.allsky_settings import AllSkySettingsPanel
    panel = AllSkySettingsPanel()
    seen = []
    panel.settings_changed.connect(seen.append)
    panel._history_btn.click()
    assert seen == [{'_action': 'calibration_history'}]
    panel.close()
    panel.deleteLater()
    _flush(qapp)


class _Window(_MainWindowSettingsMixin, QWidget):
    def __init__(self, allsky):
        super().__init__()
        self.allsky_controller = allsky
        self.notes = []

    def _notify(self, message, category='info'):
        self.notes.append(message)


def _open_dialog():
    for w in QApplication.topLevelWidgets():
        if isinstance(w, hd.CalibrationHistoryDialog) and w.isVisible():
            return w
    return None


def test_main_window_restores_through_the_controller(qapp, overwritten_guided, monkeypatch):
    monkeypatch.setattr(hd.CalibrationHistoryDialog, 'confirm', lambda self, entry: True)
    allsky = _AllSky()
    win = _Window(allsky)

    def drive():
        dlg = _open_dialog()
        if dlg is None:
            QTimer.singleShot(20, drive)
            return
        dlg._guided_btn.click()

    QTimer.singleShot(0, drive)
    win._on_allsky_panel_changed({'_action': 'calibration_history'})
    assert len(allsky.adopted) == 1
    assert allsky.adopted[0][0].rms_residual == pytest.approx(1.39)
    assert win.notes and 'restored' in win.notes[0].lower()
    win.close()
    win.deleteLater()
    _flush(qapp)
    assert _open_dialog() is None


# --- what the background service records -----------------------------------

def _thin(rms, n, n_images=1, span=0.0):
    return FisheyeModel(cx=960.0, cy=540.0, a1=600.0, rms_residual=rms,
                        n_matches=n, n_images=n_images, span_minutes=span,
                        calibrated_at="2026-01-01T00:00:00+00:00")


@pytest.mark.parametrize('escape, source', [(False, ch.SOURCE_REFINEMENT),
                                            (True, ch.SOURCE_ESCAPE)])
def test_service_names_refinement_and_escape_saves(qapp, escape, source):
    from services.allsky.calibration_service import CalibrationService
    svc = CalibrationService()
    saved = []
    svc._save_model = lambda m, **kw: saved.append(kw)
    svc._model = _thin(4.0, 11)
    svc._quality = 'preliminary'
    svc._escape_attempt = escape
    svc._refine_gen = svc._model_generation
    svc._on_refine_done(_thin(9.0, 200, 20, 60.0), 20, 60.0, evidence=True)
    assert saved == [{'source': source}]
    svc.shutdown()
    svc.deleteLater()
    _flush(qapp)
