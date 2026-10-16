"""
Anchor rows for the guided-calibration dialog: what each identified star
says in the list and on the canvas, and the bookkeeping behind it.

An anchor is a dict {px, py, ra, dec, name, snapped} the dialog owns. Two
kinds of mark are laid over it and must never be confused:

- ``user_excluded``: the user took the star out of the solve without
  deleting it (issue #124: the only way to test a doubtful star used to be
  to remove it). It is the user's decision, so it survives every edit and
  every solve result — only the user puts the star back.
- ``state`` / ``residual`` / ``used_as``: the last solve's verdict on the
  star, cleared the moment the anchor set changes.

Solve results come back as rows keyed by the name the anchor was submitted
under. Names are unique in the dialog (each star can be identified once),
so rows are matched to anchors by name — never by position, which an
excluded anchor in the middle of the list would shift.
"""
from typing import List, Optional

from PySide6.QtGui import QColor

USER_EXCLUDED = 'user_excluded'
_SOLVE_MARKS = ('state', 'residual', 'used_as')

_ROW_COLOURS = {'suspect': QColor(255, 107, 107),
                'excluded': QColor(255, 160, 60),
                'renamed': QColor(255, 160, 60)}


def is_included(a: dict) -> bool:
    return not a.get(USER_EXCLUDED, False)


def included(anchors: List[dict]) -> List[dict]:
    return [a for a in anchors if is_included(a)]


def solve_tuples(anchors: List[dict]) -> list:
    """What a solve or a hint request is given: the included anchors, in
    list order, as (px, py, ra_deg, dec_deg, name)."""
    return [(a['px'], a['py'], a['ra'], a['dec'], a['name'])
            for a in included(anchors)]


def toggle_excluded(a: dict) -> None:
    a[USER_EXCLUDED] = is_included(a)


def clear_solve_marks(anchors: List[dict]) -> None:
    """The anchor set changed: old residuals no longer describe it."""
    for a in anchors:
        for key in _SOLVE_MARKS:
            a.pop(key, None)


def apply_result_rows(anchors: List[dict], rows: List[dict]) -> None:
    """Lay a solve's per-anchor rows {name, residual, state[, used_as]} over
    the anchors that were submitted; the user's exclusions are untouched."""
    by_name = {r['name']: r for r in rows}
    for a in included(anchors):
        r = by_name.get(a['name'])
        a['state'] = r['state'] if r else 'ok'
        a['residual'] = r['residual'] if r else None
        a['used_as'] = r.get('used_as', a['name']) if r else a['name']


def label(a: dict) -> str:
    used_as = a.get('used_as') or a['name']
    return a['name'] if used_as == a['name'] else f"{a['name']} → {used_as}"


def marker_state(a: dict) -> str:
    """The StarPickCanvas marker state: 'ok' | 'suspect' | 'excluded' |
    'renamed'. A star the user excluded wears the solver's exclusion
    colour; the row text tells the two apart."""
    if not is_included(a):
        return 'excluded'
    return a.get('state') or 'ok'


def row_colour(a: dict) -> Optional[QColor]:
    return _ROW_COLOURS.get(marker_state(a))


def row_text(a: dict) -> str:
    text = label(a)
    if not is_included(a):
        return f"{text}  — excluded by you"
    if a.get('state') == 'excluded':
        return f"{text}  — left out"
    residual = a.get('residual')
    if residual is not None:
        off = "off image" if residual == float('inf') else f"{residual:.0f} px off"
        return f"{text}  — {off}"
    return f"{text}  {'✓' if a.get('snapped') else '⚠ unsnapped'}"
