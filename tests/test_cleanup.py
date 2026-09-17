"""
Tests for services.cleanup — the disk-safety invariant first.

This module runs unattended on the primary capture paths (services/watcher.py,
services/headless_runner.py) against the user's watch directory. The rule in
.claude/rules/services.md is "files only — never delete folders"; these tests
hold the code to it literally rather than trusting that os.walk happens to
classify things the way we expect.

Everything runs under tmp_path. A test in this file that writes outside
tmp_path is itself a defect.
"""
import os
import re
import sys

import pytest

project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

import services.cleanup as cleanup
from services.cleanup import (
    delete_oldest_files, delete_oldest_sessions, get_directory_size,
    get_session_folders, run_cleanup,
)

BASE_MTIME = 1_700_000_000


def _write(path, size=100, age=0):
    """Create a file of `size` bytes whose mtime is `age` seconds after BASE."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b'\0' * size)
    stamp = BASE_MTIME + age
    os.utime(path, (stamp, stamp))
    return path


def _touch_dir(path, age=0):
    path.mkdir(parents=True, exist_ok=True)
    stamp = BASE_MTIME + age
    os.utime(path, (stamp, stamp))
    return path


@pytest.fixture
def remove_spy(monkeypatch):
    """Record every path os.remove is *asked* to delete.

    The guard's whole value is that a bad path never reaches os.remove. Asserting
    on survivors alone cannot show that: os.remove refuses a directory anyway, so
    an unguarded call looks identical from the filesystem's point of view.
    """
    attempted = []
    real_remove = os.remove

    def _spy(path, *args, **kwargs):
        attempted.append(str(path))
        return real_remove(path, *args, **kwargs)

    monkeypatch.setattr(os, 'remove', _spy)
    return attempted


# --------------------------------------------------------------------------- #
#  delete_oldest_files — oldest-first, and stop once under the limit           #
# --------------------------------------------------------------------------- #

def test_deletes_oldest_first_and_stops_once_under_limit(tmp_path):
    """Five 100-byte files, limit 250: the three oldest go, the two newest stay."""
    files = [_write(tmp_path / f'f{i}.fit', size=100, age=i) for i in range(5)]

    deleted = delete_oldest_files(str(tmp_path), 250)

    assert deleted == 3
    assert [f.exists() for f in files] == [False, False, False, True, True]
    assert get_directory_size(str(tmp_path)) == 200


def test_noop_when_already_under_limit(tmp_path):
    files = [_write(tmp_path / f'f{i}.fit', size=100, age=i) for i in range(3)]

    assert delete_oldest_files(str(tmp_path), 10_000) == 0
    assert all(f.exists() for f in files)


# --------------------------------------------------------------------------- #
#  Files only — no directory is ever removed                                   #
# --------------------------------------------------------------------------- #

def test_no_directory_is_removed_even_when_everything_is_deleted(tmp_path):
    """Limit 0 deletes every file. Every directory must survive — including the
    ones left empty, which is precisely what the deleted remove_empty_directories
    helper used to reap."""
    nested = tmp_path / 'sub1' / 'sub2'
    empty = _touch_dir(tmp_path / 'empty_session')
    files = [
        _write(tmp_path / 'a.fit', age=0),
        _write(tmp_path / 'sub1' / 'b.fit', age=1),
        _write(nested / 'c.fit', age=2),
    ]

    deleted = delete_oldest_files(str(tmp_path), 0)

    assert deleted == 3
    assert not any(f.exists() for f in files)
    for d in (tmp_path, tmp_path / 'sub1', nested, empty):
        assert d.is_dir(), f"directory was removed: {d}"


@pytest.mark.skipif(sys.platform == 'win32',
                    reason="symlink creation needs elevation on Windows")
def test_symlink_to_directory_is_neither_followed_nor_deleted(tmp_path):
    """os.walk lists a symlink-to-dir under dirnames and does not descend it, so
    the link must survive and the tree behind it must be untouched — cleanup
    must not escape the watch directory through a link."""
    outside = _touch_dir(tmp_path / 'outside')
    treasure = _write(outside / 'keep_me.fit', size=500, age=0)

    watch = _touch_dir(tmp_path / 'watch')
    doomed = _write(watch / 'doomed.fit', size=100, age=1)
    link = watch / 'link_to_outside'
    link.symlink_to(outside, target_is_directory=True)

    delete_oldest_files(str(watch), 0)

    assert not doomed.exists()
    assert link.is_symlink(), "the symlink itself was removed"
    assert outside.is_dir()
    assert treasure.exists(), "cleanup followed a symlink out of the watch dir"
    assert treasure.read_bytes() == b'\0' * 500


# --------------------------------------------------------------------------- #
#  The isfile guard — a stale enumeration must never reach os.remove           #
# --------------------------------------------------------------------------- #

def test_guard_never_asks_os_remove_to_delete_a_directory(tmp_path, monkeypatch,
                                                          remove_spy):
    """Simulate a path that was a file when walked and is a directory by the time
    we act on it, by handing the deleter an enumeration that says so."""
    victim = _touch_dir(tmp_path / 'now_a_directory')
    _write(victim / 'inside.fit', size=100, age=0)

    monkeypatch.setattr(
        cleanup, 'get_all_files_with_mtime',
        lambda d: [(str(victim), BASE_MTIME, 100)],
    )

    deleted = delete_oldest_files(str(tmp_path), 0)

    assert str(victim) not in remove_spy, "os.remove was called on a directory"
    assert deleted == 0
    assert victim.is_dir()
    assert (victim / 'inside.fit').exists()


def test_guard_skips_a_path_that_vanished_after_enumeration(tmp_path, monkeypatch,
                                                            remove_spy):
    """A file deleted by the capture program between walk and delete is a benign
    race, not an error to attempt and log."""
    ghost = tmp_path / 'already_gone.fit'
    _write(tmp_path / 'real.fit', size=100, age=1)

    monkeypatch.setattr(
        cleanup, 'get_all_files_with_mtime',
        lambda d: [(str(ghost), BASE_MTIME, 100)],
    )

    deleted = delete_oldest_files(str(tmp_path), 0)

    assert str(ghost) not in remove_spy
    assert deleted == 0


def test_session_guard_never_asks_os_remove_to_delete_a_directory(tmp_path,
                                                                  monkeypatch,
                                                                  remove_spy):
    """Same guard, the delete_oldest_sessions branch: os.walk is stubbed to
    report a subdirectory as though it were a file."""
    old = _touch_dir(tmp_path / 'session_old', age=0)
    trap = _touch_dir(old / 'subdir')
    _touch_dir(tmp_path / 'session_new', age=100)
    _write(tmp_path / 'session_new' / 'new.fit', size=100, age=100)

    real_walk = os.walk

    def _lying_walk(top, *args, **kwargs):
        if str(top) == str(old):
            return iter([(str(old), [], ['subdir'])])
        return real_walk(top, *args, **kwargs)

    monkeypatch.setattr(cleanup.os, 'walk', _lying_walk)

    delete_oldest_sessions(str(tmp_path), 0)

    assert str(trap) not in remove_spy, "os.remove was called on a directory"
    assert trap.is_dir()


# --------------------------------------------------------------------------- #
#  delete_oldest_sessions — newest session untouched, structure intact         #
# --------------------------------------------------------------------------- #

def test_newest_session_folder_is_left_untouched(tmp_path):
    sessions = []
    for i in range(3):
        folder = _touch_dir(tmp_path / f'session_{i}', age=i * 100)
        sessions.append((folder, _write(folder / 'frame.fit', size=100,
                                        age=i * 100)))
    # mkdir of the children bumps the parents' mtimes; restamp after the writes
    for i, (folder, _) in enumerate(sessions):
        os.utime(folder, (BASE_MTIME + i * 100, BASE_MTIME + i * 100))

    deleted = delete_oldest_sessions(str(tmp_path), 0)

    assert deleted == 2
    assert not sessions[0][1].exists()
    assert not sessions[1][1].exists()
    assert sessions[2][1].exists(), "newest session's files were deleted"
    for folder, _ in sessions:
        assert folder.is_dir(), f"session folder was removed: {folder}"


def test_single_session_folder_is_never_touched(tmp_path):
    folder = _touch_dir(tmp_path / 'only_session')
    frame = _write(folder / 'frame.fit', size=100)

    assert delete_oldest_sessions(str(tmp_path), 0) == 0
    assert frame.exists()
    assert folder.is_dir()


def test_sessions_leaves_nested_folder_structure_intact(tmp_path):
    old = _touch_dir(tmp_path / 'session_old', age=0)
    nested = _touch_dir(old / 'lights' / 'red')
    old_frame = _write(nested / 'frame.fit', size=100, age=0)
    new = _touch_dir(tmp_path / 'session_new', age=500)
    _write(new / 'frame.fit', size=100, age=500)
    for folder, age in ((old, 0), (new, 500)):
        os.utime(folder, (BASE_MTIME + age, BASE_MTIME + age))

    delete_oldest_sessions(str(tmp_path), 0)

    assert not old_frame.exists()
    for d in (old, old / 'lights', nested, new):
        assert d.is_dir(), f"directory was removed: {d}"


def test_get_session_folders_ignores_loose_files(tmp_path):
    _touch_dir(tmp_path / 'session_a')
    _write(tmp_path / 'loose.fit')

    found = [os.path.basename(p) for p, _m, _s in
             get_session_folders(str(tmp_path))]

    assert found == ['session_a']


# --------------------------------------------------------------------------- #
#  run_cleanup — dispatch and the early exits                                  #
# --------------------------------------------------------------------------- #

def test_run_cleanup_is_a_noop_when_disabled(tmp_path, remove_spy):
    frame = _write(tmp_path / 'frame.fit', size=100)

    ok, message = run_cleanup({
        'cleanup_enabled': False,
        'watch_directory': str(tmp_path),
        'cleanup_max_size_gb': 0,
    })

    assert (ok, message) == (True, "Cleanup not enabled")
    assert frame.exists()
    assert remove_spy == []


def test_run_cleanup_rejects_a_missing_watch_directory(tmp_path):
    ok, message = run_cleanup({
        'cleanup_enabled': True,
        'watch_directory': str(tmp_path / 'does_not_exist'),
    })

    assert (ok, message) == (False, "Watch directory not valid")


def test_run_cleanup_rejects_an_empty_watch_directory_setting():
    ok, message = run_cleanup({'cleanup_enabled': True, 'watch_directory': ''})

    assert (ok, message) == (False, "Watch directory not valid")


def test_run_cleanup_reports_unknown_strategy(tmp_path, remove_spy):
    _write(tmp_path / 'frame.fit', size=100)

    ok, message = run_cleanup({
        'cleanup_enabled': True,
        'watch_directory': str(tmp_path),
        'cleanup_max_size_gb': 0,
        'cleanup_strategy': 'Delete everything immediately',
    })

    assert ok is False
    assert message == "Unknown cleanup strategy: Delete everything immediately"
    assert remove_spy == []


# --------------------------------------------------------------------------- #
#  No directory-removal primitive may reappear in this module                  #
# --------------------------------------------------------------------------- #

def test_module_exposes_no_directory_removal_helper():
    """remove_empty_directories was dead code that called os.rmdir, contradicting
    the files-only rule. Dead code that contradicts a safety invariant is worse
    than no code, because the next person wires it up believing it is sanctioned."""
    assert not hasattr(cleanup, 'remove_empty_directories')


def test_module_calls_no_directory_removal_primitive():
    source = open(cleanup.__file__, encoding='utf-8').read()
    offenders = re.findall(r'\b(os\.rmdir|os\.removedirs|shutil\.rmtree)\s*\(',
                           source)
    assert offenders == [], f"directory-removal call in cleanup.py: {offenders}"
