"""
calibration_history — every calibration that went live is kept, bounded,
and can be read back as a model.

Discussion #105: on 2026-09-29 two automatic refinements replaced a guided
solve and then the one-step backup holding it; the next night the reporter
re-did Guided Calibration because nothing on disk said what had been live.
"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import services.app_config as app_config
from services import diagnostics_bundle
from services.allsky import calibration_history as ch
from services.allsky.calibration_store import save_with_backup
from services.allsky.fisheye import FisheyeModel

T0 = datetime(2026, 9, 29, 20, 44, tzinfo=timezone.utc)


def _guided_solve(rms=1.39):
    return FisheyeModel(
        cx=1776.0, cy=1776.0, a1=1268.5, axis_alt=88.5, axis_az=287.7,
        east_left=True, rms_residual=rms, n_matches=8, n_images=1,
        span_minutes=0.0, image_width=3552, image_height=3552,
        provenance='guided', calibrated_at='2026-09-29T20:44:00+00:00')


def _joint_fit(cx=1790.3, rms=8.70, n=1047, stamp='2026-09-29T22:27:00+00:00'):
    return FisheyeModel(
        cx=cx, cy=1654.2, a1=1282.1, a3=-26.76, a5=-75.96,
        axis_alt=84.05, axis_az=281.47, east_left=True, rms_residual=rms,
        n_matches=n, n_images=60, span_minutes=59.7,
        image_width=3552, image_height=3552, provenance='guided',
        final_tol_px=15.68, chance_ratio=2.09, calibrated_at=stamp)


@pytest.fixture
def hdir(tmp_path):
    return str(tmp_path / 'history')


@pytest.fixture
def paths(tmp_path, monkeypatch):
    cal = tmp_path / 'allsky_calibration.json'
    monkeypatch.setattr(app_config, 'get_calibration_path', lambda: str(cal))
    monkeypatch.setattr(app_config, 'get_calibration_backup_path',
                        lambda: str(tmp_path / 'allsky_calibration.previous.json'))
    return {'cal': cal, 'prev': tmp_path / 'allsky_calibration.previous.json'}


def test_record_round_trips_the_model_and_its_facts(hdir):
    guided = _guided_solve()
    entry_id = ch.record(guided, ch.SOURCE_GUIDED, out_dir=hdir, now=T0)
    [entry] = ch.list_entries(hdir)
    assert entry.entry_id == entry_id
    assert entry.is_guided and entry.label == 'Guided Calibration'
    assert entry.rms_residual == pytest.approx(1.39)
    assert entry.n_matches == 8 and entry.provenance == 'guided'
    assert entry.saved_at == T0.isoformat()
    model = ch.load_model(entry_id, hdir)
    assert model is not None and model.a1 == pytest.approx(1268.5)


def test_replaced_links_to_the_model_it_overwrote(hdir):
    ch.record(_joint_fit(), ch.SOURCE_REFINEMENT, replaced=_guided_solve(),
              out_dir=hdir, now=T0)
    [entry] = ch.list_entries(hdir)
    assert entry.replaced == '2026-09-29T20:44:00+00:00'
    assert entry.chance_ratio == pytest.approx(2.09)
    assert entry.final_tol_px == pytest.approx(15.68)


def test_entries_listed_newest_first(hdir):
    for i, source in enumerate((ch.SOURCE_GUIDED, ch.SOURCE_REFINEMENT, ch.SOURCE_ESCAPE)):
        ch.record(_joint_fit(), source, out_dir=hdir, now=T0 + timedelta(minutes=i))
    assert [e.source for e in ch.list_entries(hdir)] == [
        ch.SOURCE_ESCAPE, ch.SOURCE_REFINEMENT, ch.SOURCE_GUIDED]
    assert ch.newest_guided(ch.list_entries(hdir)).source == ch.SOURCE_GUIDED


def test_the_sep_29_night_keeps_the_guided_solve(hdir):
    """The #105 sequence: guided at 20:44, refinements at 22:27 and 22:57.
    The history still holds the guided model after both."""
    ch.record(_guided_solve(), ch.SOURCE_GUIDED, out_dir=hdir, now=T0)
    ch.record(_joint_fit(), ch.SOURCE_REFINEMENT, out_dir=hdir,
              now=T0 + timedelta(minutes=103))
    ch.record(_joint_fit(cx=1795.0), ch.SOURCE_REFINEMENT, out_dir=hdir,
              now=T0 + timedelta(minutes=133))
    guided = ch.newest_guided(ch.list_entries(hdir))
    assert guided is not None
    assert ch.load_model(guided.entry_id, hdir).rms_residual == pytest.approx(1.39)


def test_retention_counts_guided_and_automatic_separately(hdir):
    for i in range(4):
        ch.record(_guided_solve(), ch.SOURCE_GUIDED, out_dir=hdir, now=T0 + timedelta(seconds=i))
    for i in range(12):
        ch.record(_joint_fit(), ch.SOURCE_REFINEMENT, out_dir=hdir,
                  now=T0 + timedelta(minutes=10 + i))
    ch.prune(hdir, max_guided=2, max_other=5)
    entries = ch.list_entries(hdir)
    assert sum(e.is_guided for e in entries) == 2
    assert sum(not e.is_guided for e in entries) == 5
    # A busy night of refinements never pushes the newest guided solves out.
    newest_guided = max(e.entry_id for e in entries if e.is_guided)
    assert newest_guided.startswith('cal_20260929T204403')


def test_default_retention_is_bounded(hdir):
    for i in range(ch.MAX_OTHER + 5):
        ch.record(_joint_fit(), ch.SOURCE_REFINEMENT, out_dir=hdir,
                  now=T0 + timedelta(minutes=i))
    assert len(ch.list_entries(hdir)) == ch.MAX_OTHER


def test_prune_deletes_files_only(hdir):
    os.makedirs(os.path.join(hdir, 'cal_20200101T000000_000000Z_refinement.json'))
    ch.record(_joint_fit(), ch.SOURCE_REFINEMENT, out_dir=hdir, now=T0)
    ch.prune(hdir, max_guided=0, max_other=0)
    assert os.path.isdir(os.path.join(hdir, 'cal_20200101T000000_000000Z_refinement.json'))
    assert os.path.isdir(hdir)
    assert ch.list_entries(hdir) == []


def test_unreadable_entries_are_skipped(hdir):
    ch.record(_joint_fit(), ch.SOURCE_REFINEMENT, out_dir=hdir, now=T0)
    with open(os.path.join(hdir, 'cal_20260930T000000_000000Z_refinement.json'), 'w') as f:
        f.write('{ not json')
    with open(os.path.join(hdir, 'cal_20260930T010000_000000Z_manual.json'), 'w') as f:
        json.dump({'something': 'else'}, f)
    assert len(ch.list_entries(hdir)) == 1
    assert ch.load_model('cal_20260930T000000_000000Z_refinement', hdir) is None


def test_load_model_refuses_paths_and_missing_ids(hdir):
    ch.record(_joint_fit(), ch.SOURCE_REFINEMENT, out_dir=hdir, now=T0)
    assert ch.load_model('', hdir) is None
    assert ch.load_model('../allsky_calibration', hdir) is None
    assert ch.load_model('cal_missing', hdir) is None


def test_record_failure_is_a_warning_not_an_exception(tmp_path):
    blocker = tmp_path / 'file_not_dir'
    blocker.write_text('x')
    assert ch.record(_joint_fit(), ch.SOURCE_REFINEMENT, out_dir=str(blocker)) is None


def test_source_for_model_tells_the_guided_solve_from_a_manual_fit():
    assert ch.source_for_model(_guided_solve()) == ch.SOURCE_GUIDED
    assert ch.source_for_model(_joint_fit()) == ch.SOURCE_MANUAL


def test_save_with_backup_records_each_automatic_save(paths):
    _guided_solve().save(str(paths['cal']))
    assert save_with_backup(_joint_fit(), source=ch.SOURCE_REFINEMENT) is None
    [entry] = ch.list_entries()
    assert entry.source == ch.SOURCE_REFINEMENT
    assert entry.replaced == '2026-09-29T20:44:00+00:00'
    assert paths['prev'].is_file()


def test_a_restamp_records_nothing(paths):
    model = _joint_fit()
    save_with_backup(model, source=ch.SOURCE_REFINEMENT)
    save_with_backup(model, stamp_time=False)
    assert len(ch.list_entries()) == 1


def test_restore_save_backs_up_but_keeps_the_models_own_stamp(paths):
    _joint_fit().save(str(paths['cal']))
    guided = _guided_solve()
    assert save_with_backup(guided, stamp_time=False, backup=True,
                            source=ch.SOURCE_RESTORED, restored_from='cal_x') is None
    assert FisheyeModel.load(str(paths['cal'])).calibrated_at == '2026-09-29T20:44:00+00:00'
    assert FisheyeModel.load(str(paths['prev'])).rms_residual == pytest.approx(8.70)
    [entry] = ch.list_entries()
    assert entry.source == ch.SOURCE_RESTORED and entry.restored_from == 'cal_x'


def test_diagnostics_bundle_lists_every_entry(paths):
    save_with_backup(_guided_solve(), source=ch.SOURCE_GUIDED)
    save_with_backup(_joint_fit(), source=ch.SOURCE_REFINEMENT)
    files = diagnostics_bundle.allsky_calibration_history_files()
    assert len(files) == 2
    assert all(k.startswith('allsky/calibration_history/cal_') for k in files)
    assert all(os.path.isfile(p) for p in files.values())


def test_bundle_with_no_history_is_empty(tmp_path):
    assert ch.bundle_files(str(tmp_path / 'none')) == {}
