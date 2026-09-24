#!/usr/bin/env python3
"""Read and write calibration JSONs — the files that carry the human labels.

The labeling tool has several writers to the same file at once: the GUI thread
saving a label, and up to six AI pre-label threads adding an `ai_suggestion`.
Every read-modify-write therefore goes through `update_calibration`, which holds
a per-file lock for the whole cycle, so no writer can work from a stale copy.

The locks are per process. Two *processes* on the same data dir (the tool plus
`ai_prelabel.py` in a shell) are still last-writer-wins — run one at a time.
"""
import json
import os
import tempfile
import threading
from pathlib import Path

_locks = {}
_locks_guard = threading.Lock()


def _lock_for(path) -> threading.RLock:
    key = os.path.normcase(os.path.abspath(path))
    with _locks_guard:
        lock = _locks.get(key)
        if lock is None:
            lock = _locks[key] = threading.RLock()
        return lock


def load_calibration(path) -> dict:
    # Locked too: on Windows os.replace fails if another thread has the target open.
    with _lock_for(path):
        with open(path, 'r') as f:
            return json.load(f)


def save_calibration(path, cal: dict):
    """Replace the file in one step.

    Writing in place truncates first, so a crash or a full disk mid-write would
    leave a half-written JSON and lose that frame's labels. The temp name is
    unique per call so two writers can never share — and truncate — one temp file.
    """
    path = Path(path)
    with _lock_for(path):
        fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
        try:
            with os.fdopen(fd, 'w') as f:
                json.dump(cal, f, indent=2)
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise


def update_calibration(path, mutate, missing_ok: bool = False):
    """Load, let `mutate(cal)` change the dict in place, save — all under the file's lock.

    `mutate` returning False means "leave the file alone"; nothing is written and
    None is returned. Otherwise returns the dict as saved. With `missing_ok`, an
    unparseable file is treated as empty rather than raising ValueError.
    """
    with _lock_for(path):
        try:
            cal = load_calibration(path)
        except ValueError:
            if not missing_ok:
                raise
            cal = {}
        if mutate(cal) is False:
            return None
        save_calibration(path, cal)
        return cal
