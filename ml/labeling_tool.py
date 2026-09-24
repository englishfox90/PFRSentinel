#!/usr/bin/env python3
"""
ML Labeling Tool for PFR Sentinel

Labeling tab: lum FITS image (framed in the colour of its roof tag) | data
(context, ML + AI predictions) | manual label form. Batch Confirm tab: label a
grid of same-tagged frames at once. Review tab: predictions vs labels.
Edits the `labels` block of each calibration JSON.

Usage:
    python ml/labeling_tool.py "D:\\Pier Camera ML Data"
    python ml/labeling_tool.py  # Uses default path

Keys (Labeling tab): A/D or ←/→ navigate · Space save & next · S save ·
R roof · 1/2/3 Clear/Partly/Overcast · C clouds · T stars · M moon · Del remove
Keys (Batch Confirm tab): Enter confirm page · N skip page
"""
import os
import sys
import argparse
from pathlib import Path

# Add parent for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QMessageBox, QTabWidget
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QShortcut

from ml.labeling_io import (
    find_sample_sets, load_fits_as_qpixmap, create_placeholder_pixmap,
    remove_sample_files,
)
from ml.calibration_store import load_calibration
from ml.frame_prediction import load_classifiers, predict_frame, describe_prediction
from ml.label_suggestion import to_bool
from ml.labels_widget import LabelsWidget
from ml.context_widget import ContextPanel
from ml.tagged_image_view import TaggedImageView
from ml.ai_worker import AiLabelWorker
from ml.review_tab import ReviewTab
from ml.batch_confirm_tab import BatchConfirmTab
from ml.filter_bar import FilterBar
from ml.label_filters import extract_filter_meta, frame_matches
from ml.worker_lifetime import join_worker, stop_worker
from services.logger import app_logger

TAB_LABELING, TAB_BATCH, TAB_REVIEW = 0, 1, 2
IMAGE_PX = 768
NO_KEY_HINT = (
    "OPENROUTER_API_KEY is not set in this app's environment. Launch the tool "
    "from a shell that has it, e.g.\n"
    "  $env:OPENROUTER_API_KEY=\"sk-or-...\"; python ml/labeling_tool.py\n"
    "or set it persistently with setx and restart.")


