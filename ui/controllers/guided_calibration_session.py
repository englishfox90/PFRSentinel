"""
One guided-calibration dialog session.

The dialog used to close on Solve and hand its anchors to a fire-and-forget
worker (issue #79): the window vanished while the solve ran, a failure that
named the suspect star arrived after the identified stars were already gone,
and success was one line in a status label on a page the user wasn't looking
at. This object keeps the session alive instead — the dialog stays open, the
solve and the star suggestions run here off the GUI thread, and a successful
result is held UNSAVED until the user has seen it drawn over their frame and
accepts it.

Owned by AllSkyController, which does the saving (commit_guided_calibration).
"""
import functools
from typing import Callable, List, Optional, Tuple

from PySide6.QtCore import QObject, QThread, Signal

from services.logger import app_logger as log

# A failed solve marks the anchors standing clear of the rest. The worst is
# always marked; others only when they are also past the RMS limit and this
# many times the median — a wrong-basin failure leaves every residual large,
# and painting all of them red would point at nothing.
_SUSPECT_MEDIAN_FACTOR = 2.5

# Stars drawn in the review step (candidates arrive brightest first). Enough
# to cover the whole sky, few enough that each label can be read.
_MAX_PREDICTED = 40


class _SolveWorker(QThread):
    solved = Signal(object)          # FisheyeModel
    failed = Signal(str, object, object)   # message, residual list|None, limit

    def __init__(self, anchors, prep: dict):
        super().__init__()
        self._anchors = anchors
        self._prep = prep

    def run(self):
        try:
            from services.allsky.guided_calibration import calibrate_from_anchors
            p = self._prep
            model = calibrate_from_anchors(
                self._anchors, p['lat'], p['lon'], p['dt'],
                p['sky_cx'], p['sky_cy'], p['sky_r'],
                image_width=p.get('image_width', 0),
                image_height=p.get('image_height', 0))
            self.solved.emit(model)
        except Exception as e:
            self.failed.emit(str(e), getattr(e, 'anchor_residuals', None),
                             getattr(e, 'rms_limit', None))


class _HintWorker(QThread):
    ready = Signal(int, object)      # request id, HintResult | None

    def __init__(self, request_id: int, anchors, prep: dict):
        super().__init__()
        self._id = request_id
        self._anchors = anchors
        self._prep = prep

    def run(self):
        result = None
        try:
            from services.allsky.guided_hints import suggest_stars
            p = self._prep
            result = suggest_stars(
                self._anchors, p.get('candidates', []), p.get('detections', []),
                p['lat'], p['lon'], p['dt'],
                p['sky_cx'], p['sky_cy'], p['sky_r'],
                should_cancel=self.isInterruptionRequested)
        except Exception as e:
            log.debug(f"Guided hints failed (non-fatal): {e}")
        self.ready.emit(self._id, result)


