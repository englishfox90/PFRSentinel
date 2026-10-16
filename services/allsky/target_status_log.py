"""
One INFO line each time what the overlay does with the NINA target changes.

The overlay renders every frame, so logging each render would bury the log,
and logging nothing left "Sentinel has the target but draws nothing"
indistinguishable from a target that was never received. A line is written
only when the outcome changes: drawn with a box, drawn as a reticle and why,
not drawn and why, or hidden because NINA stopped reporting it.
"""
import threading
from typing import Optional, Tuple

from services.logger import app_logger

_lock = threading.Lock()
_last: Optional[Tuple[str, str]] = None


def _report(name: str, outcome: str, detail: str = '') -> None:
    """Log ``outcome`` for target ``name`` unless it is the last one logged.
    ``detail`` carries live numbers and is left out of the comparison."""
    global _last
    key = (name, outcome)
    with _lock:
        if key == _last:
            return
        _last = key
    app_logger.info(f"NINA target '{name}' {outcome}{detail}")


def report_placement(name: str, placement, unplaced_reason: str) -> None:
    """The outcome of ``render_target.locate_target`` for this frame."""
    if placement is None:
        _report(name, f"not drawn on the all-sky overlay: {unplaced_reason}")
    elif placement.fov_polygon:
        _report(name, "drawn on the all-sky overlay with its field-of-view box")
    else:
        _report(name, "drawn on the all-sky overlay as a reticle, without a box: "
                f"{placement.reticle_reason}")


def report_stale(name: str, age_s: Optional[float], stale_after_s: float) -> None:
    """NINA's last report of ``name`` is older than ``stale_after_s``."""
    age = "" if age_s is None else f" ({age_s:.0f} s since the last one)"
    _report(name, f"hidden: NINA has not reported it for over {stale_after_s:.0f} s", age)


def forget() -> None:
    """No target is held any more; the next one is reported afresh."""
    global _last
    with _lock:
        _last = None
