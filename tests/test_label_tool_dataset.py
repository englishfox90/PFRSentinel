"""Labeling-tool data layer: dataset discovery, label suggestion, calibration writes."""
import json
from types import SimpleNamespace

from ml.calibration_store import load_calibration, save_calibration
from ml.dataset_files import find_sample_sets, iter_calibration_files
from ml.label_suggestion import (
    suggest_labels, labels_from_suggestion, describe_sources, roof_votes,
)


def _write_cal(folder, ts, cal=None):
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"calibration_{ts}.json"
    path.write_text(json.dumps(cal or {}))
    return path


def _nina(roof_open):
    return {'roof_state': {'available': True, 'source': 'nina_api', 'roof_open': roof_open}}


# ── discovery ────────────────────────────────────────────────────────────────

def test_backup_copy_never_shadows_the_live_calibration(tmp_path):
    live = _write_cal(tmp_path, "20260624_101632", {'labels': {'roof_open': False}})
    _write_cal(tmp_path / "_backup_scrub_20260626_224438", "20260624_101632",
               {'labels': {'roof_open': True}})

    samples = find_sample_sets(tmp_path)

    assert [s['calibration'] for s in samples] == [live]
    assert samples[0]['folder'] == tmp_path


def test_removed_frames_stay_out_of_the_dataset(tmp_path):
    _write_cal(tmp_path, "20260101_000000")
    _write_cal(tmp_path / "_removed", "20260102_000000")

    assert [p.parent for p in iter_calibration_files(tmp_path)] == [tmp_path]


def test_real_subfolders_are_still_searched(tmp_path):
    _write_cal(tmp_path / "rig_a", "20260101_000000")
    _write_cal(tmp_path / "rig_a" / "_backup", "20260101_000001")

    assert [s['timestamp'] for s in find_sample_sets(tmp_path)] == ["20260101_000000"]


def test_lum_is_only_taken_from_beside_its_calibration(tmp_path):
    _write_cal(tmp_path, "20260101_000000")
    (tmp_path / "_removed").mkdir()
    (tmp_path / "_removed" / "lum_20260101_000000.fits").write_bytes(b"x")
    assert 'lum' not in find_sample_sets(tmp_path)[0]

    (tmp_path / "lum_20260101_000000.fits").write_bytes(b"x")
    assert find_sample_sets(tmp_path)[0]['lum'] == tmp_path / "lum_20260101_000000.fits"


# ── suggestion ───────────────────────────────────────────────────────────────

def test_nina_outranks_ai_for_the_roof_call():
    cal = {**_nina(True), 'ai_suggestion': {'roof_open': False}}
    sug = suggest_labels(cal)
    assert sug['roof_open'] is True and sug['roof_source'] == 'NINA'
    assert sug['agreed'] is False


def test_nina_string_booleans_are_understood():
    assert roof_votes(_nina('False')) == {'NINA': False}


def test_agreement_needs_two_sources_and_no_dissent():
    assert suggest_labels(_nina(False))['agreed'] is False
    assert suggest_labels({**_nina(False), 'ai_suggestion': {'roof_open': False}})['agreed'] is True
    dissenting_ml = SimpleNamespace(roof_open=True)
    assert suggest_labels({**_nina(False), 'ai_suggestion': {'roof_open': False}},
                          roof_pred=dissenting_ml)['agreed'] is False


def test_closed_roof_suggests_no_sky_stars_or_moon():
    cal = {**_nina(False), 'ai_suggestion': {'roof_open': True, 'sky_condition': 'Clear'},
           'moon_context': {'available': True, 'moon_is_up': True}}
    sug = suggest_labels(cal)
    assert (sug['sky_condition'], sug['stars_visible'], sug['moon_visible']) == ('', False, False)
    labels = labels_from_suggestion(sug, "2026-09-20T22:00:00", 'batch_confirm')
    assert 'sky_condition' not in labels and 'clouds_visible' not in labels
    assert labels['label_source'] == 'batch_confirm'


def test_ai_sky_is_ignored_when_the_ai_thought_the_roof_was_closed():
    sky_pred = SimpleNamespace(sky_condition='Partly Cloudy', stars_visible=True,
                               star_density=0.4, moon_visible=False)
    cal = {**_nina(True), 'ai_suggestion': {'roof_open': False, 'sky_condition': ''}}
    sug = suggest_labels(cal, sky_pred=sky_pred)
    assert (sug['sky_condition'], sug['sky_source']) == ('Partly Cloudy', 'ML')
    assert sug['clouds_visible'] is True and sug['star_density'] == 0.4


def test_no_sources_falls_back_to_the_corner_ratio():
    assert suggest_labels({'corner_analysis': {'corner_to_center_ratio': 0.5}})['roof_open'] is True
    assert suggest_labels({})['roof_source'] == 'corner ratio'


def test_source_line_marks_who_agrees():
    sug = suggest_labels({**_nina(True), 'ai_suggestion': {'roof_open': False}})
    assert describe_sources(sug) == "roof: NINA ✗AI"


# ── writes ───────────────────────────────────────────────────────────────────

def test_save_replaces_the_file_and_leaves_no_temp_behind(tmp_path):
    path = _write_cal(tmp_path, "20260101_000000", {'a': 1})
    save_calibration(path, {'a': 2})
    assert load_calibration(path) == {'a': 2}
    assert [p.name for p in tmp_path.iterdir()] == [path.name]
