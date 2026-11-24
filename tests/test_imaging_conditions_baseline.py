"""star_baseline + night_series — the clear-sky star count each installation
learns from its own clear NIGHTS: Moon bins, one entry per night, oldest out
by number of nights (never by age), a high percentile across them with the
night in progress taking part, a short clear spell not being a night, FWHM
kept across nights and read from the low side, throttled atomic saves that
carry the night in progress, a stamp that starts over on another camera /
frame size / lens, reset removes the file, and garbage / oversized / foreign
/ version-1 files load as "learning" without raising."""
import json
import threading
from datetime import datetime, timedelta, timezone

import pytest

from services.imaging_conditions import night_series as ns
from services.imaging_conditions import star_baseline as sb
from services.imaging_conditions.night_series import NightSamples, NightSeries, night_key
from services.imaging_conditions.star_baseline import StarBaseline, moon_bin

N1, N2, N3 = '2026-11-01', '2026-11-02', '2026-11-03'


class Clock:
    t = 0.0

    def __call__(self):
        return self.t


def night(b, date, count, frames=30, bin_name='moon_down', fwhm=None, min_frames=30):
    for _ in range(frames):
        b.add(bin_name, count, date, min_frames)
        if fwhm is not None:
            b.add_fwhm(fwhm, date, min_frames)


@pytest.mark.parametrize("up, illum, expected", [
    (False, 90, 'moon_down'), (True, 10, 'moon_up_0_25'), (True, 25, 'moon_up_25_50'),
    (True, 60, 'moon_up_50_75'), (True, 100, 'moon_up_75_100'), (None, 50, 'moon_unknown'),
    (True, 'x', 'moon_unknown'),
])
def test_moon_bins(up, illum, expected):
    assert moon_bin(up, illum) == expected


def test_no_baseline_ships_with_the_app():
    from services.config_defaults import DEFAULT_CONFIG
    block = DEFAULT_CONFIG['imaging_conditions']
    # Every star threshold is relative; nothing in the block is a star count.
    assert not any('count' in k or 'detections' in k for k in block)


def test_the_night_is_the_apps_noon_to_noon_night():
    from services.timelapse_window import observing_night
    evening = datetime(2026, 11, 6, 22, 0)
    assert night_key(evening) == '2026-11-06'
    assert night_key(datetime(2026, 11, 7, 2, 0)) == '2026-11-06'    # not split at midnight
    assert night_key(datetime(2026, 11, 7, 11, 59)) == '2026-11-06'
    assert night_key(datetime(2026, 11, 7, 12, 0)) == '2026-11-07'
    aware = datetime(2026, 11, 7, 2, 0).astimezone()
    assert night_key(aware.astimezone(timezone.utc)) == '2026-11-06'  # read in local time
    assert night_key(evening) == observing_night(evening).isoformat()


def test_first_night_is_a_running_median(tmp_path):
    b = StarBaseline(str(tmp_path / 'b.json'))
    for c in (100, 300, 200):
        b.add('moon_down', c, N1, 4)
    assert b.baseline('moon_down', 4) is None
    assert b.status('moon_down', 4, N1) == {'state': 'learning', 'samples': 3, 'needed': 4,
                                            'bin': 'moon_down', 'nights': 0}
    b.add('moon_down', 250, N1, 4)
    assert b.baseline('moon_down', 4) == 225.0
    assert b.status('moon_down', 4, N1) == {'state': 'ready', 'samples': 4, 'needed': 4,
                                            'bin': 'moon_down', 'nights': 1}
    assert b.baseline('moon_up_0_25', 1) is None


def test_high_percentile_across_nights_and_a_hazy_night_barely_moves_it(tmp_path):
    b = StarBaseline(str(tmp_path / 'b.json'))
    for i, count in enumerate((300, 310, 320, 330)):
        night(b, f'2026-11-0{i + 1}', count)
    night(b, '2026-11-05', 300, frames=1)    # closes the 4th night
    assert [n['median'] for n in b.nights('moon_down')] == [300, 310, 320, 330]
    assert b.nights('moon_down')[0] == {'date': '2026-11-01', 'median': 300, 'frames': 30}
    clear = b.baseline('moon_down', 30)
    assert clear == 322.5                     # 75th percentile, above the median 315
    night(b, '2026-11-06', 150)               # a hazy night the ML still called Clear
    night(b, '2026-11-07', 150, frames=1)
    assert b.baseline('moon_down', 30) == 320.0
    # The old 240-frame median would have become 150 after that night.
    status = b.status('moon_down', 30, '2026-11-07')
    assert status['state'] == 'ready' and status['nights'] == 5 and status['samples'] == 150


