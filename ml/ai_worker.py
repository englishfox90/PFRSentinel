#!/usr/bin/env python3
"""Background QThread that runs AI pre-labelling without freezing the GUI.

Used by the labeling tab (one frame, or every unlabeled frame) and the review
tab (the filtered set). Writes an `ai_suggestion` block into each calibration
JSON; never touches human labels. Emits progress so the caller can drive a bar.
"""
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

from PySide6.QtCore import QThread, Signal

from services.logger import app_logger
from ml.ai_labeler import label_lum_frame, build_context_from_cal
from ml.calibration_store import load_calibration, save_calibration

DEFAULT_WORKERS = 6


def store_ai_suggestion(cal_path, result: dict):
    """Merge an AI result into the file as it is *now*.

    The API call takes seconds, and the labeler may save this same frame
    meanwhile — writing back a copy read before the call would erase that label.
    """
    cal = load_calibration(cal_path)
    cal["ai_suggestion"] = result
    save_calibration(cal_path, cal)


class AiLabelWorker(QThread):
    """Runs label_lum_frame over a list of jobs, several requests at a time.

    Each job is a dict: {'cal_path': str, 'lum_path': str, 'timestamp': str}.
    """

    progress = Signal(int, int, str)   # done_count, total, message
    frame_done = Signal(str)           # timestamp of a frame whose JSON just changed
    completed = Signal(int, int, str)  # labelled, failed, last_error ("" if none)

    def __init__(self, jobs: list, use_hints: bool = False, model: str | None = None,
                 workers: int = DEFAULT_WORKERS, parent=None):
        super().__init__(parent)
        self.jobs = jobs
        self.use_hints = use_hints
        self.model = model
        self.workers = max(1, workers)
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def _label_one(self, job: dict) -> dict:
        if self._cancel:
            raise RuntimeError("cancelled")
        context = build_context_from_cal(load_calibration(job["cal_path"])) if self.use_hints else None
        result = label_lum_frame(job["lum_path"], context, model=self.model)
        result["suggested_at"] = datetime.now().isoformat()
        result["hints_used"] = bool(self.use_hints)
        store_ai_suggestion(job["cal_path"], result)
        return result

    def run(self):
        total = len(self.jobs)
        labelled = failed = 0
        last_error = ""

        with ThreadPoolExecutor(max_workers=min(self.workers, max(1, total))) as pool:
            futures = {pool.submit(self._label_one, job): job for job in self.jobs}
            for done, fut in enumerate(as_completed(futures), 1):
                ts = futures[fut].get("timestamp", "?")
                try:
                    result = fut.result()
                    labelled += 1
                    self.frame_done.emit(ts)
                    roof = "OPEN" if result["roof_open"] else "CLOSED"
                    self.progress.emit(done, total, f"{ts}: roof {roof}")
                except Exception as e:
                    if self._cancel:
                        continue
                    failed += 1
                    last_error = str(e)
                    app_logger.warning(f"AI label failed for {ts}: {e}")
                    self.progress.emit(done, total, f"{ts}: ERROR {e}")

        self.completed.emit(labelled, failed, last_error)
