"""Labeling tool: calibration JSONs with several writers, and worker-thread lifetime."""
import json
import threading

import pytest
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication, QMessageBox

from ml.ai_worker import AiLabelWorker, store_ai_suggestion
from ml.calibration_store import load_calibration, save_calibration, update_calibration
from ml.worker_lifetime import join_worker, stop_worker


@pytest.fixture
def cal_path(tmp_path):
    path = tmp_path / "calibration_20260101_000000.json"
    path.write_text(json.dumps({'roof_state': {'available': False}}))
    return path


def _flush_qt():
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    QCoreApplication.processEvents()


# ── calibration_store ────────────────────────────────────────────────────────

def test_concurrent_updates_to_one_file_all_survive(cal_path):
    """Six AI threads plus the GUI thread is the real load; every write must land."""
    errors = []

    def writer(n):
        try:
            for i in range(25):
                update_calibration(cal_path, lambda cal, n=n, i=i: cal.__setitem__(f"w{n}_{i}", True))
        except Exception as e:   # a shared temp file shows up here as FileNotFoundError
            errors.append(e)

    threads = [threading.Thread(target=writer, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    cal = load_calibration(cal_path)
    assert sum(1 for k in cal if k.startswith("w")) == 8 * 25
    assert [p.name for p in cal_path.parent.iterdir()] == [cal_path.name]


def test_a_label_saved_during_the_ai_call_is_kept(cal_path):
    """The lost update: AI read the file, the human labeled it, the AI wrote back."""
    update_calibration(cal_path, lambda cal: cal.__setitem__('labels', {'labeled_at': 'now'}))
    store_ai_suggestion(cal_path, {'roof_open': True})

    cal = load_calibration(cal_path)
    assert cal['labels'] == {'labeled_at': 'now'}
    assert cal['ai_suggestion'] == {'roof_open': True}


def test_mutate_returning_false_leaves_the_file_untouched(cal_path):
    before = cal_path.read_bytes()
    assert update_calibration(cal_path, lambda cal: False) is None
    assert cal_path.read_bytes() == before


def test_unparseable_file_raises_unless_the_caller_accepts_it(cal_path):
    cal_path.write_text("{ truncated")
    with pytest.raises(ValueError):
        update_calibration(cal_path, lambda cal: None)
    saved = update_calibration(cal_path, lambda cal: cal.__setitem__('labels', {}), missing_ok=True)
    assert saved == {'labels': {}} and load_calibration(cal_path) == saved


def test_failed_write_leaves_the_original_and_no_temp_file(cal_path):
    with pytest.raises(TypeError):
        save_calibration(cal_path, {'bad': object()})
    assert load_calibration(cal_path) == {'roof_state': {'available': False}}
    assert [p.name for p in cal_path.parent.iterdir()] == [cal_path.name]


# ── worker lifetime ──────────────────────────────────────────────────────────

@pytest.fixture
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app
    _flush_qt()


def test_cancelled_ai_worker_stops_without_waiting_for_the_request(qapp, cal_path, monkeypatch):
    release = threading.Event()

    def slow_label(*args, **kwargs):
        release.wait(30)
        return {'roof_open': False}

    monkeypatch.setattr("ml.ai_worker.label_lum_frame", slow_label)
    jobs = [{'cal_path': str(cal_path), 'lum_path': 'x.fits', 'timestamp': f"t{i}"} for i in range(20)]
    worker = AiLabelWorker(jobs, workers=2)
    worker.start()

    try:
        stop_worker(worker)   # would block ~30 s if cancel waited on in-flight requests
        assert worker.isFinished()
    finally:
        release.set()
    worker.deleteLater()


def test_join_worker_returns_only_once_the_thread_has_exited(qapp, cal_path, monkeypatch):
    monkeypatch.setattr("ml.ai_worker.label_lum_frame", lambda *a, **k: {'roof_open': True})
    finished_when_released = []
    worker = AiLabelWorker([{'cal_path': str(cal_path), 'lum_path': 'x.fits', 'timestamp': 't'}])

    def on_completed(*_):
        join_worker(worker)
        finished_when_released.append(worker.isFinished())

    worker.completed.connect(on_completed)
    worker.start()
    worker.wait()
    QCoreApplication.processEvents()

    assert finished_when_released == [True]
    worker.deleteLater()


# ── batch confirm ────────────────────────────────────────────────────────────

def test_batch_confirm_never_overwrites_a_label_saved_since_the_scan(qapp, tmp_path):
    from ml.batch_confirm_tab import BatchConfirmTab
    from ml.label_suggestion import suggest_labels

    entries = []
    for ts in ("20260101_000000", "20260101_000100", "20260101_000200"):
        cal = tmp_path / f"calibration_{ts}.json"
        cal.write_text(json.dumps({}))
        sample = {'timestamp': ts, 'calibration': cal, 'lum': tmp_path / f"lum_{ts}.fits"}
        entries.append({'sample': sample, 'suggestion': suggest_labels({})})

    tab = BatchConfirmTab([e['sample'] for e in entries], None, None)
    saved_signal = []
    tab.labels_saved.connect(saved_signal.append)
    tab.agreed_only.setChecked(False)
    tab._on_ready(entries)
    assert len(tab.tiles) == 3

    human = {'roof_open': True, 'labeled_at': 'earlier', 'label_source': 'manual'}
    update_calibration(entries[0]['sample']['calibration'], lambda cal: cal.__setitem__('labels', human))
    tab.tiles[1].mousePressEvent(None)   # left out by the user
    tab.confirm_page()

    assert load_calibration(entries[0]['sample']['calibration'])['labels'] == human
    assert 'labels' not in load_calibration(entries[1]['sample']['calibration'])
    assert load_calibration(entries[2]['sample']['calibration'])['labels']['label_source'] == 'batch_confirm'
    assert saved_signal == [["20260101_000200"]]
    assert [e['sample']['timestamp'] for e in tab.entries] == ["20260101_000100"]

    tab.shutdown()
    tab.deleteLater()


# ── main window ──────────────────────────────────────────────────────────────

@pytest.fixture
def tool(qapp, tmp_path, monkeypatch):
    for ts in ("20260101_000000", "20260101_000100"):
        (tmp_path / f"calibration_{ts}.json").write_text(json.dumps({}))
        (tmp_path / f"lum_{ts}.fits").write_bytes(b"not a real frame")   # only its presence matters
    (tmp_path / "calibration_20260101_000200.json").write_text("{ truncated")

    monkeypatch.setattr("ml.labeling_tool.load_classifiers", lambda: (None, None))
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warnings.append(a[1:3]) or QMessageBox.Ok)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.No)

    import ml.labeling_tool as lt
    window = lt.LabelingTool(tmp_path)
    window.warnings = warnings
    yield window
    window.labels_widget.mark_saved()
    window.close()
    window.deleteLater()
    _flush_qt()


