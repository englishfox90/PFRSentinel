"""A night's imaging-conditions grades laid out for the scrubber.

Read-side arithmetic over the night's rows (already in memory — nothing
here touches the index): per frame OK / not OK / not graded, merged into
runs, and the clear-sky ratio as a trace. Everything is in the scrubber's
frame-index space (every frame an equal slice), so the lane lines up with
the filmstrip, the condition band and the playhead.
"""
from ..imaging_conditions.frame_grade import is_graded

GRADE_OK = 'ok'
GRADE_NOT_OK = 'not_ok'
GRADE_UNGRADED = 'ungraded'

# The trace's scale tops out here: a night clearer than the baseline still
# plots, without one bright frame flattening everything else.
RATIO_CEILING = 1.5


def frame_grade_state(row) -> str:
    if not is_graded(row):
        return GRADE_UNGRADED
    return GRADE_OK if row.get('imaging_ok') else GRADE_NOT_OK


def _ratio(row):
    value = row.get('clear_sky_ratio') if is_graded(row) else None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if value != value or value < 0:
        return None
    return min(float(value), RATIO_CEILING)


def build_grade_timeline(rows) -> dict:
    """``{graded, states, segments, ratios, runs, scale_max}`` for one night.

    ``segments``: ``(frac0, frac1, state)`` runs with equal neighbours merged.
    ``ratios``: per frame, the clear-sky ratio or None. ``runs``: lists of
    consecutive ``(index, ratio)`` — the trace breaks where the ratio was
    unknown instead of drawing a line across frames nobody measured.
    ``graded`` is False for a night with no graded frame; the lane is then
    not drawn at all.
    """
    rows = rows or []
    n = len(rows)
    states = [frame_grade_state(r) for r in rows]
    ratios = [_ratio(r) for r in rows]

    segments = []
    run_state, run_start = None, 0.0
    for i, state in enumerate(states):
        if state != run_state:
            left = 0.0 if i == 0 or n == 1 else (i - 0.5) / (n - 1)
            if run_state is not None:
                segments.append((run_start, left, run_state))
            run_state, run_start = state, left
    if run_state is not None:
        segments.append((run_start, 1.0, run_state))

    runs, current = [], []
    for i, ratio in enumerate(ratios):
        if ratio is None:
            if current:
                runs.append(current)
                current = []
        else:
            current.append((i, ratio))
    if current:
        runs.append(current)

    known = [r for r in ratios if r is not None]
    return {
        'graded': any(s != GRADE_UNGRADED for s in states),
        'states': states,
        'segments': segments,
        'ratios': ratios,
        'runs': runs,
        'scale_max': max(1.0, max(known)) if known else 1.0,
    }
