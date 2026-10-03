"""
Writing the production calibration file.

Extracted from calibration_service.py. The one rule that matters lives here:
an automatic replacement is the one write the user did not ask for, so the
file being overwritten is copied to the backup path first — on 2026-09-05 an
automatic save destroyed the only copy of a correct model.

That backup is one step deep. On 2026-09-29 (discussion #105) a Guided
Calibration solve was replaced by an automatic refinement at 22:27 and the
backup holding it by the next one at 22:57, so nothing on disk still had the
model the user had anchored. keep_guided_copy writes a guided solve to its own
file as well, which save_with_backup never touches.
"""
import os
import shutil
from datetime import datetime, timezone
from typing import Optional

from services.logger import app_logger as log

from .fisheye import FisheyeModel
from .model_admission import is_user_anchored


def save_with_backup(model: FisheyeModel, stamp_time: bool = True) -> Optional[str]:
    """Save `model` to the calibration path; return an error string or None.

    `stamp_time=False` re-saves the same model (a provenance stamp) without
    moving its calibration timestamp, and without a backup — the file being
    overwritten is this model.
    """
    try:
        from services.app_config import (
            get_calibration_backup_path, get_calibration_path)
        cal_path = get_calibration_path()
        if stamp_time and os.path.isfile(cal_path):
            try:
                shutil.copyfile(cal_path, get_calibration_backup_path())
            except OSError as e:
                log.warning(f"Could not back up the previous calibration: {e}")
        if stamp_time:
            model.calibrated_at = datetime.now(timezone.utc).isoformat()
        model.save(cal_path)
        log.info(f"Calibration saved to {cal_path}")
        return None
    except Exception as e:
        log.error(f"Failed to save calibration: {e}")
        return str(e)


def keep_guided_copy(model: FisheyeModel, cal_path: str) -> Optional[str]:
    """Write the guided solve `model` beside `cal_path`, the calibration file
    it was just saved to; the copy's path, or None when `model` is not a
    guided solve or the write failed (a warning — the calibration itself is
    already saved)."""
    if not is_user_anchored(model):
        return None
    try:
        from services.app_config import GUIDED_CALIBRATION_FILENAME
        path = os.path.join(os.path.dirname(os.path.abspath(cal_path)),
                            GUIDED_CALIBRATION_FILENAME)
        model.save(path)
        log.info(f"Guided calibration kept at {path} (automatic saves never "
                 "overwrite it)")
        return path
    except Exception as e:
        log.warning(f"Could not keep a copy of the guided calibration: {e}")
        return None
