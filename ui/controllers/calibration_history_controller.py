"""
Restoring an earlier calibration from the history (services/allsky/
calibration_history.py).

The panel lists the entries and asks for one by id; this does the rest:
loads the stored model, writes it to the calibration file through the same
backed-up save the background service uses (so the model it replaces is
itself kept in the history), and hands it to AllSkyController.adopt_model,
which seeds the background service with it exactly as a fresh Calibrate Now
or guided result does — the chance streak, the escape state and the label
memory start over.

No pole check: a restore is the user choosing a model they have seen work,
and a guided solve, the case this exists for, outranks the measured pole
anyway (.claude/rules/allsky.md).
"""
from datetime import datetime
from typing import List, Optional, Tuple

from PySide6.QtCore import QObject, Signal

from services.allsky import calibration_history
from services.allsky.calibration_history import HistoryEntry
from services.logger import app_logger as log


def describe_saved_at(entry: HistoryEntry) -> str:
    """Local date and time the entry became live, for status lines."""
    try:
        return datetime.fromisoformat(entry.saved_at).astimezone().strftime('%Y-%m-%d %H:%M')
    except (TypeError, ValueError):
        return entry.saved_at or 'an unknown time'


class CalibrationHistoryController(QObject):
    """List and restore history entries for the All-Sky page."""

    restore_finished = Signal(bool, str)   # ok, message for the user

    def __init__(self, allsky_controller, parent=None):
        super().__init__(parent)
        self._allsky = allsky_controller

    def entries(self) -> List[HistoryEntry]:
        return calibration_history.list_entries()

    def newest_guided(self) -> Optional[HistoryEntry]:
        return calibration_history.newest_guided(self.entries())

    def restore(self, entry_id: str) -> Tuple[bool, str]:
        """Make the entry the live calibration; (ok, message for the user),
        also announced on `restore_finished`."""
        ok, msg = self._restore(entry_id)
        self.restore_finished.emit(ok, msg)
        return ok, msg

    def _restore(self, entry_id: str) -> Tuple[bool, str]:
        from services.allsky.calibration_store import keep_guided_copy, save_with_backup
        from services.app_config import get_calibration_path

        entry = next((e for e in self.entries() if e.entry_id == entry_id), None)
        model = calibration_history.load_model(entry_id)
        if entry is None or model is None:
            log.warning(f"Calibration restore: entry {entry_id!r} is missing or unreadable")
            return False, "That calibration could not be read from the history."

        error = save_with_backup(
            model, stamp_time=False, backup=True,
            source=calibration_history.SOURCE_RESTORED, restored_from=entry_id)
        if error:
            return False, f"Restore failed: {error}"
        cal_path = get_calibration_path()
        keep_guided_copy(model, cal_path)
        self._allsky.remember_calibration_file(cal_path)

        when = describe_saved_at(entry)
        msg = (f"Restored the calibration from {when} ({entry.label}): "
               f"{model.n_matches} stars, RMS={model.rms_residual:.2f}px")
        log.info(f"Calibration restored from history entry {entry_id}: {model}")
        self._allsky.adopt_model(model, msg)
        return True, msg
