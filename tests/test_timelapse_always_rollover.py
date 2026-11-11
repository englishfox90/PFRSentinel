"""
Always On timelapse rollover — one video per night, split at local noon.

Reported 2026-11-06: an Always On timelapse was cut in two at midnight every
night (timelapse_20261105_2.mp4 ending 00:00:23, timelapse_20261106.mp4 starting
the same second). Midnight is the middle of an observing night; the rollover now
happens at local noon, the same cutoff the image library uses for its nights.
"""
import os
from datetime import date, datetime, timedelta

import pytest

from services.library.sessions import NIGHT_CUTOFF_HOUR
from services.timelapse_window import (
    ALWAYS_ROLLOVER_TIME, observing_night, window_for_day,
)
from tests.test_timelapse_writer import _Clock, _HealthyProcess, _frame, _wire_writer


@pytest.mark.parametrize("moment, night", [
    (datetime(2026, 11, 6, 0, 0, 23), date(2026, 11, 5)),
    (datetime(2026, 11, 6, 11, 59, 59), date(2026, 11, 5)),
    (datetime(2026, 11, 6, 12, 0, 0), date(2026, 11, 6)),
    (datetime(2026, 11, 6, 23, 59, 59), date(2026, 11, 6)),
])
def test_observing_night_runs_noon_to_noon(moment, night):
    assert observing_night(moment) == night


def test_rollover_matches_the_library_night_cutoff():
    assert ALWAYS_ROLLOVER_TIME.hour == NIGHT_CUTOFF_HOUR


def test_always_window_is_noon_to_noon():
    start, end = window_for_day({'window_mode': 'always'}, date(2026, 11, 5))
    assert start == datetime(2026, 11, 5, 12, 0)
    assert end == datetime(2026, 11, 6, 12, 0)


def _feed(writer, when):
    _Clock._now = when
    writer.add_frame(_frame())
    writer.flush()


def test_midnight_does_not_split_an_always_on_night(monkeypatch, temp_dir):
    writer, popen_calls, _ = _wire_writer(
        monkeypatch, temp_dir, _HealthyProcess, datetime(2026, 11, 5, 17, 41))
    start = datetime(2026, 11, 5, 17, 41)
    for minutes in range(0, 14 * 60, 15):       # 17:41 -> 07:26 next morning
        _feed(writer, start + timedelta(minutes=minutes))
    writer.stop()

    assert [os.path.basename(p) for p in popen_calls] == ['timelapse_20261105.mp4']


def test_always_on_rolls_over_at_noon(monkeypatch, temp_dir):
    writer, popen_calls, _ = _wire_writer(
        monkeypatch, temp_dir, _HealthyProcess, datetime(2026, 11, 6, 11, 59))
    _feed(writer, datetime(2026, 11, 6, 11, 59, 30))
    _feed(writer, datetime(2026, 11, 6, 12, 0, 15))
    writer.stop()

    assert [os.path.basename(p) for p in popen_calls] == [
        'timelapse_20261105.mp4', 'timelapse_20261106.mp4',
    ]


def test_always_on_restart_after_midnight_is_named_for_the_night(monkeypatch, temp_dir):
    writer, popen_calls, _ = _wire_writer(
        monkeypatch, temp_dir, _HealthyProcess, datetime(2026, 11, 6, 2, 0))
    _feed(writer, datetime(2026, 11, 6, 2, 0))
    writer.stop()

    assert os.path.basename(popen_calls[0]) == 'timelapse_20261105.mp4'


def test_window_modes_still_name_files_by_calendar_date(monkeypatch, temp_dir):
    writer, _, _ = _wire_writer(
        monkeypatch, temp_dir, _HealthyProcess, datetime(2026, 11, 6, 2, 0),
        window_mode='fixed')
    path = writer._build_output_path(datetime(2026, 11, 6, 2, 0))
    assert os.path.basename(path) == 'timelapse_20261106.mp4'
