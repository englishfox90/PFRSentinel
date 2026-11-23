"""star_baseline — the clear-sky star count each installation learns from its
own frames: Moon bins, median once enough samples, bounded per bin, throttled
atomic saves, a stamp that starts over on another camera / frame size / lens,
reset removes the file, and garbage / oversized / foreign files load as
"learning" without raising."""
import json
import threading

import pytest

from services.imaging_conditions import star_baseline as sb
from services.imaging_conditions.star_baseline import StarBaseline, moon_bin


class Clock:
    t = 0.0

    def __call__(self):
        return self.t


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


def test_median_once_enough_samples(tmp_path):
    b = StarBaseline(str(tmp_path / 'b.json'))
    for c in (100, 300, 200):
        b.add('moon_down', c)
    assert b.baseline('moon_down', 4) is None
    assert b.status('moon_down', 4) == {'state': 'learning', 'samples': 3, 'needed': 4,
                                        'bin': 'moon_down'}
    b.add('moon_down', 250)
    assert b.baseline('moon_down', 4) == 225.0
    assert b.status('moon_down', 4)['state'] == 'ready'
    assert b.baseline('moon_up_0_25', 1) is None


def test_bounded_per_bin_and_rejects_bad_samples(tmp_path):
    b = StarBaseline(str(tmp_path / 'b.json'), max_samples=5)
    for c in range(20):
        b.add('moon_down', c)
    for bad in (-1, 1.5, True, None, 10**9):
        b.add('moon_down', bad)
    b.add('not_a_bin', 5)
    assert b.sample_count('moon_down') == 5 and b.baseline('moon_down', 1) == 17.0


def test_round_trip_and_throttled_saves(tmp_path):
    clock = Clock()
    path = tmp_path / 'sub' / 'b.json'
    b = StarBaseline(str(path), clock=clock, save_interval_s=600)
    b.ensure_stamp({'camera': 'cam', 'width': 100, 'height': 100, 'lens_a1': None})
    b.add('moon_down', 300)
    assert b.maybe_save() is True
    b.add('moon_down', 310)
    assert b.maybe_save() is False        # throttled
    clock.t += 601
    assert b.maybe_save() is True
    again = StarBaseline(str(path))
    assert again.sample_count('moon_down') == 2
    assert again.ensure_stamp({'camera': 'cam', 'width': 100, 'height': 100,
                               'lens_a1': None}) is False
    assert not list((tmp_path / 'sub').glob('*.tmp'))


def test_stamp_change_starts_over(tmp_path):
    b = StarBaseline(str(tmp_path / 'b.json'))
    stamp = {'camera': 'cam', 'width': 100, 'height': 100, 'lens_a1': 1000.0}
    assert b.ensure_stamp(stamp) is False
    b.add('moon_down', 300)
    assert b.ensure_stamp(dict(stamp, lens_a1=1030.0)) is False   # 3 %: a refinement
    assert b.ensure_stamp(dict(stamp, lens_a1=None)) is False     # calibration reset
    assert b.sample_count('moon_down') == 1
    assert b.ensure_stamp(dict(stamp, lens_a1=1200.0)) is True
    assert b.sample_count('moon_down') == 0


def test_reset_removes_the_file(tmp_path):
    path = tmp_path / 'b.json'
    b = StarBaseline(str(path))
    b.add('moon_down', 300)
    b.save_now()
    assert path.is_file()
    b.reset()
    assert not path.exists() and b.sample_count('moon_down') == 0
    b.reset()   # nothing there: no error


@pytest.mark.parametrize("content", [
    b'{truncated', b'[]', b'{"version": 99, "bins": {}}', b'{"version": 1, "bins": []}',
    b'{"version": 1, "bins": {"moon_down": "abc", "evil": [1,2]}, "stamp": "x"}',
    b'\xff\xfe garbage',
])
def test_garbage_files_load_as_learning(tmp_path, content):
    path = tmp_path / 'b.json'
    path.write_bytes(content)
    b = StarBaseline(str(path))
    assert b.baseline('moon_down', 1) is None
    b.add('moon_down', 5)
    assert b.sample_count('moon_down') == 1


def test_mixed_file_keeps_only_valid_samples(tmp_path):
    path = tmp_path / 'b.json'
    path.write_text(json.dumps({'version': 1, 'bins': {
        'moon_down': [100, 'x', -5, 200, True, 300], 'moon_up_0_25': [], 'evil': [1]}}))
    assert sb.load_bins(str(path)) == {'moon_down': [100, 200, 300]}


def test_oversized_file_is_ignored(tmp_path, monkeypatch):
    path = tmp_path / 'b.json'
    path.write_text(json.dumps({'version': 1, 'bins': {'moon_down': [1] * 100}}))
    monkeypatch.setattr(sb, 'MAX_FILE_BYTES', 50)
    assert sb.load_bins(str(path)) == {}


def test_failed_write_keeps_samples_dirty(tmp_path, monkeypatch):
    b = StarBaseline(str(tmp_path / 'b.json'))
    b.add('moon_down', 1)
    monkeypatch.setattr(sb.os, 'replace', lambda *a: (_ for _ in ()).throw(OSError('disk')))
    assert b.save_now() is False
    monkeypatch.undo()
    assert b.save_now() is True


def test_concurrent_adds_and_saves(tmp_path):
    b = StarBaseline(str(tmp_path / 'b.json'), save_interval_s=0)

    def work():
        for i in range(200):
            b.add('moon_down', i)
            b.maybe_save()

    threads = [threading.Thread(target=work) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert b.sample_count('moon_down') == sb.MAX_SAMPLES
    assert len(sb.load_bins(b.path)['moon_down']) <= sb.MAX_SAMPLES

