"""
Writing the production calibration file.

Extracted from calibration_service.py. The one rule that matters lives here:
an automatic replacement is the one write the user did not ask for, so the
file being overwritten is copied to the backup path first — on 2026-09-05 an
automatic save destroyed the only copy of a correct model.
"""
import os
import shutil
from datetime import datetime, timezone
from typing import Optional

from services.logger import app_logger as log

from .fisheye import FisheyeModel


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
