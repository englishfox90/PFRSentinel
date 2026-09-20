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

    def test_adjust_stars_discards_the_result_and_returns_to_picking(self, dialog):
        dlg, req = dialog
        _identify(dlg, MIN_ANCHORS)
        dlg.show_solved(_solved())
        dlg._on_back()
        assert req['discard'] == 1
        assert dlg._predicted == []
        assert dlg._solve_btn.text().startswith("Solve")
        assert len(dlg._anchors) == MIN_ANCHORS


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
