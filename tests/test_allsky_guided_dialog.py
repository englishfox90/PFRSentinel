"""
Tests for ui/panels/allsky_guided_dialog.py (issue #79).

The reported faults were all about what the dialog did around a solve: it
closed when Solve was pressed, came back empty after a failure that named the
suspect star, and gave the frame a fixed 760 px no matter the monitor.
"""
import pytest

pytest.importorskip("PySide6")

from PIL import Image
from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QApplication

from services.allsky.guided_calibration import MIN_ANCHORS
from services.allsky.guided_hints import HintResult, StarHint
from ui.panels import allsky_guided_dialog as gd

FRAME = 2000


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _candidates():
    names = ['Vega', 'Deneb', 'Altair', 'Arcturus', 'Spica', 'Regulus', 'Capella']
    return [{'name': n, 'ra_deg': 10.0 * i, 'dec_deg': 5.0 * i,
             'alt': 40.0 + i, 'az': 30.0 * i, 'vmag': 0.1 * i}
            for i, n in enumerate(names)]


def _prep():
    image = Image.new('RGB', (FRAME, FRAME), (4, 4, 4))
    # One detection per candidate, on a diagonal, so clicks have a snap target.
    detections = [(200.0 + 250.0 * i, 300.0 + 200.0 * i, 900.0 - i)
                  for i in range(7)]
    return {'image': image, 'display_image': image, 'detections': detections,
            'candidates': _candidates(), 'sky_cx': 1000.0, 'sky_cy': 1000.0,
            'sky_r': 900.0, 'image_width': FRAME, 'image_height': FRAME}


@pytest.fixture
def dialog(qapp):
    dlg = gd.GuidedCalibrationDialog(_prep())
    requests = {'solve': [], 'hints': [], 'save': 0, 'discard': 0}
    dlg.solve_requested.connect(requests['solve'].append)
    dlg.hints_requested.connect(requests['hints'].append)
    dlg.save_requested.connect(
        lambda: requests.__setitem__('save', requests['save'] + 1))
    dlg.discard_requested.connect(
        lambda: requests.__setitem__('discard', requests['discard'] + 1))
    dlg.show()
    qapp.processEvents()
    yield dlg, requests
    dlg._anchors.clear()      # reject() would otherwise ask to confirm
    dlg.close()
    dlg.deleteLater()
    qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    qapp.processEvents()


def _identify(dlg, n, start=0):
    """Click n detections from `start` and name each after its candidate."""
    for i in range(start, start + n):
        x, y, _flux = dlg._detections[i]
        dlg._on_image_click(x + 3.0, y - 2.0)
        dlg._select_candidate(dlg._candidates[i]['name'])
        dlg._on_add()


def _failed(suspect='Altair'):
    rows = [{'name': c['name'], 'residual': 140.0 if c['name'] == suspect else 6.0,
             'state': 'suspect' if c['name'] == suspect else 'ok'}
            for c in _candidates()[:MIN_ANCHORS]]
    return {'message': f"That didn't fit. '{suspect}' is 140 px off.",
            'detail': '', 'anchors': rows}


def _solved(renamed=None):
    rows = [{'name': c['name'], 'used_as': c['name'], 'residual': 3.0, 'state': 'ok'}
            for c in _candidates()[:MIN_ANCHORS]]
    if renamed:
        rows[0].update(used_as=renamed, state='renamed')
    return {'n_used': MIN_ANCHORS, 'n_anchors': MIN_ANCHORS, 'rms': 3.4,
            'rms_limit': 12.0, 'note': '', 'anchors': rows,
            'predicted': [{'name': 'Vega', 'x': 200.0, 'y': 300.0},
                          {'name': 'Capella', 'x': 1500.0, 'y': 400.0}]}


