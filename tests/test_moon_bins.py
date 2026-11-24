"""moon_bins — the Moon bins the clear-sky baseline is kept in: a thin Moon
is one bin wherever it is, a Moon at least half lit is split low / high at
30° altitude with hysteresis, unknown altitude or site falls back, and the
names version-2 files carry still load."""
import json

import pytest

from services.imaging_conditions import moon_bins as mb
from services.imaging_conditions import star_baseline as sb
from services.imaging_conditions.moon_bins import MoonAltitudeBand, moon_bin


@pytest.mark.parametrize("up, illum, band, expected", [
    (False, 90, 'high', 'moon_down'),
    (None, 90, 'high', 'moon_unknown'),            # no site
    (True, 'x', 'high', 'moon_unknown'),
    (True, 10, 'high', 'moon_up_0_25'),            # a thin Moon is never split
    (True, 10, 'low', 'moon_up_0_25'),
    (True, 40, 'high', 'moon_up_25_50'),
    (True, 50, 'low', 'moon_up_50_75_low'),
    (True, 60, 'high', 'moon_up_50_75_high'),
    (True, 87, 'low', 'moon_up_75_100_low'),
    (True, 100, 'high', 'moon_up_75_100_high'),
    (True, 87, None, 'moon_up_75_100'),            # altitude unknown: un-split bin
    (True, 60, 'sideways', 'moon_up_50_75'),
])
def test_bins(up, illum, band, expected):
    assert moon_bin(up, illum, band) == expected
    assert expected in mb.MOON_BINS


def test_the_bins_stay_few():
    # Every bin has to collect its own clear nights.
    learned_with_a_site = [b for b in mb.MOON_BINS
                           if b not in ('moon_unknown', 'moon_up_50_75', 'moon_up_75_100')]
    assert len(learned_with_a_site) == 7
    assert len(set(mb.MOON_BINS)) == len(mb.MOON_BINS)


def test_band_splits_at_the_line_on_a_first_reading():
    assert MoonAltitudeBand().update(29.9) == 'low'
    assert MoonAltitudeBand().update(30.0) == 'high'
    assert MoonAltitudeBand().update(-5) == 'low'


def test_a_moon_sitting_at_the_line_keeps_its_band():
    band = MoonAltitudeBand()
    assert band.update(28.0) == 'low'
    # Rising through the line: still low until it is clearly past it.
    assert [band.update(a) for a in (29.5, 30.2, 31.0, 32.9)] == ['low'] * 4
    assert band.update(33.0) == 'high'
    # And back: high until clearly under it.
    assert [band.update(a) for a in (31.0, 29.0, 27.0)] == ['high'] * 3
    assert band.update(26.9) == 'low'


def test_unknown_altitude_forgets_the_band():
    band = MoonAltitudeBand()
    band.update(40.0)
    for unknown in (None, 'x', float('nan'), True):
        assert band.update(unknown) is None
    assert band.update(29.0) == 'low'   # a fresh start, not the old 'high'
    band.update(40.0)
    band.reset()
    assert band.update(31.0) == 'high'


def test_version_2_file_with_the_old_bin_names_still_loads(tmp_path):
    path = tmp_path / 'b.json'
    b = sb.StarBaseline(str(path))
    for bin_name in ('moon_down', 'moon_up_50_75', 'moon_up_75_100'):
        for _ in range(30):
            b.add(bin_name, 200, '2026-11-01', 30)
    assert b.save_now()
    assert json.loads(path.read_text())['version'] == sb.FILE_VERSION

    again = sb.StarBaseline(str(path))
    assert again.baseline('moon_down', 30) == 200
    # Kept under the old name (read when the altitude is unknown)...
    assert again.baseline('moon_up_75_100', 30) == 200
    # ...and never handed to the split bins, which learn from their own nights.
    assert again.baseline('moon_up_75_100_high', 30) is None
    assert again.status('moon_up_75_100_low', 30)['state'] == 'learning'


def test_split_bins_are_stored_and_read_back(tmp_path):
    path = tmp_path / 'b.json'
    b = sb.StarBaseline(str(path))
    for _ in range(30):
        b.add('moon_up_75_100_high', 80, '2026-11-01', 30)
        b.add('moon_up_75_100_low', 150, '2026-11-01', 30)
    b.save_now()
    again = sb.StarBaseline(str(path))
    assert again.baseline('moon_up_75_100_high', 30) == 80
    assert again.baseline('moon_up_75_100_low', 30) == 150
