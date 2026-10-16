"""
Calibration history dialog for the All-Sky page.

Layout only: the rows come in as calibration_history.HistoryEntry records
and a restore goes out as `restore_requested(entry_id)`; the main window
hands it to CalibrationHistoryController and calls `show_result` with what
happened. Guided solves are marked, and "Restore last guided" picks the
newest one — the model a user is most likely to want back (discussion #105).
"""
from datetime import datetime
from typing import List, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QHBoxLayout, QHeaderView, QTableWidgetItem,
    QVBoxLayout,
)
from qfluentwidgets import (
    BodyLabel, CaptionLabel, MessageBox, PrimaryPushButton, PushButton,
    TableWidget,
)

from ..theme.icons import mdi
from ..theme.tokens import Spacing

COLUMNS = ("Saved", "Source", "Quality", "RMS", "Stars")


def _local_time(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso).astimezone().strftime('%Y-%m-%d %H:%M')
    except (TypeError, ValueError):
        return iso or '—'


class CalibrationHistoryDialog(QDialog):
    """List of earlier calibrations with Restore."""

    restore_requested = Signal(str)

    def __init__(self, entries: List, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Calibration History")
        self.resize(680, 440)
        self._entries = list(entries)
        self.restored = False

        vl = QVBoxLayout(self)
        vl.setContentsMargins(Spacing.base, Spacing.base, Spacing.base, Spacing.base)
        vl.setSpacing(Spacing.sm)

        intro = CaptionLabel(
            "Every calibration that became the live one, newest first. "
            "Restoring makes it live again; the current one stays in this "
            "list, so a restore can always be undone.")
        intro.setWordWrap(True)
        vl.addWidget(intro)

        self._table = TableWidget(self)
        self._table.setColumnCount(len(COLUMNS))
        self._table.setHorizontalHeaderLabels(COLUMNS)
        self._table.verticalHeader().hide()
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SingleSelection)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self._table.itemSelectionChanged.connect(self._update_buttons)
        vl.addWidget(self._table, 1)
        self._fill()

        self._empty = BodyLabel("No calibrations have been kept yet. The "
                                "next calibration that is saved starts the history.")
        self._empty.setWordWrap(True)
        self._empty.setVisible(not self._entries)
        vl.addWidget(self._empty)

        self._result = CaptionLabel("")
        self._result.setWordWrap(True)
        self._result.hide()
        vl.addWidget(self._result)

        row = QHBoxLayout()
        self._guided_btn = PushButton("Restore last guided", icon=mdi('target'))
        self._guided_btn.clicked.connect(self._on_restore_guided)
        row.addWidget(self._guided_btn)
        row.addStretch()
        self._restore_btn = PrimaryPushButton("Restore selected", icon=mdi('history'))
        self._restore_btn.clicked.connect(self._on_restore_selected)
        row.addWidget(self._restore_btn)
        close_btn = PushButton("Close")
        close_btn.clicked.connect(self.reject)
        row.addWidget(close_btn)
        vl.addLayout(row)

        self._guided_btn.setEnabled(self._newest_guided() is not None)
        self._update_buttons()

    def _fill(self) -> None:
        from services.allsky.calibration_quality import CalibrationQuality
        self._table.setRowCount(len(self._entries))
        for r, e in enumerate(self._entries):
            source = e.label + (" ★" if e.is_guided else "")
            if e.source == 'restored' and e.restored_from:
                source += " (earlier entry)"
            level = e.quality or 'none'
            cells = (_local_time(e.saved_at), source,
                     level.capitalize() if level != 'none' else 'None',
                     f"{e.rms_residual:.2f} px", str(e.n_matches))
            for c, text in enumerate(cells):
                item = QTableWidgetItem(text)
                item.setData(Qt.UserRole, e.entry_id)
                if c == 2:
                    item.setForeground(QColor(CalibrationQuality.badge_colors(level)[1]))
                self._table.setItem(r, c, item)
        self._table.resizeColumnsToContents()

    def _newest_guided(self):
        return next((e for e in self._entries if e.is_guided), None)

    def selected_entry(self):
        rows = self._table.selectionModel().selectedRows() if self._table.selectionModel() else []
        if not rows:
            return None
        return self._entries[rows[0].row()]

    def _update_buttons(self) -> None:
        self._restore_btn.setEnabled(self.selected_entry() is not None)

    def _on_restore_selected(self) -> None:
        entry = self.selected_entry()
        if entry is not None:
            self._confirm_and_request(entry)

    def _on_restore_guided(self) -> None:
        entry = self._newest_guided()
        if entry is not None:
            self._confirm_and_request(entry)

    def _confirm_and_request(self, entry) -> None:
        if self.confirm(entry):
            self.restore_requested.emit(entry.entry_id)

    def confirm(self, entry) -> bool:
        box = MessageBox(
            "Restore this calibration?",
            f"{entry.label} from {_local_time(entry.saved_at)} — "
            f"{entry.n_matches} stars, RMS {entry.rms_residual:.2f} px.\n\n"
            "It replaces the current calibration, which stays in the history. "
            "Automatic refinement starts again from the restored model.",
            self.window(),
        )
        box.yesButton.setText("Restore")
        box.cancelButton.setText("Cancel")
        return bool(box.exec())

    def show_result(self, ok: bool, message: str) -> None:
        if ok:
            self.restored = True
            self.accept()
            return
        self._result.setText(message)
        self._result.setStyleSheet("color: #FF6B6B;")
        self._result.show()

    def entry_ids(self) -> List[str]:
        return [e.entry_id for e in self._entries]

    def newest_guided_id(self) -> Optional[str]:
        e = self._newest_guided()
        return e.entry_id if e else None
