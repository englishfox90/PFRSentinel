#!/usr/bin/env python3
"""Locate sample files in an ML data directory.

Qt-free on purpose: the labeling tool, the AI pre-labeller and the training
scripts must all agree on what counts as "the dataset".

Folders whose name starts with an underscore are housekeeping, never data:
`_removed` (frames taken out of the dataset), `_backup_scrub_*` (pre-edit copies
written by the cleanup scripts), `_label_cleanup`, `_label_validation`. They hold
copies of calibration JSONs under the *same filenames* as the live ones, so a
plain rglob picks up both and lets the stale copy shadow the real file.
"""
import os
from pathlib import Path

CAL_PREFIX = "calibration_"


def iter_calibration_files(data_dir):
    """Yield every live calibration JSON under data_dir, in timestamp order per folder."""
    data_dir = Path(data_dir)
    for root, dirs, files in os.walk(data_dir):
        dirs[:] = sorted(d for d in dirs if not d.startswith("_"))
        for name in sorted(files):
            if name.startswith(CAL_PREFIX) and name.endswith(".json"):
                yield Path(root) / name


def find_sample_sets(data_dir) -> list:
    """All samples as dicts: timestamp, folder, calibration, and lum when present.

    The lum frame is only ever looked up beside its own calibration file.
    """
    # Imported here so iter_calibration_files stays usable by training scripts
    # launched with only ml/ on sys.path.
    from services.logger import app_logger

    samples = {}
    for cal_file in iter_calibration_files(data_dir):
        timestamp = cal_file.stem[len(CAL_PREFIX):]
        if timestamp in samples:
            app_logger.warning(
                f"Duplicate sample {timestamp}: keeping {samples[timestamp]['calibration']}, "
                f"ignoring {cal_file}")
            continue

        sample = {'timestamp': timestamp, 'folder': cal_file.parent, 'calibration': cal_file}
        lum_path = cal_file.parent / f"lum_{timestamp}.fits"
        if lum_path.exists():
            sample['lum'] = lum_path
        samples[timestamp] = sample

    return [samples[ts] for ts in sorted(samples)]