class TestLayout:

    def test_frame_gets_the_room_and_controls_stay_a_thin_column(self, dialog, qapp):
        dlg, _req = dialog
        dlg.resize(2400, 1400)
        qapp.processEvents()
        assert dlg._canvas.width() > 2400 - gd._SIDEBAR_WIDTH - 100
        assert dlg._canvas.height() > 1200

    def test_dialog_can_be_maximised(self, dialog):
        dlg, _req = dialog
        from PySide6.QtCore import Qt
        assert dlg.windowFlags() & Qt.WindowMaximizeButtonHint

    def test_view_can_zoom(self, dialog):
        dlg, _req = dialog
        dlg._canvas.zoom_in()
        assert dlg._canvas.zoom() > 1.0
        assert '×' in dlg._zoom_lbl.text()


class TestCollecting:

    def test_click_snaps_to_the_detected_star(self, dialog):
        dlg, _req = dialog
        x, y, _flux = dlg._detections[0]
        dlg._on_image_click(x + 5.0, y + 5.0)
        assert dlg._pending == (x, y)

    def test_solve_needs_the_minimum_number_of_stars(self, dialog):
        dlg, _req = dialog
        _identify(dlg, MIN_ANCHORS - 1)
        assert not dlg._solve_btn.isEnabled()
        assert dlg._solve_btn.text() == f"Solve ({MIN_ANCHORS - 1}/{MIN_ANCHORS})"
        _identify(dlg, 1, start=MIN_ANCHORS - 1)
        assert dlg._solve_btn.isEnabled()

    def test_each_change_asks_for_fresh_suggestions(self, dialog):
        dlg, req = dialog
        _identify(dlg, 3)
        assert [len(a) for a in req['hints']] == [1, 2, 3]
        dlg._list.setCurrentRow(0)
        dlg._on_remove()
        assert len(req['hints'][-1]) == 2

    def test_clicking_a_suggested_star_offers_its_name(self, dialog):
        dlg, _req = dialog
        _identify(dlg, 3)
        x, y, _flux = dlg._detections[4]
        dlg.show_hints(HintResult(
            hints=[StarHint('Spica', x + 8.0, y - 6.0, 1.0, True)], trusted=True))
        dlg._on_image_click(x, y)
        assert dlg._combo.currentData()['name'] == 'Spica'
        assert 'Spica' in dlg._pending_lbl.text()

    def test_dropping_a_suggested_star_identifies_it_in_one_gesture(self, dialog):
        dlg, req = dialog
        _identify(dlg, 3)
        x, y, _flux = dlg._detections[4]
        dlg.show_hints(HintResult(
            hints=[StarHint('Spica', x + 8.0, y - 6.0, 1.0, True)], trusted=True))
        dlg._on_hint_dropped('Spica', x + 4.0, y + 3.0)

        added = dlg._anchors[-1]
        assert added['name'] == 'Spica'
        assert (added['px'], added['py']) == (x, y), "snapped to the detection"
        assert added['snapped']
        assert 'Spica added' in dlg._pending_lbl.text()
        assert len(req['hints'][-1]) == 4, "fresh suggestions were requested"

    def test_dropping_without_a_detection_nearby_keeps_the_drop_point(self, dialog):
        dlg, _req = dialog
        _identify(dlg, 3)
        dlg._on_hint_dropped('Spica', 1900.0, 100.0)
        added = dlg._anchors[-1]
        assert (added['px'], added['py']) == (1900.0, 100.0)
        assert not added['snapped']
        assert 'unsnapped' in dlg._pending_lbl.text()

    def test_dropping_an_already_identified_star_is_refused(self, dialog):
        dlg, _req = dialog
        _identify(dlg, 3)
        dlg._on_hint_dropped('Vega', 1900.0, 100.0)
        assert len(dlg._anchors) == 3
        assert 'already identified' in dlg._pending_lbl.text()

    def test_untrusted_suggestions_are_withheld_and_explained(self, dialog):
        dlg, _req = dialog
        _identify(dlg, 3)
        dlg.show_hints(HintResult(
            hints=[StarHint('Spica', 10.0, 10.0, 1.0, False)], trusted=False,
            message="one of them is probably mis-identified"))
        assert dlg._hints == []
        assert 'mis-identified' in dlg._status_body.text()
        # ...and the warning goes away once the anchors agree again.
        dlg.show_hints(HintResult(hints=[], trusted=True))
        assert dlg._status_body.text() == ''