class LabelingTool(QMainWindow):
    """Main labeling tool window."""

    def __init__(self, data_dir: Path):
        super().__init__()
        self.data_dir = data_dir
        self.samples = find_sample_sets(data_dir)
        self.meta_cache = self._build_meta_cache()
        self.current_index = 0
        self.current_cal = {}
        self._cal_error = ""   # why the current frame's JSON could not be read, if it couldn't
        self._ai_worker = None
        self._ai_all_worker = None

        self.roof_classifier, self.sky_classifier = load_classifiers()

        self.setWindowTitle(f"ML Labeling Tool - {data_dir}")
        self.setMinimumSize(1400, 900)

        self.setup_ui()
        self.setup_shortcuts()

        if self.samples:
            self.labels_widget.set_jump_dates(self._available_dates())
            self._refresh_counts()
            self.load_sample(0)
        else:
            QMessageBox.warning(self, "No Data", f"No calibration files found in:\n{data_dir}")

    def _build_meta_cache(self) -> dict:
        """ts -> compact filter metadata. The ONLY full pass over the JSONs; every
        count, filter and navigation step afterwards is answered from here."""
        cache = {}
        for sample in self.samples:
            try:
                cache[sample['timestamp']] = extract_filter_meta(load_calibration(sample['calibration']))
            except (OSError, ValueError) as e:
                app_logger.warning(f"Unreadable calibration {sample['calibration']}: {e}")
        return cache

    def _refresh_meta(self, timestamp: str):
        sample = next((s for s in self.samples if s['timestamp'] == timestamp), None)
        if sample is None:
            return
        try:
            self.meta_cache[timestamp] = extract_filter_meta(load_calibration(sample['calibration']))
        except (OSError, ValueError):
            pass

    def _refresh_counts(self):
        metas = self.meta_cache.values()
        labeled = sum(1 for m in metas if m['has_label'])
        self.labels_widget.update_unlabeled_count(labeled, len(self.samples))
        self.filter_bar.set_count(self._matching_count(), len(self.samples))
        if self._ai_all_worker is None:
            self.context_panel.set_ai_all_state(len(self._ai_pending_jobs()))

    # ── UI ────────────────────────────────────────────────────────────────────

    def setup_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)

        self.tabs = QTabWidget()
        self.tabs.setStyleSheet("""
            QTabWidget::pane { border: 1px solid #444; }
            QTabBar::tab {
                background: #2a2a2a; padding: 10px 20px;
                border: 1px solid #444; border-bottom: none;
                margin-right: 2px;
            }
            QTabBar::tab:selected { background: #333; border-bottom: 1px solid #333; }
            QTabBar::tab:hover { background: #3a3a3a; }
        """)
        main_layout.addWidget(self.tabs)

        labeling_widget = QWidget()
        labeling_outer = QVBoxLayout(labeling_widget)

        self.filter_bar = FilterBar()
        self.filter_bar.changed.connect(self._on_view_changed)
        labeling_outer.addWidget(self.filter_bar)

        columns = QHBoxLayout()
        self.setup_labeling_ui(columns)
        labeling_outer.addLayout(columns)
        self.tabs.addTab(labeling_widget, "📝 Labeling")

        self.batch_tab = BatchConfirmTab(self.samples, self.roof_classifier, self.sky_classifier)
        self.batch_tab.labels_saved.connect(self._on_batch_saved)
        self.tabs.addTab(self.batch_tab, "⚡ Batch Confirm")

        self.review_tab = ReviewTab(self.samples)
        self.review_tab.navigate_to_sample.connect(self.go_to_sample_from_review)
        self.tabs.addTab(self.review_tab, "🔍 Review Predictions")

        self.tabs.currentChanged.connect(self.on_tab_changed)

    def setup_labeling_ui(self, main_layout):
        images_widget = QWidget()
        images_layout = QVBoxLayout(images_widget)

        self.mismatch_banner = QLabel("")
        self.mismatch_banner.setAlignment(Qt.AlignCenter)
        self.mismatch_banner.setWordWrap(True)
        self.mismatch_banner.setVisible(False)
        self.mismatch_banner.setStyleSheet(
            "background: #7f1d1d; color: white; font-weight: bold; "
            "font-size: 14px; padding: 8px; border-radius: 5px;")
        images_layout.addWidget(self.mismatch_banner)

        self.image_view = TaggedImageView()
        images_layout.addWidget(self.image_view, 1)

        main_layout.addWidget(images_widget, 1)

        self.context_panel = ContextPanel()
        self.context_panel.ai_requested.connect(self.request_ai_suggestion)
        self.context_panel.ai_all_requested.connect(self.request_ai_for_unlabeled)
        main_layout.addWidget(self.context_panel, 1)

        self.labels_widget = LabelsWidget()
        main_layout.addWidget(self.labels_widget, 1)

        self.labels_widget.prev_requested.connect(self.prev_sample)
        self.labels_widget.next_requested.connect(self.next_sample)
        self.labels_widget.first_requested.connect(self.go_first)
        self.labels_widget.last_requested.connect(self.go_last)
        self.labels_widget.jump_date_requested.connect(self.jump_to_date)
        self.labels_widget.save_requested.connect(self._do_save)
        self.labels_widget.save_next_requested.connect(self._do_save_next)
        self.labels_widget.remove_requested.connect(self._do_remove)
        self.labels_widget.skip_changed.connect(self._on_view_changed)
        self.labels_widget.labels_changed.connect(self._update_image_tag)

    def _update_image_tag(self):
        if not self.samples:
            self.image_view.set_tag(None, "", saved=False)
            return
        roof_open, sky = self.labels_widget.current_tag()
        saved = (bool((self.current_cal.get('labels') or {}).get('labeled_at'))
                 and not self.labels_widget.unsaved_changes)
        self.image_view.set_tag(roof_open, sky, saved)

    def on_tab_changed(self, index: int):
        if index == TAB_REVIEW:
            self.review_tab.refresh_if_needed()
        elif index == TAB_BATCH:
            self.batch_tab.refresh_if_needed()
        elif index == TAB_LABELING and self.samples and not self.labels_widget.unsaved_changes:
            self.load_sample(self.current_index)   # pick up labels written by Batch Confirm

    def go_to_sample_from_review(self, index: int):
        self.tabs.setCurrentIndex(TAB_LABELING)
        self.load_sample(index)

    def setup_shortcuts(self):
        lw = self.labels_widget
        labeling = {
            "A": self.prev_sample, "Left": self.prev_sample,
            "D": self.next_sample, "Right": self.next_sample,
            "S": self._do_save, "Space": self._do_save_next,
            "Home": self.go_first, "End": self.go_last, "Delete": self._do_remove,
            "R": lw.toggle_roof, "C": lw.toggle_clouds, "T": lw.toggle_stars, "M": lw.toggle_moon,
            "1": lambda: lw.set_sky("Clear"),
            "2": lambda: lw.set_sky("Partly Cloudy"),
            "3": lambda: lw.set_sky("Overcast"),
        }
        batch = {"Return": self.batch_tab.confirm_page, "Enter": self.batch_tab.confirm_page,
                 "N": self.batch_tab.skip_page}
        # Window-wide so they work wherever focus sits, but gated on the visible
        # tab: Space must never save a frame the user cannot see.
        for tab, keys in ((TAB_LABELING, labeling), (TAB_BATCH, batch)):
            for key, fn in keys.items():
                QShortcut(QKeySequence(key), self,
                          lambda fn=fn, tab=tab: fn() if self.tabs.currentIndex() == tab else None)

    # ── AI pre-labelling ──────────────────────────────────────────────────────

    def _job(self, sample: dict) -> dict:
        return {'cal_path': str(sample['calibration']), 'lum_path': str(sample['lum']),
                'timestamp': sample['timestamp']}

    def _ai_pending_jobs(self) -> list:
        """Unlabeled frames with a lum image and no AI suggestion yet."""
        jobs = []
        for sample in self.samples:
            meta = self.meta_cache.get(sample['timestamp'])
            if meta and 'lum' in sample and not meta['has_label'] and not meta['ai_present']:
                jobs.append(self._job(sample))
        return jobs

    def request_ai_suggestion(self):
        """Run the AI labeler on the currently displayed frame (background thread)."""
        sample = self.samples[self.current_index]
        if 'lum' not in sample:
            QMessageBox.information(self, "AI Suggest", "This sample has no lum frame to send.")
            return
        self.context_panel.set_ai_busy(True)
        join_worker(self._ai_worker)   # the previous request's thread, before replacing it
        self._ai_worker = AiLabelWorker([self._job(sample)])
        # frame_done names the frame that was sent; by the time it completes the
        # user may be looking at a different one.
        self._ai_worker.frame_done.connect(self._refresh_meta)
        self._ai_worker.completed.connect(self._on_ai_suggestion_done)
        self._ai_worker.start()

    def _on_ai_suggestion_done(self, labelled: int, failed: int, error: str):
        self.context_panel.set_ai_busy(False)
        if labelled:
            self._after_ai_changed()
        if failed:
            detail = error or "Unknown error"
            hint = f"\n\n{NO_KEY_HINT}" if "OPENROUTER_API_KEY" in detail else ""
            QMessageBox.warning(self, "AI Suggest", f"AI request failed:\n\n{detail}{hint}")

    def request_ai_for_unlabeled(self):
        jobs = self._ai_pending_jobs()
        if not jobs:
            return
        if not os.getenv("OPENROUTER_API_KEY"):
            QMessageBox.warning(self, "AI pre-label", NO_KEY_HINT)
            return
        reply = QMessageBox.question(
            self, "AI pre-label",
            f"Send {len(jobs)} unlabeled frame(s) to OpenRouter (~${len(jobs) * 0.0008:.2f})?\n\n"
            f"Runs in the background, image-only (no hints). You can keep labeling.",
            QMessageBox.Yes | QMessageBox.No)
        if reply != QMessageBox.Yes:
            return
        self.context_panel.set_ai_all_state(0, f"AI 0/{len(jobs)}")
        self._ai_all_worker = AiLabelWorker(jobs)
        self._ai_all_worker.frame_done.connect(self._refresh_meta)
        self._ai_all_worker.progress.connect(
            lambda done, total, _msg: self.context_panel.set_ai_all_state(0, f"AI {done}/{total}"))
        self._ai_all_worker.completed.connect(self._on_ai_all_done)
        self._ai_all_worker.start()

    def _on_ai_all_done(self, labelled: int, failed: int, error: str):
        # `completed` is emitted from inside run(); the thread may not have returned yet.
        join_worker(self._ai_all_worker)
        self._ai_all_worker = None
        self._after_ai_changed()
        msg = f"AI pre-labelled {labelled} frame(s); {failed} failed."
        if failed and error:
            msg += f"\n\nLast error: {error}"
        QMessageBox.information(self, "AI pre-label", msg)

    def _after_ai_changed(self):
        self.review_tab.mark_dirty()
        self.batch_tab.mark_stale()
        self._refresh_counts()
        if not self.labels_widget.unsaved_changes:
            self.load_sample(self.current_index)
        else:
            # Keep the user's in-progress edits; just show the new AI verdict beside them.
            self.current_cal['ai_suggestion'] = load_calibration(
                self.samples[self.current_index]['calibration']).get('ai_suggestion')
            self.context_panel.populate(self.current_cal)
            self._update_mismatch_banner(self.current_cal)

    # ── Filtering / stepping ──────────────────────────────────────────────────

    def _filter_active(self) -> bool:
        return self.filter_bar.criteria().is_active()

    def _stepping_active(self) -> bool:
        """True when navigation should skip non-matching frames."""
        return self._filter_active() or self.labels_widget.skip_labeled_checked

    def _matches(self, sample: dict) -> bool:
        """Frame passes the filter bar AND the skip-labeled toggle."""
        meta = self.meta_cache.get(sample.get('timestamp'))
        if meta is None:
            return False
        if not frame_matches(meta, self.filter_bar.criteria()):
            return False
        if self.labels_widget.skip_labeled_checked and meta['has_label']:
            return False
        return True

    def find_next_matching(self, start: int, direction: int = 1) -> int:
        index = start + direction
        while 0 <= index < len(self.samples):
            if self._matches(self.samples[index]):
                return index
            index += direction
        return -1

    def find_first_match(self) -> int:
        return self.find_next_matching(-1, 1)

    def find_last_match(self) -> int:
        return self.find_next_matching(len(self.samples), -1)

    def go_first(self):
        if not self.samples:
            return
        target = self.find_first_match() if self._stepping_active() else 0
        if target >= 0:
            self.load_sample(target)

    def go_last(self):
        if not self.samples:
            return
        target = self.find_last_match() if self._stepping_active() else len(self.samples) - 1
        if target >= 0:
            self.load_sample(target)

    def _available_dates(self) -> list:
        """Unique capture dates (YYYYMMDD) as (display, raw) pairs, in order."""
        seen = set()
        dates = []
        for sample in self.samples:
            raw = sample.get('timestamp', '')[:8]
            if len(raw) == 8 and raw.isdigit() and raw not in seen:
                seen.add(raw)
                dates.append((f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}", raw))
        return dates

    def jump_to_date(self, date_str: str):
        """Jump to the first frame captured on/after the chosen night (absolute)."""
        for i, sample in enumerate(self.samples):
            if sample.get('timestamp', '')[:8] >= date_str:
                self.load_sample(i)
                return
        if self.samples:
            self.load_sample(len(self.samples) - 1)

    def _matching_count(self) -> int:
        return sum(1 for s in self.samples if self._matches(s))

    def _on_view_changed(self):
        """Filter or skip-labeled toggled: recount and jump to a match if needed."""
        self._refresh_counts()
        if not self.samples:
            return
        if self._stepping_active() and not self._matches(self.samples[self.current_index]):
            nxt = self.find_next_matching(self.current_index, 1)
            target = nxt if nxt >= 0 else self.find_first_match()
            if target >= 0:
                self.load_sample(target)
                return
        self.labels_widget.set_nav_enabled(*self._nav_enabled(self.current_index))

    def _nav_enabled(self, index: int) -> tuple:
        if self._stepping_active():
            return (self.find_next_matching(index, -1) >= 0,
                    self.find_next_matching(index, 1) >= 0)
        return index > 0, index < len(self.samples) - 1

    # ── Loading / saving ──────────────────────────────────────────────────────

    def load_sample(self, index: int):
        if not self.samples:
            return

        if self.labels_widget.unsaved_changes:
            reply = QMessageBox.question(
                self, "Unsaved Changes",
                "Save changes before moving to next sample?",
                QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel
            )
            if reply == QMessageBox.Yes:
                self._do_save()
            elif reply == QMessageBox.Cancel:
                return

        index = max(0, min(index, len(self.samples) - 1))
        self.current_index = index
        sample = self.samples[index]

        prev_enabled, next_enabled = self._nav_enabled(index)
        self.labels_widget.set_navigation(index, len(self.samples), prev_enabled, next_enabled,
                                          sample['folder'].name, sample['timestamp'])

        if 'lum' in sample:
            self.image_view.set_pixmap(load_fits_as_qpixmap(sample['lum'], IMAGE_PX))
        else:
            self.image_view.set_pixmap(create_placeholder_pixmap("No lum FITS", IMAGE_PX))

        try:
            self.current_cal = load_calibration(sample['calibration'])
            self._cal_error = ""
        except (OSError, ValueError) as e:
            app_logger.warning(f"Could not read {sample['calibration']}: {e}")
            self.current_cal = {}
            self._cal_error = str(e)

        self.context_panel.populate(self.current_cal)
        pred = predict_frame(self.roof_classifier, self.sky_classifier, sample, self.current_cal)
        self.context_panel.set_model_text(describe_prediction(
            self.roof_classifier, self.sky_classifier, sample, self.current_cal, pred))
        self.labels_widget.populate_fields(self.current_cal, pred['roof'], pred['sky'])

        self._update_mismatch_banner(self.current_cal)
        self.labels_widget.mark_saved()

    def _update_mismatch_banner(self, cal: dict):
        """Flag frames where the AI roof call disagrees with the NINA roof state."""
        if self._cal_error:
            self.mismatch_banner.setText(
                f"⚠️ This frame's calibration JSON could not be read, so it cannot be labeled "
                f"— move on with → or remove it with Del.  ({self._cal_error})")
            self.mismatch_banner.setVisible(True)
            return

        ai = cal.get('ai_suggestion')
        rs = cal.get('roof_state', {})
        if not ai or not rs.get('available') or rs.get('roof_open') is None:
            self.mismatch_banner.setVisible(False)
            return

        ai_open = bool(ai.get('roof_open'))
        nina_open = to_bool(rs.get('roof_open'))
        if ai_open == nina_open:
            self.mismatch_banner.setVisible(False)
            return

        self.mismatch_banner.setText(
            f"⚠️ MISMATCH — AI: roof {'OPEN' if ai_open else 'CLOSED'}  vs  "
            f"NINA: roof {'OPEN' if nina_open else 'CLOSED'}  ·  worth reviewing")
        self.mismatch_banner.setVisible(True)

    def _do_save(self) -> bool:
        if not self.samples:
            return False
        sample = self.samples[self.current_index]
        if self._cal_error:
            QMessageBox.warning(
                self, "Cannot label this frame",
                f"This frame's calibration JSON could not be read:\n{sample['calibration']}\n\n"
                f"{self._cal_error}\n\nNothing was saved. Move on with → or remove the frame with Del.")
            return False
        if not self.labels_widget.save_labels(self.current_cal, sample['calibration']):
            QMessageBox.warning(self, "Save failed",
                                f"Could not write:\n{sample['calibration']}\n\nThe label was NOT saved.")
            return False
        self.meta_cache[sample['timestamp']] = extract_filter_meta(self.current_cal)
        self._refresh_counts()
        self.review_tab.mark_dirty()
        self.batch_tab.forget([sample['timestamp']])
        return True

    def _do_save_next(self):
        if self._do_save():
            self.next_sample()

    def _on_batch_saved(self, timestamps: list):
        for ts in timestamps:
            self._refresh_meta(ts)
        self._refresh_counts()
        self.review_tab.mark_dirty()

    def _do_remove(self):
        """Move the current sample's files to the _removed folder and advance."""
        if not self.samples:
            return
        sample = self.samples[self.current_index]
        ts = sample.get('timestamp', '?')
        trash_dir = self.data_dir / "_removed"
        reply = QMessageBox.question(
            self, "Remove image",
            f"Move sample {ts} out of the dataset?\n\n"
            f"Its calibration JSON and lum FITS are moved to:\n{trash_dir}\n"
            f"(recoverable — move them back to undo).",
            QMessageBox.Yes | QMessageBox.No
        )
        if reply != QMessageBox.Yes:
            return

        try:
            moved = remove_sample_files(sample, trash_dir)
        except Exception as e:
            QMessageBox.warning(self, "Remove failed", f"Could not move files:\n{e}")
            return

        app_logger.info(f"Removed sample {ts}: moved {len(moved)} file(s) to {trash_dir}")
        self.meta_cache.pop(ts, None)
        del self.samples[self.current_index]      # in-place so the other tabs' list stays in sync
        self.labels_widget.mark_saved()           # nothing pending for the removed frame
        self.review_tab.mark_dirty()
        self.batch_tab.forget([ts])

        if not self.samples:
            self.current_cal = {}
            self._update_image_tag()
            QMessageBox.information(self, "Removed", "No samples remain in the dataset.")
            return

        self.labels_widget.set_jump_dates(self._available_dates())
        self._refresh_counts()

        new_index = min(self.current_index, len(self.samples) - 1)
        if self._stepping_active() and not self._matches(self.samples[new_index]):
            nxt = self.find_next_matching(new_index, 1)
            if nxt < 0:
                nxt = self.find_first_match()
            if nxt >= 0:
                new_index = nxt
        self.load_sample(new_index)

    def prev_sample(self):
        if self._stepping_active():
            prev_idx = self.find_next_matching(self.current_index, -1)
            if prev_idx >= 0:
                self.load_sample(prev_idx)
        elif self.current_index > 0:
            self.load_sample(self.current_index - 1)

    def next_sample(self):
        if self._stepping_active():
            next_idx = self.find_next_matching(self.current_index, 1)
            if next_idx >= 0:
                self.load_sample(next_idx)
            elif self.labels_widget.skip_labeled_checked and not self._filter_active():
                QMessageBox.information(self, "Done", "All samples have been labeled!")
        elif self.current_index < len(self.samples) - 1:
            self.load_sample(self.current_index + 1)

    def closeEvent(self, event):
        if self.labels_widget.unsaved_changes:
            reply = QMessageBox.question(
                self, "Unsaved Changes",
                "Save changes before closing?",
                QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel
            )
            if reply == QMessageBox.Yes:
                self._do_save()
            elif reply == QMessageBox.Cancel:
                event.ignore()
                return
        for worker in (self._ai_worker, self._ai_all_worker):
            stop_worker(worker)
        self.batch_tab.shutdown()
        self.review_tab.shutdown()
        event.accept()


def main():
    parser = argparse.ArgumentParser(description="ML Labeling Tool for calibration data")
    parser.add_argument("data_dir", nargs="?", default=r"D:\Pier Camera ML Data",
                        help="Directory containing calibration files")
    args = parser.parse_args()

    data_dir = Path(args.data_dir)
    if not data_dir.exists():
        app_logger.error(f"Directory not found: {data_dir}")
        sys.exit(1)

    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    app.setStyleSheet("""
        QMainWindow, QWidget { background: #1e1e1e; color: #e0e0e0; }
        QGroupBox { border: 1px solid #444; border-radius: 5px; margin-top: 10px; padding-top: 10px; }
        QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 5px; }
        QPushButton { background: #333; border: 1px solid #555; padding: 8px 16px; border-radius: 4px; }
        QPushButton:hover { background: #444; }
        QPushButton:pressed { background: #555; }
        QSpinBox, QDoubleSpinBox, QLineEdit, QTextEdit {
            background: #2a2a2a; border: 1px solid #444; padding: 5px; border-radius: 3px;
        }
        QCheckBox { spacing: 8px; }
        QScrollArea { border: none; }
    """)

    window = LabelingTool(data_dir)
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