def test_tonight_takes_part_once_it_has_enough_frames(tmp_path):
    b = StarBaseline(str(tmp_path / 'b.json'))
    night(b, N1, 300)
    night(b, N2, 400, frames=29)
    assert b.baseline('moon_down', 30) == 300.0
    b.add('moon_down', 400, N2)
    assert b.baseline('moon_down', 30) == 375.0   # 75th percentile of 300 and 400


def test_a_short_clear_spell_is_not_a_night(tmp_path):
    b = StarBaseline(str(tmp_path / 'b.json'))
    night(b, N1, 300)
    night(b, N2, 900, frames=20)     # ten clear minutes between clouds
    assert b.status('moon_down', 30, N2)['samples'] == 30    # ready on night 1 alone
    night(b, N3, 310, frames=1)
    assert [n['date'] for n in b.nights('moon_down')] == [N1]
    assert b.baseline('moon_down', 30) == 300.0


def test_leftover_frames_of_another_night_are_not_tonights_progress(tmp_path):
    b = StarBaseline(str(tmp_path / 'b.json'))
    night(b, N1, 300, frames=12)
    assert b.status('moon_down', 30, N1)['samples'] == 12
    assert b.status('moon_down', 30, N2)['samples'] == 0


def test_oldest_out_by_number_of_nights_never_by_age(tmp_path):
    b = StarBaseline(str(tmp_path / 'b.json'))
    start = datetime(2020, 1, 1)
    total = sb.MAX_NIGHTS + 5
    for i in range(total):
        night(b, (start + timedelta(days=40 * i)).date().isoformat(), 100 + i, frames=30)
    night(b, '2031-01-01', 500, frames=1)
    kept = b.nights('moon_down')
    assert len(kept) == sb.MAX_NIGHTS
    assert kept[0]['median'] == 105 and kept[-1]['median'] == 100 + total - 1
    # Years between the stored nights and "now": nothing has aged out.
    assert b.status('moon_down', 30, '2031-01-01')['nights'] == sb.MAX_NIGHTS


def test_bins_are_independent_and_bad_samples_are_refused(tmp_path):
    b = StarBaseline(str(tmp_path / 'b.json'))
    night(b, N1, 300, frames=3, min_frames=3)
    night(b, N1, 80, frames=3, bin_name='moon_up_75_100', min_frames=3)
    for bad in (-1, True, None, 'x', 10**9, float('nan'), float('inf')):
        b.add('moon_down', bad, N1)
    b.add('not_a_bin', 5, N1)
    b.add('moon_down', 5, 'not a date')
    assert b.sample_count('moon_down') == 3
    assert b.baseline('moon_down', 3) == 300.0 and b.baseline('moon_up_75_100', 3) == 80.0


def test_a_long_night_is_thinned_evenly_and_bounded():
    tonight = NightSamples(N1, max_samples=100)
    for i in range(10_000):
        tonight.add(i)
    assert tonight.frames == 10_000 and len(tonight.samples) <= 100
    assert tonight.samples[0] == 0 and tonight.samples[-1] > 9_000
    assert abs(tonight.median() - 5_000) < 200      # of the whole night, not its end


def test_fwhm_reference_is_the_low_side_across_nights(tmp_path):
    b = StarBaseline(str(tmp_path / 'b.json'))
    assert b.fwhm_reference(30) == (None, 0)
    for i, fwhm in enumerate((2.4, 2.8, 3.2, 3.6)):
        night(b, f'2026-11-0{i + 1}', 300, fwhm=fwhm)
    night(b, '2026-11-05', 300, frames=1, fwhm=2.4)
    px, nights = b.fwhm_reference(30)
    assert nights == 4 and px == pytest.approx(2.7)   # 25th percentile, under the median 3.0
    for bad in (0, -1, 500, None, 'x', True):
        b.add_fwhm(bad, '2026-11-05')
    assert b.fwhm_reference(30) == (px, 4)


