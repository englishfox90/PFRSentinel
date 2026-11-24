"""The grade a frame got when it was taken, carried to the image library.

``ImagingConditionsFeed.observe`` returns the full record it published for a
frame. The library keeps a small summary of it per archived frame, so a
night can be read back later ("why did NINA pause at 02:10?"). The summary
rides on the frame's metadata under ``GRADE_KEY``. The key is lower case on
purpose: overlay tokens are looked up in upper case, so it can never be one.

Nothing here judges a frame. A grade is written once, from the record of
the capture, and a reprocess of the same capture keeps it.
"""
import threading
from typing import Optional

GRADE_KEY = '_imaging_grade'

# Library columns, in the order they are stored. All NULL = not graded.
GRADE_COLUMNS = ('imaging_ok', 'frame_ok', 'imaging_reasons',
                 'clear_sky_ratio', 'moon_bin')

_REASON_SEP = ','
_MAX_REASONS = 8

_last_lock = threading.Lock()
_last: Optional[tuple] = None  # (capture identity, summary) of the last judged frame


def summarize_record(record) -> Optional[dict]:
    """The JSON-safe summary of one feed record, or None when there is none."""
    if not isinstance(record, dict) or not isinstance(record.get('imaging_ok'), bool):
        return None
    frame_ok = record.get('frame_ok')
    ratio = (record.get('stars') or {}).get('ratio')
    moon_bin = record.get('baseline_bin')
    return {
        'imaging_ok': record['imaging_ok'],
        'frame_ok': frame_ok if isinstance(frame_ok, bool) else None,
        'reasons': [str(r) for r in (record.get('reasons') or [])][:_MAX_REASONS],
        'clear_sky_ratio': _number(ratio),
        'moon_bin': moon_bin if isinstance(moon_bin, str) and moon_bin else None,
    }


def stamp_frame_grade(metadata: dict, record) -> None:
    """Leave the grade of this capture on its metadata.

    ``record`` is what the feed returned: None when imaging conditions are
    off (the frame is then ungraded and any stale grade is removed) and None
    for a reprocess, which is not judged — a reprocess keeps the grade
    already on the metadata, or gets back the one its capture was given.
    """
    global _last
    from services.observing_window import SAME_CAPTURE_KEY
    identity = _capture_identity(metadata)
    if metadata.get(SAME_CAPTURE_KEY):
        if GRADE_KEY not in metadata and identity is not None:
            with _last_lock:
                last = _last
            if last is not None and last[0] == identity:
                metadata[GRADE_KEY] = dict(last[1])
        return
    summary = summarize_record(record)
    with _last_lock:
        _last = (identity, summary) if summary is not None and identity is not None else None
    if summary is None:
        metadata.pop(GRADE_KEY, None)
    else:
        metadata[GRADE_KEY] = summary


def forget_last_grade() -> None:
    """Drop the remembered capture (tests)."""
    global _last
    with _last_lock:
        _last = None


def grade_columns(metadata) -> dict:
    """The library's grade columns for a frame's metadata; all None when the
    frame carries no grade."""
    grade = (metadata or {}).get(GRADE_KEY)
    if not isinstance(grade, dict) or not isinstance(grade.get('imaging_ok'), bool):
        return dict.fromkeys(GRADE_COLUMNS)
    frame_ok = grade.get('frame_ok')
    return {
        'imaging_ok': int(grade['imaging_ok']),
        'frame_ok': int(frame_ok) if isinstance(frame_ok, bool) else None,
        'imaging_reasons': join_reasons(grade.get('reasons')),
        'clear_sky_ratio': _number(grade.get('clear_sky_ratio')),
        'moon_bin': grade.get('moon_bin') if isinstance(grade.get('moon_bin'), str) else None,
    }


def join_reasons(reasons) -> str:
    return _REASON_SEP.join(
        str(r).strip() for r in (reasons or []) if str(r).strip())


def split_reasons(text) -> list:
    if not text:
        return []
    return [part.strip() for part in str(text).split(_REASON_SEP) if part.strip()]


def is_graded(row) -> bool:
    """Whether a library row was graded when it was captured."""
    return isinstance(row, dict) and row.get('imaging_ok') is not None


def _capture_identity(metadata):
    name, stamp = metadata.get('FILENAME'), metadata.get('DATETIME')
    if name is None and stamp is None:
        return None
    return (str(name), str(stamp))


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if value != value or value in (float('inf'), float('-inf')):
        return None
    return round(float(value), 3)
