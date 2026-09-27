"""Jev audit verdicts beside human labels: the pure comparison, the stored block,
and the Review tab's Jev filters and columns (offscreen Qt)."""
import json

import pytest
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication

from ml.calibration_store import load_calibration
from ml.jev_label_audit import store_jev_audit
from ml.jev_review import (
    JEV_KEY, banner_text, conflict_kinds, describe_jev, is_corroborated,
    make_jev_block, review_priority, roof_conflict, sky_conflict,
)
from ml.review_tab import JEV_FILTERS, ReviewTab

ANSWERS = {
    "roof": {"type": "choice", "choice": "closed", "confidence": 0.93,
             "probabilities": {"open": 0.07, "closed": 0.93}},
    "sky": {"type": "choice", "choice": "Overcast", "confidence": 0.8,
            "probabilities": {"Clear": 0.1, "Partly Cloudy": 0.1, "Overcast": 0.8}},
    "stars": {"type": "noul", "noul": 0.2},
}


def _labels(roof_open=True, sky="Clear"):
    labels = {"roof_open": roof_open, "labeled_at": "2026-09-20T21:54:27", "label_source": "manual"}
    if sky:
        labels["sky_condition"] = sky
    return labels


def _jev(roof_open=False, sky="Overcast", roof_conf=0.93, sky_conf=0.8):
    return {"model": "typesafe/jev-1.13", "roof_open": roof_open, "roof_p_open": 0.07,
            "roof_confidence": roof_conf, "sky_condition": sky, "sky_confidence": sky_conf,
            "nina_in_state": True, "weather_in_state": True}


# ── block ────────────────────────────────────────────────────────────────────

def test_block_carries_every_answer_and_the_state_flags():
    block = make_jev_block(ANSWERS, "typesafe/jev-1.13-20260917", "2026-09-27T12:00:00",
                           nina_in_state=False, weather_in_state=True)
    assert block["roof_open"] is False and block["roof_p_open"] == 0.07
    assert block["roof_confidence"] == 0.93
    assert block["sky_condition"] == "Overcast" and block["sky_probabilities"]["Overcast"] == 0.8
    assert block["stars_p"] == 0.2
    assert block["nina_in_state"] is False and block["weather_in_state"] is True


def test_block_leaves_out_answers_the_model_did_not_give():
    block = make_jev_block({"roof": {"choice": "open"}}, "m", "t", True, True)
    assert block["roof_open"] is True and block["roof_p_open"] == 1.0
    assert "sky_condition" not in block and "stars_p" not in block


def test_store_keeps_a_label_written_meanwhile(tmp_path):
    path = tmp_path / "calibration_20260101_000000.json"
    path.write_text(json.dumps({"labels": _labels(), "exposure": "10s"}))

    store_jev_audit(path, _jev())

    on_disk = load_calibration(path)
    assert on_disk["labels"] == _labels()
    assert on_disk[JEV_KEY]["sky_condition"] == "Overcast"
    assert on_disk["exposure"] == "10s"


# ── conflicts ────────────────────────────────────────────────────────────────

def test_agreement_is_no_conflict():
    cal = {"labels": _labels(False, None), JEV_KEY: _jev(False, "Overcast")}
    assert conflict_kinds(cal) == []
    assert banner_text(cal) is None
    assert review_priority(cal) == 0.0


def test_roof_and_sky_disagreements_are_named():
    cal = {"labels": _labels(True, "Clear"), JEV_KEY: _jev(False, "Overcast")}
    assert roof_conflict(cal) == (True, False)
    assert sky_conflict(cal) == ("Clear", "Overcast")
    assert conflict_kinds(cal) == ["roof", "sky"]
    assert "roof CLOSED vs manual OPEN" in banner_text(cal)
    assert "sky Overcast vs manual Clear" in banner_text(cal)


def test_sky_is_only_judged_on_open_roof_frames_with_a_sky_label():
    closed = {"labels": _labels(False, "Clear"), JEV_KEY: _jev(False, "Overcast")}
    assert sky_conflict(closed) is None
    no_sky = {"labels": _labels(True, None), JEV_KEY: _jev(True, "Overcast")}
    assert sky_conflict(no_sky) is None
    unlabeled = {"labels": {}, JEV_KEY: _jev(False, "Overcast")}
    assert conflict_kinds(unlabeled) == []


def test_jev_alone_is_never_corroborated():
    cal = {"labels": _labels(True, "Clear"), JEV_KEY: _jev(False, "Overcast")}
    assert not is_corroborated(cal)
    assert "judge by eye" in banner_text(cal)


def test_nina_siding_with_jev_corroborates_the_roof_conflict():
    cal = {"labels": _labels(True, None), JEV_KEY: _jev(False, None),
           "roof_state": {"available": True, "roof_open": False}}
    assert is_corroborated(cal)
    assert "NINA/AI side with Jev" in banner_text(cal)

    cal["ai_suggestion"] = {"roof_open": True}   # one dissenter is enough to withhold it
    assert not is_corroborated(cal)


def test_ai_siding_with_jev_corroborates_the_sky_conflict():
    cal = {"labels": _labels(True, "Clear"), JEV_KEY: _jev(True, "Overcast"),
           "ai_suggestion": {"roof_open": True, "sky_condition": "Overcast"}}
    assert is_corroborated(cal)
    cal["ai_suggestion"]["sky_condition"] = "Clear"
    assert not is_corroborated(cal)