def test_round_trip_keeps_the_night_in_progress_and_saves_are_throttled(tmp_path):
    clock = Clock()
    path = tmp_path / 'sub' / 'b.json'
    b = StarBaseline(str(path), clock=clock, save_interval_s=600)
    b.ensure_stamp({'camera': 'cam', 'width': 100, 'height': 100, 'lens_a1': None})
    night(b, N1, 300, fwhm=3.0)
    night(b, N2, 310, frames=12, fwhm=2.5)
    assert b.maybe_save() is True
    b.add('moon_down', 310, N2)
    assert b.maybe_save() is False        # throttled
    clock.t += 601
    assert b.maybe_save() is True
    data = json.loads(path.read_text())
    assert data['version'] == 2 and set(data) == {'version', 'stamp', 'stars', 'fwhm'}
    assert data['stars']['moon_down']['nights'] == [{'date': N1, 'median': 300, 'frames': 30}]
    assert data['stars']['moon_down']['tonight']['frames'] == 13
    again = StarBaseline(str(path))
    assert again.nights('moon_down') == b.nights('moon_down')
    assert again.sample_count('moon_down') == 13          # a restart mid-night loses nothing
    assert again.fwhm_reference(30) == (3.0, 1)
    assert again.ensure_stamp({'camera': 'cam', 'width': 100, 'height': 100,
                               'lens_a1': None}) is False
    for _ in range(17):
        again.add('moon_down', 310, N2)
    assert again.status('moon_down', 30, N2)['nights'] == 2
    assert not list((tmp_path / 'sub').glob('*.tmp'))


def test_stamp_change_starts_over(tmp_path):
    b = StarBaseline(str(tmp_path / 'b.json'))
    stamp = {'camera': 'cam', 'width': 100, 'height': 100, 'lens_a1': 1000.0}
    assert b.ensure_stamp(stamp) is False
    night(b, N1, 300, fwhm=3.0)
    b.add('moon_down', 300, N2)
    assert b.ensure_stamp(dict(stamp, lens_a1=1030.0)) is False   # 3 %: a refinement
    assert b.ensure_stamp(dict(stamp, lens_a1=None)) is False     # calibration reset
    assert b.sample_count('moon_down') == 1 and len(b.nights('moon_down')) == 1
    assert b.ensure_stamp(dict(stamp, lens_a1=1200.0)) is True
    assert b.sample_count('moon_down') == 0 and b.nights('moon_down') == []
    assert b.baseline('moon_down', 1) is None and b.fwhm_reference(1) == (None, 0)


def test_reset_removes_the_file(tmp_path):
    path = tmp_path / 'b.json'
    b = StarBaseline(str(path))
    night(b, N1, 300, fwhm=3.0)
    b.save_now()
    assert path.is_file()
    b.reset()
    assert not path.exists() and b.sample_count('moon_down') == 0
    assert b.baseline('moon_down', 1) is None and b.fwhm_reference(1) == (None, 0)
    b.reset()   # nothing there: no error


V1_FILE = json.dumps({'version': 1, 'stamp': None,
                      'bins': {'moon_down': [300] * 240}}).encode()


@pytest.mark.parametrize("content", [
    b'{truncated', b'[]', b'{"version": 99, "stars": {}}', b'{"version": 2, "stars": []}',
    b'{"version": 2, "stars": {"moon_down": "abc", "evil": {"nights": []}}, "stamp": "x"}',
    b'{"version": 2, "stars": {"moon_down": {"nights": "x", "tonight": [1]}}, "fwhm": 7}',
    b'\xff\xfe garbage', V1_FILE,
])
def test_garbage_and_old_version_files_load_as_learning(tmp_path, content):
    path = tmp_path / 'b.json'
    path.write_bytes(content)
    b = StarBaseline(str(path))
    assert b.baseline('moon_down', 1) is None
    assert b.status('moon_down', 30)['state'] == 'learning'
    assert b.fwhm_reference(1) == (None, 0)
    b.add('moon_down', 5, N1)
    assert b.sample_count('moon_down') == 1