class TestSolving:

    def test_solve_keeps_the_dialog_open_and_shows_progress(self, dialog, qapp):
        dlg, req = dialog
        _identify(dlg, MIN_ANCHORS)
        dlg._on_primary()
        assert len(req['solve']) == 1 and len(req['solve'][0]) == MIN_ANCHORS
        dlg.show_solving()
        qapp.processEvents()
        assert dlg.isVisible()
        assert dlg._progress.isVisible()
        assert not dlg._solve_btn.isEnabled()
        assert not dlg._cancel_btn.isEnabled()

    def test_failure_keeps_every_star_and_marks_the_suspect(self, dialog, qapp):
        dlg, _req = dialog
        _identify(dlg, MIN_ANCHORS)
        dlg.show_solving()
        dlg.show_failed(_failed('Altair'))
        qapp.processEvents()

        assert dlg.isVisible()
        assert [a['name'] for a in dlg._anchors] == \
            [c['name'] for c in _candidates()[:MIN_ANCHORS]]
        suspect = next(a for a in dlg._anchors if a['name'] == 'Altair')
        assert suspect['state'] == 'suspect'
        assert '140 px off' in dlg._list.item(2).text()
        assert dlg._list.currentRow() == 2, "the suspect is selected, ready to remove"
        assert 'kept' in dlg._status_title.text()
        assert dlg._solve_btn.isEnabled(), "the user can fix it and solve again"

    def test_editing_after_a_failure_clears_the_stale_marks(self, dialog):
        dlg, _req = dialog
        _identify(dlg, MIN_ANCHORS)
        dlg.show_failed(_failed('Altair'))
        dlg._list.setCurrentRow(2)
        dlg._on_remove()
        assert all('state' not in a for a in dlg._anchors)
        assert dlg._status_title.text() == ''

    def test_cannot_be_closed_mid_solve(self, dialog):
        dlg, _req = dialog
        _identify(dlg, MIN_ANCHORS)
        dlg.show_solving()
        dlg.reject()
        assert dlg.isVisible()


