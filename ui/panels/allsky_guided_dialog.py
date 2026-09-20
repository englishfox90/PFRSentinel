"""
Guided all-sky calibration dialog (UI only).

Shows the latest frame on a zoomable canvas; the user clicks bright stars
(clicks snap to detected centroids) and names each one. The dialog stays open
for the whole session — it asks for a solve, shows the outcome over the same
frame, and only closes once a result has been saved or the user cancels. A
failed solve keeps every identified star and marks the ones that didn't fit
(issue #79: the window used to vanish on Solve and come back empty).

No business logic lives here. Solving, star suggestions and saving belong to
ui/controllers/guided_calibration_session.py and AllSkyController; this file
emits requests and renders what comes back.
"""
import math
from typing import List, Optional, Tuple

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPixmap
from PySide6.QtWidgets import (
    QCompleter, QDialog, QHBoxLayout, QVBoxLayout, QWidget,
)
from qfluentwidgets import (
    BodyLabel, CaptionLabel, EditableComboBox, IndeterminateProgressBar,
    ListWidget, MessageBox, PushButton, PrimaryPushButton, StrongBodyLabel,
    ToolButton,
)

from ..components.star_pick_canvas import (
    CanvasHint, CanvasMarker, StarPickCanvas)
from ..theme.icons import mdi
from ..theme.tokens import Spacing
from services.allsky.guided_calibration import MIN_ANCHORS

# Snap radius within which a click locks onto a detected star. Expressed as a
# fraction of the sky radius so it stays a constant *display* distance at any
# frame resolution (a fixed 30 image-px was only ~7 display-px on 3552px
# frames — most careful clicks missed the snap and carried 10-20px of error,
# which alone exceeded the solver's RMS limit).
_SNAP_SKY_FRACTION = 0.035
_SNAP_MIN_PX = 30.0

# The controls are a fixed thin column; every other pixel goes to the frame.
_SIDEBAR_WIDTH = 320
_SCREEN_FRACTION = 0.92

_STATE_COLLECT, _STATE_SOLVING, _STATE_REVIEW = 'collect', 'solving', 'review'

_TONE_COLOURS = {'info': '', 'ok': '#3DD68C', 'warn': '#FFD166',
                 'error': '#FF6B6B'}
_ROW_COLOURS = {'suspect': QColor(255, 107, 107),
                'excluded': QColor(255, 160, 60),
                'renamed': QColor(255, 160, 60)}