def test_mixed_file_keeps_only_what_is_valid(tmp_path):
    path = tmp_path / 'b.json'
    good = {'date': N1, 'median': 300.0, 'frames': 40}
    path.write_text(json.dumps({'version': 2, 'stars': {
        'moon_down': {
            'nights': [good, dict(good), {'date': 'x', 'median': 1, 'frames': 40},
                       {'date': N2, 'median': -5, 'frames': 40},
                       {'date': N3, 'median': 310, 'frames': True}, 'junk'],
            'tonight': {'date': N3, 'frames': 2, 'stride': 3,
                        'samples': [100, 'x', -5, 200, True, 300]}},
        'moon_up_0_25': {'nights': [], 'tonight': None},
        'evil': {'nights': [good]}},
        'fwhm': {'nights': [{'date': N1, 'median': 999, 'frames': 40}],
                 'tonight': {'date': 'nope', 'samples': [3.0]}}}))
    bins, fwhm, stamp = sb.load_file(str(path))
    assert set(bins) == {'moon_down'} and stamp is None and fwhm.is_empty()
    series = bins['moon_down']
    assert series.nights == [good]
    assert series.tonight.samples == [100, 200, 300]
    assert series.tonight.frames == 3 and series.tonight.stride == 1


def test_oversized_file_is_ignored(tmp_path, monkeypatch):
    path = tmp_path / 'b.json'
    b = StarBaseline(str(path))
    night(b, N1, 300)
    b.save_now()
    monkeypatch.setattr(sb, 'MAX_FILE_BYTES', 50)
    assert sb.load_file(str(path))[0] == {}


def test_a_full_store_stays_under_the_file_cap(tmp_path):
    path = tmp_path / 'b.json'
    b = StarBaseline(str(path))
    start = datetime(2026, 1, 1)
    for bin_name in sb.MOON_BINS:
        for i in range(sb.MAX_NIGHTS):
            night(b, (start + timedelta(days=i)).date().isoformat(), 12345, frames=30,
                  bin_name=bin_name, fwhm=12.34)
        night(b, '2027-01-01', 12345, frames=3 * sb.MAX_NIGHT_SAMPLES, bin_name=bin_name,
              fwhm=12.34)
    assert b.save_now() is True
    assert path.stat().st_size < sb.MAX_FILE_BYTES // 2
    assert len(StarBaseline(str(path)).nights('moon_unknown')) == sb.MAX_NIGHTS


def test_failed_write_keeps_samples_dirty(tmp_path, monkeypatch):
    b = StarBaseline(str(tmp_path / 'b.json'))
    b.add('moon_down', 1, N1)
    monkeypatch.setattr(sb.os, 'replace', lambda *a: (_ for _ in ()).throw(OSError('disk')))
    assert b.save_now() is False
    monkeypatch.undo()
    assert b.save_now() is True


def test_concurrent_adds_and_saves(tmp_path):
    b = StarBaseline(str(tmp_path / 'b.json'), save_interval_s=0)

    def work(n):
        for i in range(200):
            b.add('moon_down', i, N1 if (i + n) % 50 else N2, 5)
            b.add_fwhm(3.0, N1)
            b.maybe_save()
            b.status('moon_down', 5, N1)

    threads = [threading.Thread(target=work, args=(n,)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    b.save_now()
    bins, fwhm, _ = sb.load_file(b.path)
    assert bins['moon_down'].tonight is not None and fwhm.tonight.frames == 800


def test_same_date_twice_is_one_night():
    series = NightSeries(0, 1000)
    for date, count, frames in ((N1, 300, 40), (N2, 310, 30), (N1, 200, 30), (N3, 1, 1)):
        for _ in range(frames):
            series.add(date, count)
    assert [(n['date'], n['median']) for n in series.nights] == [(N1, 300), (N2, 310)]
    assert ns.percentile([], 75) is None and ns.percentile([4], 75) == 4.0
