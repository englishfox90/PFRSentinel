"""Per-night summary of the imaging-conditions grades stored with each frame.

Read-side arithmetic over index rows: how many frames were graded, what
share of them were OK for imaging, and the reason that most often stood in
the way. Ungraded rows (``imaging_ok`` NULL — conditions were off, or the
frame predates the grade) are left out of every figure.
"""
from collections import Counter

from ..imaging_conditions.frame_grade import is_graded, split_reasons

# Not causes: the verdict was waiting for a run of good frames. They only
# name the night when no frame carried a real cause.
_HOLDING = ('warming_up', 'recovering')


def summarize_grades(rows) -> dict:
    """``{graded_count, ok_count, ok_pct, top_reason, top_reason_count}``.

    ``ok_pct`` is None when nothing was graded. ``top_reason`` is the reason
    code heading the most not-OK frames, or None when every graded frame
    was OK.
    """
    graded = [r for r in rows if is_graded(r)]
    ok = sum(1 for r in graded if r.get('imaging_ok'))
    causes, holding = Counter(), Counter()
    for r in graded:
        if r.get('imaging_ok'):
            continue
        reasons = split_reasons(r.get('imaging_reasons'))
        if not reasons:
            continue
        (holding if reasons[0] in _HOLDING else causes)[reasons[0]] += 1
    top, top_count = None, 0
    for counter in (causes, holding):
        if counter:
            # most_common is stable, so a tie goes to the reason seen first.
            top, top_count = counter.most_common(1)[0]
            break
    return {
        'graded_count': len(graded),
        'ok_count': ok,
        'ok_pct': round(100 * ok / len(graded)) if graded else None,
        'top_reason': top,
        'top_reason_count': top_count,
    }
