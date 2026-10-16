"""
Tests for ui/panels/allsky_guided_anchors.py (issue #124): the user's own
exclusions against the solver's verdicts, and result rows matched to the
anchors by name rather than by position.
"""
import pytest

pytest.importorskip("PySide6")

from ui.panels import allsky_guided_anchors as anchors


def _anchor(name, **extra):
    return {'px': 1.0, 'py': 2.0, 'ra': 3.0, 'dec': 4.0, 'name': name,
            'snapped': True, **extra}


def test_solve_tuples_skip_the_users_exclusions_in_order():
    rows = [_anchor('Vega'), _anchor('Deneb'), _anchor('Altair')]
    anchors.toggle_excluded(rows[1])
    assert [t[4] for t in anchors.solve_tuples(rows)] == ['Vega', 'Altair']
    anchors.toggle_excluded(rows[1])
    assert [t[4] for t in anchors.solve_tuples(rows)] == ['Vega', 'Deneb', 'Altair']


def test_result_rows_match_by_name_around_an_exclusion():
    rows = [_anchor('Vega'), _anchor('Deneb'), _anchor('Altair')]
    anchors.toggle_excluded(rows[1])
    anchors.apply_result_rows(rows, [
        {'name': 'Vega', 'used_as': 'Vega', 'residual': 2.0, 'state': 'ok'},
        {'name': 'Altair', 'used_as': 'Altair', 'residual': 9.0, 'state': 'ok'}])
    assert rows[0]['residual'] == 2.0
    assert rows[2]['residual'] == 9.0, "not shifted onto the excluded slot"
    assert 'residual' not in rows[1] and not anchors.is_included(rows[1])


def test_clearing_solve_marks_keeps_the_users_exclusion():
    a = _anchor('Vega', state='suspect', residual=140.0, used_as='Vega')
    anchors.toggle_excluded(a)
    anchors.clear_solve_marks([a])
    assert not anchors.is_included(a)
    assert not any(k in a for k in ('state', 'residual', 'used_as'))


@pytest.mark.parametrize("anchor, text, state", [
    (_anchor('Vega'), 'Vega  ✓', 'ok'),
    (_anchor('Vega', snapped=False), 'Vega  ⚠ unsnapped', 'ok'),
    (_anchor('Vega', user_excluded=True), 'Vega  — excluded by you', 'excluded'),
    (_anchor('Vega', state='excluded', residual=None, used_as='Vega'),
     'Vega  — left out', 'excluded'),
    (_anchor('Vega', state='renamed', residual=3.4, used_as='Sirius'),
     'Vega → Sirius  — 3 px off', 'renamed'),
    (_anchor('Vega', state='suspect', residual=float('inf'), used_as='Vega'),
     'Vega  — off image', 'suspect'),
    (_anchor('Vega', state='ok', residual=2.6, used_as='Vega'),
     'Vega  — 3 px off', 'ok'),
])
def test_row_text_and_marker_state_per_verdict(anchor, text, state):
    assert anchors.row_text(anchor) == text
    assert anchors.marker_state(anchor) == state
    assert (anchors.row_colour(anchor) is None) == (state == 'ok')