class TestReview:

    def test_solved_result_is_shown_for_review_not_saved(self, dialog, qapp):
        dlg, req = dialog
        _identify(dlg, MIN_ANCHORS)
        dlg.show_solved(_solved())
        qapp.processEvents()

        assert dlg.isVisible() and not dlg.saved
        assert dlg._solve_btn.text() == "Save calibration"
        assert dlg._back_btn.isVisible()
        assert 'Nothing is saved yet' in dlg._status_body.text()
        assert req['save'] == 0

    def test_review_draws_predicted_stars_but_not_over_identified_ones(self, dialog):
        dlg, _req = dialog
        _identify(dlg, MIN_ANCHORS)
        dlg.show_solved(_solved())
        assert [h.label for h in dlg._predicted] == ['Capella']

    def test_a_corrected_identification_is_shown_on_the_star(self, dialog):
        dlg, _req = dialog
        _identify(dlg, MIN_ANCHORS)
        dlg.show_solved(_solved(renamed='Sirius'))
        assert dlg._list.item(0).text().startswith('Vega → Sirius')

    def test_save_is_requested_then_the_dialog_closes_on_success(self, dialog):
        dlg, req = dialog
        _identify(dlg, MIN_ANCHORS)
        dlg.show_solved(_solved())
        dlg._on_primary()
        assert req['save'] == 1
        dlg.show_saved(True, "")
        assert dlg.saved and not dlg.isVisible()

    def test_failed_save_keeps_the_dialog_and_the_stars(self, dialog):
        dlg, _req = dialog
        _identify(dlg, MIN_ANCHORS)
        dlg.show_solved(_solved())
        dlg._on_primary()
        dlg.show_saved(False, "disk full")
        assert dlg.isVisible() and not dlg.saved
        assert len(dlg._anchors) == MIN_ANCHORS
        assert 'disk full' in dlg._status_body.text()

    def test_failed_save_can_be_retried_without_solving_again(self, dialog):
        """A refusal can be transient (Calibrate Now in flight). Dropping
        back to "Solve" left no way to save, and solving discards the model."""
        dlg, req = dialog
        _identify(dlg, MIN_ANCHORS)
        dlg.show_solved(_solved())
        dlg._on_primary()
        dlg.show_saved(False, "wait for Calibrate Now to finish")

        assert dlg._solve_btn.text() == "Save calibration"
        assert dlg._solve_btn.isEnabled()
        assert dlg._back_btn.isVisible()
        assert dlg._predicted, "the reviewed result is still on the frame"

        dlg._on_primary()
        assert req['save'] == 2 and req['solve'] == []
        dlg.show_saved(True, "")
        assert dlg.saved

    def test_dropping_a_predicted_star_discards_the_result_and_adds_it(self, dialog):
        """The review circles are where the solve thinks the stars are.
        Dragging one onto the real star says the solve missed it: back to
        picking, with that star now identified for the next solve."""
        dlg, req = dialog
        _identify(dlg, MIN_ANCHORS)
        dlg.show_solved(_solved())
        assert not dlg._canvas._interactive, "picking is off during review"

        dlg._on_hint_dropped('Capella', 1510.0, 405.0)

        assert req['discard'] == 1
        assert dlg._predicted == []
        assert dlg._solve_btn.text().startswith("Solve")
        assert [a['name'] for a in dlg._anchors][-1] == 'Capella'
        assert len(dlg._anchors) == MIN_ANCHORS + 1
        assert 'Solve' in dlg._pending_lbl.text()
        assert req['save'] == 0

    def test_dropping_an_identified_name_in_review_keeps_the_result(self, dialog):
        """A star the solver renamed leaves its given name drawn as a
        prediction. Dropping that circle must be refused BEFORE the held
        result is discarded, or the user loses the solve and gains nothing."""
        dlg, req = dialog
        _identify(dlg, MIN_ANCHORS)
        taken = dlg._anchors[0]['name']
        dlg.show_solved(_solved())
        predicted = list(dlg._predicted)

        dlg._on_hint_dropped(taken, 1510.0, 405.0)

        assert req['discard'] == 0
        assert dlg._predicted == predicted
        assert dlg._state == 'review'
        assert len(dlg._anchors) == MIN_ANCHORS
        assert 'already identified' in dlg._pending_lbl.text()

    def test_adjust_stars_discards_the_result_and_returns_to_picking(self, dialog):
        dlg, req = dialog
        _identify(dlg, MIN_ANCHORS)
        dlg.show_solved(_solved())
        dlg._on_back()
        assert req['discard'] == 1
        assert dlg._predicted == []
        assert dlg._solve_btn.text().startswith("Solve")
        assert len(dlg._anchors) == MIN_ANCHORS


