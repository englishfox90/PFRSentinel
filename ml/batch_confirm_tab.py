#!/usr/bin/env python3
"""Batch Confirm tab — label a page of look-alike frames with one keypress.

Unlabeled frames are grouped by their suggested tag (roof closed / open + sky).
A page of thumbnails is shown; click the ones that look wrong to leave them for
one-by-one labeling, then confirm the rest. Spotting the odd one out in a grid
of same-tagged frames is far faster than reading a form per frame.
"""
from datetime import datetime

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QPushButton,
    QComboBox, QCheckBox, QProgressBar, QFrame,
)
from PySide6.QtCore import Qt, Signal, QThread

from services.logger import app_logger
from .calibration_store import load_calibration, update_calibration
from .frame_prediction import predict_frame
from .label_suggestion import suggest_labels, labels_from_suggestion, describe_sources
from .labeling_io import load_fits_as_qpixmap
from .tagged_image_view import OPEN_COLOUR, CLOSED_COLOUR, tag_text
from .worker_lifetime import join_worker, stop_worker

COLUMNS, ROWS = 6, 4
PAGE_SIZE = COLUMNS * ROWS
TILE_PX = 190
GROUPS = ["Roof CLOSED", "Open · Clear", "Open · Partly Cloudy", "Open · Overcast", "Open · sky unknown"]


def group_of(suggestion: dict) -> str:
    if not suggestion['roof_open']:
        return GROUPS[0]
    return f"Open · {suggestion['sky_condition']}" if suggestion['sky_condition'] else GROUPS[4]


class SuggestionWorker(QThread):
    """Computes a suggestion for every unlabeled frame, off the GUI thread."""

    progress = Signal(int, int)
    ready = Signal(list)   # [{'sample': dict, 'suggestion': dict}]

    def __init__(self, samples: list, roof_clf, sky_clf, parent=None):
        super().__init__(parent)
        self.samples = samples
        self.roof_clf = roof_clf
        self.sky_clf = sky_clf
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def run(self):
        found = []
        total = len(self.samples)
        for i, sample in enumerate(self.samples, 1):
            if self._cancel:
                return
            try:
                cal = load_calibration(sample['calibration'])
                if not (cal.get('labels') or {}).get('labeled_at'):
                    pred = predict_frame(self.roof_clf, self.sky_clf, sample, cal)
                    found.append({'sample': sample,
                                  'suggestion': suggest_labels(cal, pred['roof'], pred['sky'])})
            except Exception as e:
                app_logger.warning(f"Batch confirm: skipped {sample.get('timestamp')}: {e}")
            if i % 25 == 0 or i == total:
                self.progress.emit(i, total)
        self.ready.emit(found)


class Tile(QFrame):
    """One thumbnail. Click toggles whether it is part of the confirm."""

    toggled = Signal()

    def __init__(self, entry: dict, parent=None):
        super().__init__(parent)
        self.entry = entry
        self.included = True
        sug = entry['suggestion']
        self._colour = OPEN_COLOUR if sug['roof_open'] else CLOSED_COLOUR

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(2)
        self.image = QLabel()
        self.image.setAlignment(Qt.AlignCenter)
        self.image.setPixmap(load_fits_as_qpixmap(entry['sample']['lum'], TILE_PX))
        layout.addWidget(self.image)

        extras = ("★" if sug['stars_visible'] else "") + ("☾" if sug['moon_visible'] else "")
        ts = entry['sample']['timestamp']
        self.caption = QLabel()
        self.caption.setAlignment(Qt.AlignCenter)
        self._caption_text = f"{ts[4:6]}-{ts[6:8]} {ts[9:11]}:{ts[11:13]}  {extras}"
        layout.addWidget(self.caption)

        self.setToolTip(f"{ts}\n{tag_text(sug['roof_open'], sug['sky_condition'])}\n"
                        f"{describe_sources(sug)}\n\nClick to leave this frame out")
        self.setCursor(Qt.PointingHandCursor)
        self._restyle()

    def mousePressEvent(self, event):
        self.included = not self.included
        self._restyle()
        self.toggled.emit()

    def _restyle(self):
        if self.included:
            self.setStyleSheet(f"Tile {{ border: 4px solid {self._colour}; border-radius: 4px; }}")
            self.caption.setText(self._caption_text)
            self.caption.setStyleSheet("color: #ccc; border: none;")
        else:
            self.setStyleSheet("Tile { border: 4px dashed #666; border-radius: 4px; background: #111; }")
            self.caption.setText("LEFT OUT")
            self.caption.setStyleSheet("color: #f59e0b; font-weight: bold; border: none;")
        self.image.setEnabled(self.included)