def test_priority_ranks_corroborated_above_confident():
    confident = {"labels": _labels(True, None), JEV_KEY: _jev(False, None, roof_conf=0.99)}
    corroborated = {"labels": _labels(True, None), JEV_KEY: _jev(False, None, roof_conf=0.6),
                    "roof_state": {"available": True, "roof_open": False}}
    assert review_priority(corroborated) > review_priority(confident) > 0


def test_description_names_what_the_model_saw_and_the_mismatch():
    cal = {"labels": _labels(True, "Clear"), JEV_KEY: _jev(False, "Overcast")}
    text = describe_jev(cal)
    assert "image stats, NINA roof, weather" in text
    assert "CLOSED" in text and "MISMATCH" in text and "manual: Clear" in text
    assert "No Jev audit" in describe_jev({"labels": _labels()})


# ── review tab ───────────────────────────────────────────────────────────────

@pytest.fixture
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)


def _sample(folder, ts, cal):
    path = folder / f"calibration_{ts}.json"
    path.write_text(json.dumps(cal))
    return {"timestamp": ts, "folder": folder, "calibration": path}


def test_review_tab_jev_filters_columns_and_ordering(qapp, tmp_path):
    samples = [
        _sample(tmp_path, "20260101_000100",
                {"labels": _labels(True, "Clear"), JEV_KEY: _jev(False, "Overcast", roof_conf=0.99)}),
        _sample(tmp_path, "20260101_000200",
                {"labels": _labels(True, None), JEV_KEY: _jev(False, None, roof_conf=0.6),
                 "roof_state": {"available": True, "roof_open": False}}),
        _sample(tmp_path, "20260101_000300",
                {"labels": _labels(False, None), JEV_KEY: _jev(False, None)}),
        _sample(tmp_path, "20260101_000400", {"labels": _labels(True, "Clear")}),
    ]
    tab = ReviewTab(samples)
    tab.refresh_data()
    combo_index = {text: i for i, text in enumerate(
        tab.filter_combo.itemText(i) for i in range(tab.filter_combo.count()))}
    assert all(combo_index[name] == idx for idx, name in JEV_FILTERS.items())

    tab.filter_combo.setCurrentIndex(9)   # roof conflicts, corroborated first
    assert [d["timestamp"] for d in tab.filtered_data] == ["20260101_000200", "20260101_000100"]
    assert tab.table.item(0, 12).text() == "‼️ roof"
    assert tab.table.item(1, 12).text() == "❌ roof+sky"
    assert tab.table.item(1, 10).text() == "🔴 CLOSED 99%"
    assert tab.table.item(1, 11).text() == "Overcast 80%"
    assert tab.table.item(0, 13).text() == tmp_path.name

    tab.filter_combo.setCurrentIndex(10)
    assert [d["timestamp"] for d in tab.filtered_data] == ["20260101_000100"]
    tab.filter_combo.setCurrentIndex(11)
    assert [d["timestamp"] for d in tab.filtered_data] == ["20260101_000200"]
    tab.filter_combo.setCurrentIndex(12)
    assert len(tab.filtered_data) == 3
    assert tab.table.item(2, 12).text() == "✅"
    tab.filter_combo.setCurrentIndex(13)
    assert [d["timestamp"] for d in tab.filtered_data] == ["20260101_000400"]
    assert tab.table.item(0, 10).text() == "--"

    tab.shutdown()
    tab.deleteLater()


def test_review_table_never_auto_resizes_columns_per_item(qapp, tmp_path):
    """ResizeToContents re-measures a column on every setItem while the table is
    visible; on 2,000 rows that froze the GUI for minutes (Sep 2026)."""
    from PySide6.QtWidgets import QHeaderView
    tab = ReviewTab([_sample(tmp_path, "20260101_000100", {"labels": _labels()})])
    tab.refresh_data()
    header = tab.table.horizontalHeader()
    modes = {header.sectionResizeMode(c) for c in range(tab.table.columnCount())}
    assert QHeaderView.ResizeToContents not in modes
    assert header.sectionResizeMode(ReviewTab.FOLDER_COLUMN) == QHeaderView.Stretch
    tab.shutdown()
    tab.deleteLater()


def test_labeling_tool_shows_the_verdict_and_banner_without_prefilling(qapp, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    import ml.labeling_tool as lt

    disputed = {"labels": _labels(True, "Clear"), JEV_KEY: _jev(False, "Overcast"),
                "roof_state": {"available": True, "source": "nina_api", "roof_open": False}}
    (tmp_path / "calibration_20260101_000000.json").write_text(json.dumps(disputed))
    (tmp_path / "calibration_20260101_000100.json").write_text(json.dumps({JEV_KEY: _jev(True, "Clear")}))
    monkeypatch.setattr("ml.labeling_tool.load_classifiers", lambda: (None, None))
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.No)

    window = lt.LabelingTool(tmp_path)
    try:
        window.load_sample(0)
        assert window.mismatch_banner.isVisibleTo(window)
        assert "JEV DISAGREES" in window.mismatch_banner.text()
        assert "NINA/AI side with Jev" in window.mismatch_banner.text()
        assert "MISMATCH" in window.context_panel.jev_text.toPlainText()
        assert window.labels_widget.roof_open.isChecked()   # the human label stays on the form

        window.load_sample(1)   # unlabelled: verdict shown, form filled from NINA/AI/CNN only
        assert not window.mismatch_banner.isVisibleTo(window)
        assert "OPEN" in window.context_panel.jev_text.toPlainText()
    finally:
        window.labels_widget.mark_saved()
        window.close()
        window.deleteLater()