class TestExcluding:
    """Issue #124: a doubtful star can be left out of the solve without
    deleting it, and the solver's own verdict on every star is visible."""

    @staticmethod
    def _exclude(dlg, row):
        dlg._list.setCurrentRow(row)
        dlg._on_toggle_excluded()

    def test_excluded_star_is_kept_but_left_out_of_solve_hints_and_count(self, dialog):
        dlg, req = dialog
        _identify(dlg, MIN_ANCHORS)
        assert dlg._solve_btn.isEnabled()

        self._exclude(dlg, 2)

        assert [a['name'] for a in dlg._anchors] == \
            [c['name'] for c in _candidates()[:MIN_ANCHORS]], "still listed"
        assert 'excluded by you' in dlg._list.item(2).text()
        assert dlg._list.item(2).foreground().color() == gd.anchors.row_colour(
            {'name': 'x', 'state': 'excluded'})
        assert [n for *_xy, n in req['hints'][-1]] == \
            ['Vega', 'Deneb', 'Arcturus', 'Spica']
        assert dlg._solve_btn.text() == f"Solve ({MIN_ANCHORS - 1}/{MIN_ANCHORS})"
        assert not dlg._solve_btn.isEnabled()

        _identify(dlg, 1, start=MIN_ANCHORS)
        dlg._on_primary()
        assert [n for *_xy, n in req['solve'][0]] == \
            ['Vega', 'Deneb', 'Arcturus', 'Spica', 'Regulus']

    def test_include_puts_the_star_back(self, dialog):
        dlg, req = dialog
        _identify(dlg, MIN_ANCHORS)
        self._exclude(dlg, 2)
        assert dlg._exclude_btn.text() == "Include selected"

        dlg._on_toggle_excluded()

        assert dlg._exclude_btn.text() == "Exclude selected"
        assert '✓' in dlg._list.item(2).text()
        assert len(req['hints'][-1]) == MIN_ANCHORS
        assert dlg._solve_btn.isEnabled()

    def test_toggle_button_follows_the_selection_and_fits_the_column(self, dialog, qapp):
        dlg, _req = dialog
        assert not dlg._exclude_btn.isEnabled(), "nothing selected"
        _identify(dlg, 3)
        self._exclude(dlg, 0)
        dlg._list.setCurrentRow(1)
        assert dlg._exclude_btn.text() == "Exclude selected"
        dlg._list.setCurrentRow(0)
        assert dlg._exclude_btn.text() == "Include selected"
        qapp.processEvents()
        for btn in (dlg._remove_btn, dlg._exclude_btn):
            assert btn.width() >= btn.sizeHint().width(), btn.text()
        dlg.show_solving()
        assert not dlg._exclude_btn.isEnabled()

    def test_exclusion_survives_edits_and_a_failed_solve(self, dialog):
        dlg, _req = dialog
        _identify(dlg, MIN_ANCHORS)
        self._exclude(dlg, 1)
        _identify(dlg, 1, start=MIN_ANCHORS)          # _anchors_changed()
        assert not gd.anchors.is_included(dlg._anchors[1])

        dlg.show_failed(_failed('Altair'))
        assert not gd.anchors.is_included(dlg._anchors[1])
        assert 'excluded by you' in dlg._list.item(1).text()
        assert dlg._anchors[2]['state'] == 'suspect'
        assert dlg._list.currentRow() == 2

    def test_result_rows_land_on_their_anchors_around_an_exclusion(self, dialog):
        dlg, _req = dialog
        _identify(dlg, MIN_ANCHORS + 1)
        self._exclude(dlg, 2)                          # Altair, mid-list
        submitted = [n for *_xy, n in gd.anchors.solve_tuples(dlg._anchors)]
        rows = [{'name': n, 'used_as': n, 'residual': 1.0 + i, 'state': 'ok'}
                for i, n in enumerate(submitted)]
        result = dict(_solved(), anchors=rows, n_used=5, n_anchors=5)
        result['predicted'].append({'name': 'Altair', 'x': 700.0, 'y': 700.0})

        dlg.show_solved(result)

        by_name = {a['name']: a for a in dlg._anchors}
        for i, n in enumerate(submitted):
            assert by_name[n]['residual'] == 1.0 + i, n
            assert f"{1.0 + i:.0f} px off" in dlg._list.item(
                [a['name'] for a in dlg._anchors].index(n)).text()
        assert 'residual' not in by_name['Altair']
        assert 'excluded by you' in dlg._list.item(2).text()
        assert 'Altair' in [h.label for h in dlg._predicted], \
            "the solve shows where it puts the excluded star"

    def test_solver_verdicts_are_visible_per_row(self, dialog):
        dlg, _req = dialog
        _identify(dlg, MIN_ANCHORS)
        result = _solved(renamed='Sirius')
        result['anchors'][2].update(residual=None, state='excluded')
        dlg.show_solved(result)

        texts = [dlg._list.item(i).text() for i in range(dlg._list.count())]
        assert texts[0] == 'Vega → Sirius  — 3 px off'
        assert texts[2] == 'Altair  — left out'
        assert texts[1] == 'Deneb  — 3 px off'
        orange = gd.anchors.row_colour({'name': 'x', 'state': 'excluded'})
        assert dlg._list.item(0).foreground().color() == orange
        assert dlg._list.item(2).foreground().color() == orange
        assert dlg._list.item(1).foreground().color() != orange

    def test_remove_still_works_on_an_excluded_row(self, dialog):
        dlg, req = dialog
        _identify(dlg, MIN_ANCHORS)
        self._exclude(dlg, 2)
        dlg._on_remove()
        assert [a['name'] for a in dlg._anchors] == \
            ['Vega', 'Deneb', 'Arcturus', 'Spica']
        assert len(req['hints'][-1]) == 4

    def test_dropping_an_excluded_stars_prediction_points_at_include(self, dialog):
        dlg, req = dialog
        _identify(dlg, MIN_ANCHORS)
        self._exclude(dlg, 0)                          # Vega
        result = _solved()
        result['anchors'] = result['anchors'][1:]
        dlg.show_solved(result)
        assert 'Vega' in [h.label for h in dlg._predicted]

        dlg._on_hint_dropped('Vega', 210.0, 300.0)

        assert req['discard'] == 0 and dlg._state == 'review'
        assert 'Include selected' in dlg._pending_lbl.text()


