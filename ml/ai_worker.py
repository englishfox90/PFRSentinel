#!/usr/bin/env python3
"""Background QThread that runs AI pre-labelling without freezing the GUI.

Used by the labeling tab (one frame, or every unlabeled frame) and the review
tab (the filtered set). Writes an `ai_suggestion` block into each calibration
JSON; never touches human labels. Emits progress so the caller can drive a bar.
"""
from concurrent.futures import ThreadPoolExecutor, FIRST_COMPLETED, wait as wait_for_futures
from datetime import datetime

from PySide6.QtCore import QThread, Signal

from services.logger import app_logger
from .ai_labeler import label_lum_frame, build_context_from_cal
from .calibration_store import load_calibration, update_calibration

DEFAULT_WORKERS = 6
CANCEL_POLL_S = 0.25


def store_ai_suggestion(cal_path, result: dict):
    """Merge an AI result into the file as it is at the moment of writing.

    The API call takes seconds, and the labeler may save this same frame
    meanwhile — writing back a copy read before the call would erase that label.
    The read and the write share the file's lock, so nothing can land between them.
    """
    def add_suggestion(cal):
        cal["ai_suggestion"] = result

    update_calibration(cal_path, add_suggestion)


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

        pool = ThreadPoolExecutor(max_workers=min(self.workers, max(1, total)))
        futures = {pool.submit(self._label_one, job): job for job in self.jobs}
        pending = set(futures)
        done_count = 0
        try:
            # Polled rather than as_completed(): a request can sit in a 60 s timeout
            # plus retries, and cancel() has to be noticed well before that.
            while pending and not self._cancel:
                finished, pending = wait_for_futures(pending, timeout=CANCEL_POLL_S, return_when=FIRST_COMPLETED)
                for fut in finished:
                    done_count += 1
                    ts = futures[fut].get("timestamp", "?")
                    try:
                        result = fut.result()
                        labelled += 1
                        self.frame_done.emit(ts)
                        roof = "OPEN" if result["roof_open"] else "CLOSED"
                        self.progress.emit(done_count, total, f"{ts}: roof {roof}")
                    except Exception as e:
                        failed += 1
                        last_error = str(e)
                        app_logger.warning(f"AI label failed for {ts}: {e}")
                        self.progress.emit(done_count, total, f"{ts}: ERROR {e}")
        finally:
            # On cancel: queued jobs are dropped, in-flight requests finish on the
            # pool's own threads and still store their result (locked, atomic).
            pool.shutdown(wait=not self._cancel, cancel_futures=True)

        self.completed.emit(labelled, failed, last_error)
