#!/usr/bin/env python3
"""Read and write calibration JSONs — the files that carry the human labels."""
import json
import os
from pathlib import Path


def load_calibration(path) -> dict:
    with open(path, 'r') as f:
        return json.load(f)


def save_calibration(path, cal: dict):
    """Replace the file in one step.

    Writing in place truncates first, so a crash or a full disk mid-write would
    leave a half-written JSON and lose that frame's labels.
    """
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, 'w') as f:
        json.dump(cal, f, indent=2)
    os.replace(tmp, path)