class GuidedCalibrationSession(QObject):
    """Solve + suggestion plumbing for one open guided-calibration dialog.

    Signals:
        solving():            a solve started.
        solved(dict):         a solve passed; the model is held, NOT saved.
                              Keys: n_used, n_anchors, rms, rms_limit, note,
                              anchors [{name, used_as, residual, state}],
                              predicted [{name, x, y}], brightest first.
        failed(dict):         a solve failed. Keys: message, detail,
                              anchors [{name, residual, state}].
        hints_ready(object):  HintResult or None for the latest anchor set.
        saved(bool, str):     outcome of save(); the message explains a False.

    Every request the dialog makes ends in exactly one of these, including
    when the code handling a result raises: the dialog is modal and refuses
    to close mid-solve, so a swallowed exception would strand the user in
    front of a progress bar with capture running behind it.
    """

    solving = Signal()
    solved = Signal(dict)
    failed = Signal(dict)
    hints_ready = Signal(object)
    saved = Signal(bool, str)

    def __init__(self, prep: dict,
                 commit: Optional[Callable[[object], Tuple[bool, str]]] = None,
                 parent=None):
        super().__init__(parent)
        self._prep = prep
        self._commit = commit
        self._solve_worker: Optional[_SolveWorker] = None
        self._hint_workers: List[_HintWorker] = []
        self._hint_request = 0
        self._pending_model = None
        self._closed = False

    @property
    def prep(self) -> dict:
        return self._prep

    @property
    def pending_model(self):
        """The solved-but-unsaved model, or None."""
        return self._pending_model

    # ------------------------------------------------------------------
    # Dialog-facing slots
    # ------------------------------------------------------------------

    def solve(self, anchors: list) -> None:
        if self._solve_worker is not None or self._closed:
            return
        self._pending_model = None
        log.info(f"Guided calibration starting with {len(anchors)} anchors")
        self.solving.emit()
        worker = _SolveWorker(list(anchors), self._prep)
        worker.solved.connect(self._on_solved)
        worker.failed.connect(self._on_failed)
        worker.finished.connect(functools.partial(self._retire_solve, worker))
        self._solve_worker = worker
        worker.start()

    def discard(self) -> None:
        """Drop the held result (the user went back to adjust their stars)."""
        self._pending_model = None

    def save(self) -> None:
        """Hand the held result to the owner's commit hook.

        The model stays held until the commit reports success, so a failed
        save can simply be retried.
        """
        model = self._pending_model
        if model is None or self._commit is None:
            self.saved.emit(False, "There is no solved calibration to save.")
            return
        try:
            ok, message = self._commit(model)
        except Exception as e:
            log.error(f"Guided calibration save failed: {e}")
            ok, message = False, f"The calibration could not be saved: {e}"
        if ok:
            self._pending_model = None
        self.saved.emit(bool(ok), message)

    def request_hints(self, anchors: list) -> None:
        """Suggest positions for the stars not yet identified.

        Each call supersedes the last: a result for an older anchor set is
        dropped on arrival rather than the worker being interrupted.
        """
        if self._closed:
            return
        self._hint_request += 1
        # A superseded suggestion solve is pure waste: stop it rather than
        # let edits made in quick succession stack up solves nobody reads.
        for stale in self._hint_workers:
            stale.requestInterruption()
        from services.allsky.guided_hints import MIN_HINT_ANCHORS
        if len(anchors) < MIN_HINT_ANCHORS:
            self.hints_ready.emit(None)
            return
        worker = _HintWorker(self._hint_request, list(anchors), self._prep)
        worker.ready.connect(self._on_hints)
        worker.finished.connect(functools.partial(self._retire_hint, worker))
        self._hint_workers.append(worker)
        worker.start()

    def close(self) -> None:
        """End the session: late results are ignored, workers waited out."""
        self._closed = True
        self._pending_model = None
        # Nobody will read a suggestion now. Without this, closing the dialog
        # right after adding a star held the GUI thread for the rest of that
        # solve — and for each one stacked behind it (measured 0.65 s for
        # five on a fast desktop; the rig PCs are slower).
        for w in self._hint_workers:
            w.requestInterruption()
        still_running = False
        for w in [self._solve_worker, *self._hint_workers]:
            if w is not None and w.isRunning():
                still_running |= not w.wait(5000)
        # The prep dict pins two full-resolution frames. A worker that
        # outlived the wait still reads it and still needs its retire slot,
        # so in that (never observed) case the session is left to the parent.
        if not still_running:
            self._release()

    def _release(self) -> None:
        self._prep = {}
        self._commit = None
        self.deleteLater()

    # ------------------------------------------------------------------
    # Worker results
    # ------------------------------------------------------------------

    def _on_solved(self, model) -> None:
        if self._closed:
            return
        try:
            self._publish_solved(model)
        except Exception as e:
            self._pending_model = None
            self._on_failed(f"The solve finished but its result could not be "
                            f"shown: {e}", None, None)

    def _publish_solved(self, model) -> None:
        self._pending_model = model
        limit = float(getattr(model, 'guided_rms_limit', 0.0) or 0.0)
        rows = []
        for given, used_as, residual in getattr(model, 'guided_residuals', []):
            state = ('excluded' if residual is None
                     else 'renamed' if used_as != given else 'ok')
            rows.append({'name': given, 'used_as': used_as,
                         'residual': residual, 'state': state})
        self.solved.emit({
            'n_used': int(model.n_matches),
            'n_anchors': len(rows) or int(model.n_matches),
            'rms': float(model.rms_residual),
            'rms_limit': limit,
            'note': getattr(model, 'guided_note', None) or '',
            'anchors': rows,
            'predicted': self._predict(model),
        })

    def _on_failed(self, message: str, residuals, limit) -> None:
        if self._closed:
            return
        log.warning(f"Guided calibration failed: {message}")
        rows = _classify_failed(residuals or [], limit)
        suspects = [r['name'] for r in rows if r['state'] == 'suspect']
        if suspects:
            worst = rows[0]
            off = ("off the image" if worst['residual'] == float('inf')
                   else f"{worst['residual']:.0f} px from where that star "
                        "should be")
            summary = (
                f"That didn't fit. '{worst['name']}' is {off} — most likely "
                "mis-identified or mis-clicked. Your stars are kept: remove "
                "or re-identify it and solve again.")
            if len(suspects) > 1:
                others = ", ".join(f"'{n}'" for n in suspects[1:])
                summary += f" Also check {others}."
        else:
            summary = _short_reason(message)
        self.failed.emit({'message': summary, 'detail': message, 'anchors': rows})

    def _on_hints(self, request_id: int, result) -> None:
        if self._closed or request_id != self._hint_request:
            return
        self.hints_ready.emit(result)

    def _retire_solve(self, worker) -> None:
        if self._solve_worker is worker:
            self._solve_worker = None
        worker.deleteLater()
        self._release_if_orphaned()

    def _retire_hint(self, worker) -> None:
        if worker in self._hint_workers:
            self._hint_workers.remove(worker)
        worker.deleteLater()
        self._release_if_orphaned()

    def _release_if_orphaned(self) -> None:
        """close() gave up waiting on a worker; let go once the last retires."""
        if (self._closed and self._prep and self._solve_worker is None
                and not self._hint_workers):
            self._release()

    def _predict(self, model) -> list:
        """Where the solved model puts every bright candidate star."""
        out = []
        w = self._prep.get('image_width', 0) or 0
        h = self._prep.get('image_height', 0) or 0
        for c in self._prep.get('candidates', []):
            xy = model.altaz_to_pixel(float(c['alt']), float(c['az']))
            if xy is None:
                continue
            if w and h and not (0 <= xy[0] < w and 0 <= xy[1] < h):
                continue
            out.append({'name': c['name'], 'x': float(xy[0]), 'y': float(xy[1])})
        return out[:_MAX_PREDICTED]


def _classify_failed(residuals, limit) -> list:
    """Rows for the dialog from a failed solve's residuals (worst first)."""
    if not residuals:
        return []
    finite = sorted(d for _n, d in residuals if d != float('inf'))
    median = finite[len(finite) // 2] if finite else 0.0
    floor = max(float(limit or 0.0), _SUSPECT_MEDIAN_FACTOR * median)
    rows = []
    for i, (name, d) in enumerate(residuals):
        suspect = i == 0 or d > floor
        rows.append({'name': name, 'residual': d,
                     'state': 'suspect' if suspect else 'ok'})
    return rows


def _short_reason(message: str) -> str:
    lower = message.lower()
    if 'below the horizon' in lower:
        return message
    if 'implausible model' in lower:
        return ("The stars fit, but only with a lens shape no real lens has — "
                "usually two identifications swapped. Your stars are kept: "
                "check them and solve again.")
    if 'need at least' in lower:
        return message
    first = message.split('. ')[0].strip()
    return f"Calibration failed — {first}. Your stars are kept."