class GuidedCalibrationDialog(QDialog):
    """Collect user-identified star anchors and review the solve.

    Signals (requests to the controller layer):
        solve_requested(list): anchors as (px, py, ra_deg, dec_deg, name).
        hints_requested(list): same shape; asks where the unnamed stars are.
        save_requested():      the reviewed result should be saved.
        discard_requested():   the reviewed result was abandoned.
    """

    solve_requested = Signal(list)
    hints_requested = Signal(list)
    save_requested = Signal()
    discard_requested = Signal()

    def __init__(self, prep: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Guided All-Sky Calibration")
        self.setWindowFlags(self.windowFlags() | Qt.WindowMaximizeButtonHint)
        self.setSizeGripEnabled(True)
        self._detections = prep.get('detections', [])
        self._candidates = prep.get('candidates', [])
        self._snap_px = max(_SNAP_MIN_PX,
                            _SNAP_SKY_FRACTION * float(prep.get('sky_r', 0.0)))
        self._anchors: List[dict] = []   # {px, py, ra, dec, name, snapped, ...}
        self._pending: Optional[Tuple[float, float]] = None
        self._pending_snapped = False
        self._hints: List[CanvasHint] = []
        self._predicted: List[CanvasHint] = []
        self._state = _STATE_COLLECT
        self.saved = False

        # Display uses the pre-stretched copy (a raw all-sky frame is near-
        # black, see issue #10) — detections/anchors still key off prep['image']
        # coordinates, which stretch_for_display preserves pixel-for-pixel.
        self._build_ui(prep.get('display_image', prep['image']))
        self._fit_to_screen()
        self._on_zoom_changed(self._canvas.zoom())
        self._refresh()

    # ------------------------------------------------------------------
    def _build_ui(self, pil_image):
        root = QHBoxLayout(self)
        root.setContentsMargins(Spacing.base, Spacing.base,
                                Spacing.base, Spacing.base)
        root.setSpacing(Spacing.base)

        # Left: the frame, given all the room there is.
        left = QVBoxLayout()
        left.setSpacing(Spacing.xs)
        self._canvas = StarPickCanvas()
        self._canvas.set_image(self._pil_to_pixmap(pil_image), self._snap_px)
        self._canvas.clicked.connect(self._on_image_click)
        self._canvas.zoom_changed.connect(self._on_zoom_changed)
        left.addWidget(self._canvas, 1)

        bar = QHBoxLayout()
        bar.setSpacing(Spacing.xs)
        for icon, tip, slot in (
                ('magnify-minus-outline', "Zoom out (-)", self._canvas.zoom_out),
                ('magnify-plus-outline', "Zoom in (+)", self._canvas.zoom_in),
                ('fit-to-screen-outline', "Whole frame (0, or double-click)",
                 self._canvas.reset_view)):
            btn = ToolButton(mdi(icon))
            btn.setToolTip(tip)
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(slot)
            bar.addWidget(btn)
        self._zoom_lbl = CaptionLabel("")
        bar.addWidget(self._zoom_lbl)
        bar.addStretch(1)
        bar.addWidget(CaptionLabel(
            "Scroll to zoom · drag to pan · hover to magnify"))
        left.addLayout(bar)
        root.addLayout(left, 1)

        # Right: a thin column of controls.
        side_host = QWidget()
        side_host.setFixedWidth(_SIDEBAR_WIDTH)
        side = QVBoxLayout(side_host)
        side.setContentsMargins(0, 0, 0, 0)
        side.setSpacing(Spacing.sm)
        root.addWidget(side_host)

        self._hint = CaptionLabel(
            "Click a bright star, say which star it is, then Add. Identify "
            f"at least {MIN_ANCHORS}, spread across the sky — 6 or more lets "
            "the solver recover if one is wrong. After 3, the other bright "
            "stars are labelled for you.")
        self._hint.setWordWrap(True)
        side.addWidget(self._hint)

        self._pending_lbl = CaptionLabel("No star selected.")
        self._pending_lbl.setWordWrap(True)
        side.addWidget(self._pending_lbl)

        self._combo = EditableComboBox()
        self._combo.setPlaceholderText("Search for a star by name…")
        for c in self._candidates:
            self._combo.addItem(f"{c['name']}  (mag {c['vmag']:.1f}, "
                                f"alt {c['alt']:.0f}°)", userData=c)
        self._combo.setCurrentIndex(-1)
        # EditableComboBox does no filtering by itself — its text handler
        # only exact-matches the FULL item string (which ends in "(mag …)"),
        # so typed star names never matched and the list had to be scrolled.
        # A contains-mode completer provides the intended type-to-search.
        completer = QCompleter(
            [self._combo.itemText(i) for i in range(self._combo.count())],
            self._combo)
        completer.setCaseSensitivity(Qt.CaseInsensitive)
        completer.setFilterMode(Qt.MatchContains)
        completer.setMaxVisibleItems(12)
        self._combo.setCompleter(completer)
        side.addWidget(self._combo)

        self._add_btn = PushButton("Add this star")
        self._add_btn.setCursor(Qt.PointingHandCursor)
        self._add_btn.clicked.connect(self._on_add)
        side.addWidget(self._add_btn)

        side.addWidget(CaptionLabel("Identified stars:"))
        self._list = ListWidget()
        self._list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._list.currentRowChanged.connect(self._on_row_selected)
        side.addWidget(self._list, 1)

        self._remove_btn = PushButton("Remove selected")
        self._remove_btn.setCursor(Qt.PointingHandCursor)
        self._remove_btn.clicked.connect(self._on_remove)
        side.addWidget(self._remove_btn)

        # Outcome area: always in the same place, never a vanished window.
        self._status_title = StrongBodyLabel("")
        self._status_title.setWordWrap(True)
        side.addWidget(self._status_title)
        self._status_body = CaptionLabel("")
        self._status_body.setWordWrap(True)
        side.addWidget(self._status_body)
        self._progress = IndeterminateProgressBar()
        self._progress.hide()
        side.addWidget(self._progress)

        # The primary action gets the column's full width: three buttons on
        # one row clipped their own labels at this sidebar width.
        self._solve_btn = PrimaryPushButton("Solve")
        self._solve_btn.setCursor(Qt.PointingHandCursor)
        self._solve_btn.clicked.connect(self._on_primary)
        side.addWidget(self._solve_btn)

        row = QHBoxLayout()
        row.setSpacing(Spacing.sm)
        self._cancel_btn = PushButton("Cancel")
        self._cancel_btn.setCursor(Qt.PointingHandCursor)
        self._cancel_btn.clicked.connect(self.reject)
        self._back_btn = PushButton("Adjust stars")
        self._back_btn.setCursor(Qt.PointingHandCursor)
        self._back_btn.clicked.connect(self._on_back)
        row.addWidget(self._cancel_btn)
        row.addStretch(1)
        row.addWidget(self._back_btn)
        side.addLayout(row)

    def _fit_to_screen(self) -> None:
        screen = (self.parent().screen() if self.parent() is not None
                  else QGuiApplication.primaryScreen())
        if screen is None:
            self.resize(1280, 860)
            return
        avail = screen.availableGeometry()
        self.resize(int(avail.width() * _SCREEN_FRACTION),
                    int(avail.height() * _SCREEN_FRACTION))

    @staticmethod
    def _pil_to_pixmap(pil_image) -> QPixmap:
        img = pil_image.convert('RGB')
        w, h = img.size
        data = img.tobytes('raw', 'RGB')
        return QPixmap.fromImage(
            QImage(data, w, h, 3 * w, QImage.Format_RGB888).copy())

    # ------------------------------------------------------------------
    # Results from the controller layer
    # ------------------------------------------------------------------

    def show_solving(self) -> None:
        self._state = _STATE_SOLVING
        self._set_status("Solving…", "Fitting the lens model to your stars. "
                         "This takes a few seconds.", 'info')
        self._refresh()

    def show_failed(self, result: dict) -> None:
        """A solve failed: keep every star, mark the ones that didn't fit."""
        self._state = _STATE_COLLECT
        self._apply_residuals(result.get('anchors', []))
        self._set_status("Not solved — your stars are kept",
                         result.get('message', ''), 'error')
        self._refresh()
        suspects = [i for i, a in enumerate(self._anchors)
                    if a.get('state') == 'suspect']
        if suspects:
            self._list.setCurrentRow(suspects[0])

    def show_solved(self, result: dict) -> None:
        """A solve passed and is held unsaved: show it over the frame."""
        self._state = _STATE_REVIEW
        self._apply_residuals(result.get('anchors', []))
        # An identified star already carries its own marker and label.
        mine = {a.get('used_as') or a['name'] for a in self._anchors}
        self._predicted = [
            CanvasHint(p['x'], p['y'], p['name'], supported=True, emphasised=True)
            for p in result.get('predicted', []) if p['name'] not in mine]
        body = (f"RMS {result['rms']:.1f} px over {result['n_used']} of "
                f"{result['n_anchors']} stars. The blue circles show where "
                "this calibration puts every bright star: zoom in and check "
                "they sit on real stars, then Save. Nothing is saved yet.")
        if result.get('note'):
            body = f"{result['note']}\n\n{body}"
        self._set_status("Solved — check it, then save", body,
                         'warn' if result.get('note') else 'ok')
        self._canvas.reset_view()
        self._refresh()

    def show_hints(self, result) -> None:
        """Star suggestions for the current anchors (HintResult or None)."""
        self._hints = []
        if result is not None and result.trusted:
            self._hints = [CanvasHint(h.x, h.y, h.name, supported=h.supported)
                           for h in result.hints]
        if self._state == _STATE_COLLECT:
            if result is not None and not result.trusted and result.message:
                self._set_status("Check your stars", result.message, 'warn')
            elif self._status_title.text() == "Check your stars":
                self._set_status("", "", 'info')
            self._refresh()

    def show_saved(self, ok: bool, message: str) -> None:
        if ok:
            self.saved = True
            self.accept()
            return
        # Back to the review, not to picking: the session still holds the
        # solved model, and a refusal can be transient (a Calibrate Now run
        # in flight). Dropping to "Solve" left no way to save again, and
        # solving would discard the very result the user was trying to keep.
        self._state = _STATE_REVIEW
        self._set_status("Not saved — try again", message, 'error')
        self._refresh()

    # ------------------------------------------------------------------
    # User actions
    # ------------------------------------------------------------------

    def _on_image_click(self, ix: float, iy: float):
        """Snap the click to the nearest detected star (image coords)."""
        if self._state != _STATE_COLLECT:
            return
        best, best_d = None, self._snap_px
        for d in self._detections:
            dist = math.hypot(d[0] - ix, d[1] - iy)
            if dist <= best_d:
                best, best_d = (d[0], d[1]), dist
        self._pending = best if best is not None else (ix, iy)
        self._pending_snapped = best is not None
        snapped = ("snapped to detected star" if best is not None
                   else "no detected star nearby — the solve is much less "
                        "accurate with unsnapped clicks")
        text = (f"Selected ({self._pending[0]:.0f}, {self._pending[1]:.0f}) "
                f"— {snapped}.")
        suggestion = self._hint_near(*self._pending)
        if suggestion is not None:
            self._select_candidate(suggestion.label)
            text += (f" This looks like {suggestion.label} — check, then Add.")
        self._pending_lbl.setText(text)
        self._combo.setFocus()
        self._refresh()

    def _hint_near(self, ix: float, iy: float) -> Optional[CanvasHint]:
        best, best_d = None, self._snap_px
        for h in self._hints:
            d = math.hypot(h.x - ix, h.y - iy)
            if d <= best_d:
                best, best_d = h, d
        return best

    def _select_candidate(self, name: str) -> None:
        for i in range(self._combo.count()):
            c = self._combo.itemData(i)
            if c and c.get('name') == name:
                self._combo.setCurrentIndex(i)
                self._combo.setCursorPosition(0)   # show the name, not "…alt 43°)"
                return

    def _on_add(self):
        if self._pending is None:
            self._pending_lbl.setText("Click a star in the image first.")
            return
        c = self._combo.currentData() or self._match_typed_star()
        if not c:
            self._pending_lbl.setText(
                "Choose which star this is — type a name to search the list.")
            return
        if any(a['name'] == c['name'] for a in self._anchors):
            self._pending_lbl.setText(
                f"{c['name']} is already identified — each star can only be "
                "used once.")
            return
        self._anchors.append({
            'px': self._pending[0], 'py': self._pending[1],
            'ra': c['ra_deg'], 'dec': c['dec_deg'], 'name': c['name'],
            'snapped': self._pending_snapped})
        self._pending = None
        self._pending_snapped = False
        self._combo.setCurrentIndex(-1)
        self._combo.setText("")
        self._pending_lbl.setText(f"{c['name']} added.")
        self._anchors_changed()

    def _match_typed_star(self):
        """Resolve free-typed text to a candidate star.

        Pressing Enter in an EditableComboBox appends the raw text as a new
        (data-less) item instead of selecting a match, so 'vega' + Add used
        to do nothing, silently. Accept the typed text when it names exactly
        one candidate (or matches one exactly), case-insensitive.
        """
        text = self._combo.text().strip().lower()
        if not text:
            return None
        hits = [c for c in self._candidates if text in c['name'].lower()]
        exact = [c for c in hits if c['name'].lower() == text]
        if exact:
            return exact[0]
        return hits[0] if len(hits) == 1 else None

    def _on_remove(self):
        i = self._list.currentRow()
        if 0 <= i < len(self._anchors):
            self._anchors.pop(i)
            self._anchors_changed()

    def _on_row_selected(self, row: int) -> None:
        """Selecting a star in the list brings it into view."""
        if 0 <= row < len(self._anchors) and self._canvas.zoom() > 1.0:
            a = self._anchors[row]
            self._canvas.centre_on(a['px'], a['py'])

    def _anchors_changed(self) -> None:
        """The anchor set changed: old residuals no longer describe it."""
        for a in self._anchors:
            for key in ('state', 'residual', 'used_as'):
                a.pop(key, None)
        self._set_status("", "", 'info')
        self._refresh()
        self.hints_requested.emit(self._anchor_tuples())

    def _on_primary(self):
        if self._state == _STATE_REVIEW:
            self._state = _STATE_SOLVING
            self._set_status("Saving…", "", 'info')
            self._refresh()
            self.save_requested.emit()
        elif self._state == _STATE_COLLECT:
            self.solve_requested.emit(self._anchor_tuples())

    def _on_back(self):
        """Leave the review without saving; the stars stay as they were."""
        self.discard_requested.emit()
        self._state = _STATE_COLLECT
        self._predicted = []
        self._set_status("", "", 'info')
        self._refresh()

    def reject(self):
        if self._state == _STATE_SOLVING:
            return   # a few seconds; closing now would orphan the result
        if self._anchors and not self._confirm_abandon():
            return
        if self._state == _STATE_REVIEW:
            self.discard_requested.emit()
        super().reject()

    def _confirm_abandon(self) -> bool:
        unsaved = (" The solved calibration has not been saved."
                   if self._state == _STATE_REVIEW else "")
        box = MessageBox(
            "Close guided calibration?",
            f"You have identified {len(self._anchors)} star(s); closing "
            f"discards them.{unsaved}", self)
        box.yesButton.setText("Close")
        box.cancelButton.setText("Keep working")
        return bool(box.exec())

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------

    def _anchor_tuples(self) -> list:
        return [(a['px'], a['py'], a['ra'], a['dec'], a['name'])
                for a in self._anchors]

    def _apply_residuals(self, rows: List[dict]) -> None:
        by_name = {r['name']: r for r in rows}
        for a in self._anchors:
            r = by_name.get(a['name'])
            a['state'] = r['state'] if r else 'ok'
            a['residual'] = r['residual'] if r else None
            a['used_as'] = r.get('used_as', a['name']) if r else a['name']

    def _set_status(self, title: str, body: str, tone: str) -> None:
        colour = _TONE_COLOURS.get(tone, '')
        self._status_title.setStyleSheet(f"color: {colour};" if colour else "")
        self._status_title.setText(title)
        self._status_title.setVisible(bool(title))
        self._status_body.setText(body)
        self._status_body.setVisible(bool(body))

    def _on_zoom_changed(self, zoom: float) -> None:
        self._zoom_lbl.setText("Whole frame" if zoom <= 1.0 else f"{zoom:.1f}×")

    @staticmethod
    def _label(a: dict) -> str:
        used_as = a.get('used_as') or a['name']
        return a['name'] if used_as == a['name'] else f"{a['name']} → {used_as}"

    @staticmethod
    def _row_text(a: dict) -> str:
        text = GuidedCalibrationDialog._label(a)
        if a.get('state') == 'excluded':
            return f"{text}  — left out"
        residual = a.get('residual')
        if residual is not None:
            off = "off image" if residual == float('inf') else f"{residual:.0f} px off"
            return f"{text}  — {off}"
        return f"{text}  {'✓' if a.get('snapped') else '⚠ unsnapped'}"

    def _refresh(self):
        selected = self._list.currentRow()
        self._list.blockSignals(True)
        self._list.clear()
        for a in self._anchors:
            self._list.addItem(self._row_text(a))
            colour = _ROW_COLOURS.get(a.get('state'))
            if colour is not None:
                self._list.item(self._list.count() - 1).setForeground(colour)
        if 0 <= selected < self._list.count():
            self._list.setCurrentRow(selected)
        self._list.blockSignals(False)

        collecting = self._state == _STATE_COLLECT
        reviewing = self._state == _STATE_REVIEW
        markers = [CanvasMarker(a['px'], a['py'], self._label(a),
                                a.get('state') or 'ok') for a in self._anchors]
        self._canvas.set_overlays(
            markers, self._predicted if reviewing else self._hints,
            self._pending if collecting else None)
        self._canvas.set_interactive(collecting)

        for w in (self._combo, self._add_btn, self._remove_btn, self._list):
            w.setEnabled(collecting)
        self._progress.setVisible(self._state == _STATE_SOLVING)
        self._cancel_btn.setEnabled(self._state != _STATE_SOLVING)
        self._back_btn.setVisible(reviewing)

        n = len(self._anchors)
        if reviewing:
            self._solve_btn.setText("Save calibration")
            self._solve_btn.setEnabled(True)
        else:
            self._solve_btn.setEnabled(collecting and n >= MIN_ANCHORS)
            self._solve_btn.setText(
                f"Solve ({n}/{MIN_ANCHORS})" if n < MIN_ANCHORS
                else f"Solve ({n} stars)")