class TestClosing:

    def test_closing_with_identified_stars_asks_first(self, dialog, monkeypatch):
        dlg, _req = dialog
        _identify(dlg, 2)
        monkeypatch.setattr(dlg, '_confirm_abandon', lambda: False)
        dlg.reject()
        assert dlg.isVisible()
        monkeypatch.setattr(dlg, '_confirm_abandon', lambda: True)
        dlg.reject()
        assert not dlg.isVisible()

    def test_closing_with_nothing_identified_does_not_ask(self, dialog, monkeypatch):
        dlg, _req = dialog
        monkeypatch.setattr(dlg, '_confirm_abandon',
                            lambda: pytest.fail("nothing to lose, nothing to ask"))
        dlg.reject()
        assert not dlg.isVisible()

    def test_closing_during_review_discards_the_held_result(self, dialog, monkeypatch):
        dlg, req = dialog
        _identify(dlg, MIN_ANCHORS)
        dlg.show_solved(_solved())
        monkeypatch.setattr(dlg, '_confirm_abandon', lambda: True)
        dlg.reject()
        assert req['discard'] == 1


# ----------------------------------------------------------------------
# Display stretch slider (discussion #105)
# ----------------------------------------------------------------------

def _star_frame():
    import numpy as np
    rng = np.random.default_rng(1)
    arr = rng.normal(6.0, 1.5, (FRAME, FRAME)).clip(0, 255)
    for _ in range(300):
        y, x = rng.integers(2, FRAME - 2, 2)
        arr[y, x] = rng.uniform(40, 255)
    return Image.fromarray(np.repeat(arr.astype(np.uint8)[..., None], 3, 2))


def test_stretch_slider_is_hidden_without_a_stretch_in_the_prep(dialog):
    dlg, _ = dialog
    assert dlg._stretch_ctl.isHidden()


def test_stretch_slider_rerenders_the_frame_keeping_the_view_and_reports(qapp):
    from services.allsky.display_stretch import DisplayStretch
    prep = _prep()
    prep['image'] = _star_frame()
    stretch = DisplayStretch(prep['image'])
    prep['display_image'] = stretch.render(0.5)
    prep['display_stretch'] = stretch
    prep['display_strength'] = 0.5
    dlg = gd.GuidedCalibrationDialog(prep)
    reported = []
    dlg.display_stretch_changed.connect(reported.append)
    dlg.show()
    qapp.processEvents()
    try:
        assert not dlg._stretch_ctl.isHidden()
        assert dlg._stretch_ctl.slider.value() == 50
        dlg._canvas.zoom_in()
        zoom = dlg._canvas.zoom()
        before = dlg._canvas._full.toImage()

        dlg._stretch_ctl.slider.setValue(90)
        assert reported == []                    # settles first
        assert dlg._stretch_ctl.settling
        dlg._stretch_ctl.settle()                # what the settle timer does
        qapp.processEvents()

        after = dlg._canvas._full.toImage()
        assert after != before
        assert after.size() == before.size()
        assert dlg._canvas.zoom() == pytest.approx(zoom)
        assert reported == [pytest.approx(0.9)]
    finally:
        dlg.close()