class BatchConfirmTab(QWidget):
    labels_saved = Signal(list)   # timestamps whose JSON now carries a label

    def __init__(self, samples: list, roof_clf, sky_clf, parent=None):
        super().__init__(parent)
        self.samples = samples
        self.roof_clf = roof_clf
        self.sky_clf = sky_clf
        self.entries = []
        self.tiles = []
        self._stale = True
        self._worker = None
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        top = QHBoxLayout()
        top.addWidget(QLabel("Group:"))
        self.group_combo = QComboBox()
        self.group_combo.setMinimumWidth(260)
        self.group_combo.currentIndexChanged.connect(self._show_page)
        top.addWidget(self.group_combo)

        self.agreed_only = QCheckBox("Only frames where the roof sources agree")
        self.agreed_only.setChecked(True)
        self.agreed_only.setToolTip(
            "NINA + AI + ML roof calls, at least two present and none dissenting.\n"
            "Against existing labels that agreement was right 99.9 % of the time.")
        self.agreed_only.stateChanged.connect(self._rebuild_groups)
        top.addWidget(self.agreed_only)

        self.rescan_btn = QPushButton("🔄 Rescan")
        self.rescan_btn.clicked.connect(self.rescan)
        top.addWidget(self.rescan_btn)
        top.addStretch()

        self.progress = QProgressBar()
        self.progress.setMaximumWidth(220)
        self.progress.setVisible(False)
        top.addWidget(self.progress)
        layout.addLayout(top)

        self.banner = QLabel("")
        self.banner.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.banner)

        self.grid_host = QWidget()
        self.grid = QGridLayout(self.grid_host)
        self.grid.setSpacing(6)
        layout.addWidget(self.grid_host, 1)

        bottom = QHBoxLayout()
        self.hint = QLabel("Click a frame that does NOT match the tag to leave it out, then confirm the rest.")
        self.hint.setStyleSheet("color: #888;")
        bottom.addWidget(self.hint)
        bottom.addStretch()
        self.skip_btn = QPushButton("Skip page (N)")
        self.skip_btn.setToolTip("Show the next page without labeling anything on this one")
        self.skip_btn.clicked.connect(self.skip_page)
        bottom.addWidget(self.skip_btn)
        self.confirm_btn = QPushButton("Confirm (Enter)")
        self.confirm_btn.setStyleSheet("background: #10b981; color: white; font-weight: bold; padding: 10px 24px;")
        self.confirm_btn.clicked.connect(self.confirm_page)
        bottom.addWidget(self.confirm_btn)
        layout.addLayout(bottom)

        # Enter / N are bound by the main window, which knows which tab is showing.
        self._skipped = set()

    # ── Data ──────────────────────────────────────────────────────────────────

    def mark_stale(self):
        self._stale = True

    def forget(self, timestamps):
        """Frames labeled or removed elsewhere — drop them without a full rescan."""
        gone = set(timestamps)
        if any(e['sample']['timestamp'] in gone for e in self.entries):
            self.entries = [e for e in self.entries if e['sample']['timestamp'] not in gone]
            self._rebuild_groups()

    def shutdown(self):
        stop_worker(self._worker)
        self._worker = None

    def refresh_if_needed(self):
        if self._stale and self._worker is None:
            self.rescan()

    def rescan(self):
        if self._worker is not None:
            return
        self._skipped.clear()
        self.rescan_btn.setEnabled(False)
        self.progress.setVisible(True)
        self.progress.setValue(0)
        self.banner.setText("Working out suggestions for the unlabeled frames…")
        self._set_banner_colour("#444")
        candidates = [s for s in self.samples if 'lum' in s and 'calibration' in s]
        self._worker = SuggestionWorker(candidates, self.roof_clf, self.sky_clf)
        self._worker.progress.connect(lambda done, total: self.progress.setValue(int(done / total * 100) if total else 0))
        self._worker.ready.connect(self._on_ready)
        self._worker.start()

    def _on_ready(self, entries: list):
        # `ready` is emitted from inside run(); the thread may not have returned yet.
        join_worker(self._worker)
        self._worker = None
        self._stale = False
        self.entries = entries
        self.rescan_btn.setEnabled(True)
        self.progress.setVisible(False)
        self._rebuild_groups()

    def _pool(self, group: str) -> list:
        return [e for e in self.entries
                if group_of(e['suggestion']) == group
                and e['sample']['timestamp'] not in self._skipped
                and (e['suggestion']['agreed'] or not self.agreed_only.isChecked())]

    def _rebuild_groups(self):
        current = self.group_combo.currentData()
        self.group_combo.blockSignals(True)
        self.group_combo.clear()
        for group in GROUPS:
            n = len(self._pool(group))
            if n:
                self.group_combo.addItem(f"{group}  ({n})", group)
        idx = self.group_combo.findData(current)
        self.group_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.group_combo.blockSignals(False)
        self._show_page()

    # ── Page ──────────────────────────────────────────────────────────────────

    def _set_banner_colour(self, colour: str):
        self.banner.setStyleSheet(
            f"background: {colour}; color: white; font-weight: bold; font-size: 16px; "
            f"padding: 8px; border-radius: 4px;")

    def _show_page(self):
        for tile in self.tiles:
            tile.setParent(None)
            tile.deleteLater()
        self.tiles = []

        group = self.group_combo.currentData()
        page = self._pool(group)[:PAGE_SIZE] if group else []
        if not page:
            left = len(self.entries)
            self.banner.setText(
                "Nothing left to batch-confirm." if not left else
                f"No frames in this view — {left} unlabeled frame(s) remain "
                f"(untick “sources agree”, run AI pre-label, or label them one by one).")
            self._set_banner_colour("#444")
            self.confirm_btn.setEnabled(False)
            self.skip_btn.setEnabled(False)
            return

        sug = page[0]['suggestion']
        self.banner.setText(f"Every frame below will be labeled:   {group.upper()}")
        self._set_banner_colour(OPEN_COLOUR if sug['roof_open'] else CLOSED_COLOUR)
        for i, entry in enumerate(page):
            tile = Tile(entry)
            tile.toggled.connect(self._update_confirm_text)
            self.grid.addWidget(tile, i // COLUMNS, i % COLUMNS)
            self.tiles.append(tile)
        self.skip_btn.setEnabled(True)
        self._update_confirm_text()

    def _update_confirm_text(self):
        n = sum(1 for t in self.tiles if t.included)
        self.confirm_btn.setText(f"Confirm {n} frame(s)  (Enter)")
        self.confirm_btn.setEnabled(n > 0)

    def skip_page(self):
        self._skipped.update(t.entry['sample']['timestamp'] for t in self.tiles)
        self._rebuild_groups()

    def confirm_page(self):
        if not self.confirm_btn.isEnabled():
            return
        now = datetime.now().isoformat()
        saved, already_labeled = [], []
        for tile in self.tiles:
            ts = tile.entry['sample']['timestamp']
            if not tile.included:
                self._skipped.add(ts)
                continue
            new_labels = labels_from_suggestion(tile.entry['suggestion'], now, 'batch_confirm')

            def label_if_unlabeled(cal, new_labels=new_labels):
                if (cal.get('labels') or {}).get('labeled_at'):
                    return False   # labeled elsewhere since the scan; never overwrite a human label
                cal['labels'] = new_labels

            try:
                if update_calibration(tile.entry['sample']['calibration'], label_if_unlabeled) is not None:
                    saved.append(ts)
                else:
                    already_labeled.append(ts)
            except (OSError, ValueError) as e:
                app_logger.warning(f"Batch confirm: could not save {ts}: {e}")
                self._skipped.add(ts)

        done = set(saved) | set(already_labeled)
        self.entries = [e for e in self.entries if e['sample']['timestamp'] not in done]
        app_logger.info(f"Batch confirm: labeled {len(saved)} frame(s)")
        self.labels_saved.emit(saved)
        self._rebuild_groups()
