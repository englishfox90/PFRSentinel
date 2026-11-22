"""What the shared capture loop says while it recovers, per backend.

The loop was written for a USB camera, and its recovery lines say so ("USB bus
settle", "ZWO cold-open quirk"). A session may carry ``recovery_wording`` to
replace them; ``ZWO_WORDING`` is the default and reproduces the loop's original
text exactly. ``levelled`` sends the loop's failure lines to the log as
WARNING (and tracebacks of ``quiet_traceback_types`` to DEBUG) instead of the
session's INFO callback — a network outage is expected weather, not a crash.

``errors_survive_reconnect`` (network only) keeps the loop's error count until
a frame arrives instead of zeroing it on every reconnect that answers: an Alpaca
server can accept every reconnect while the camera behind it never finishes an
exposure (issue #177), and a count reset each time never reached long-retry.
With it the loop also reports a reconnect that answered but has not delivered a
frame yet as ``link_state`` ``'unconfirmed'`` (``'stalled'`` after an exposure
that never finished), instead of "reconnecting".
"""
from dataclasses import dataclass

from .errors import CameraDisconnectedError, ExposureStalledError


@dataclass(frozen=True)
class RecoveryWording:
    cold_open_absorb: str = ("First frame after reconnect failed ({error}) — reopening "
                             "silently (known ZWO cold-open quirk, no fault raised)")
    pre_reconnect_wait: str = ("Waiting {seconds:.0f}s before reconnection attempt "
                               "(USB bus settle)...")
    post_reconnect_settle: str = "Waiting 3s for USB bus to stabilise..."
    levelled: bool = False
    quiet_traceback_types: tuple = ()
    errors_survive_reconnect: bool = False


ZWO_WORDING = RecoveryWording()

NETWORK_WORDING = RecoveryWording(
    cold_open_absorb=("First frame after reconnect failed ({error}) — retrying once on a "
                      "fresh connection (no fault raised)"),
    pre_reconnect_wait="Waiting {seconds:.0f}s before reconnecting to the Alpaca server...",
    post_reconnect_settle="Waiting 3s before the first frame on the new connection...",
    levelled=True,
    quiet_traceback_types=(CameraDisconnectedError, ExposureStalledError),
    errors_survive_reconnect=True,
)


def wording_for(camera) -> RecoveryWording:
    wording = getattr(camera, 'recovery_wording', None)
    return wording if isinstance(wording, RecoveryWording) else ZWO_WORDING


__all__ = ["RecoveryWording", "ZWO_WORDING", "NETWORK_WORDING", "wording_for"]