def test_ai_result_refreshes_the_frame_that_was_sent_not_the_one_on_screen(tool):
    sent = tool.samples[0]
    update_calibration(sent['calibration'], lambda cal: cal.__setitem__('ai_suggestion', {'roof_open': True}))
    tool.load_sample(1)   # the user moved on while the request was in flight

    tool._refresh_meta(sent['timestamp'])   # what frame_done delivers
    tool._on_ai_suggestion_done(1, 0, "")

    assert tool.meta_cache[sent['timestamp']]['ai_present'] is True
    assert sent['timestamp'] not in [j['timestamp'] for j in tool._ai_pending_jobs()]


def test_save_and_next_on_an_unreadable_frame_says_why(tool):
    tool.load_sample(2)
    assert tool.mismatch_banner.isVisibleTo(tool) and "could not be read" in tool.mismatch_banner.text()

    tool._do_save_next()

    assert tool.current_index == 2
    assert [title for title, _ in tool.warnings] == ["Cannot label this frame"]
    assert (tool.data_dir / "calibration_20260101_000200.json").read_text() == "{ truncated"


def test_an_empty_but_valid_calibration_can_still_be_labeled(tool):
    tool.load_sample(0)
    tool.labels_widget.toggle_roof()
    assert tool._do_save() is True
    assert load_calibration(tool.samples[0]['calibration'])['labels']['label_source'] == 'manual'
    assert tool.warnings == []
