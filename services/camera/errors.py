"""Backend-neutral camera exceptions.

The Alpaca backend raises these so the shared capture loop and controller can
treat a dropped network camera the same way as a dropped USB one. The ZWO path
still raises its historical exception types and messages: the loop and the
controller key on message text, so those raise sites are deliberately left alone.
"""


class CameraError(Exception):
    """Base class for camera backend failures."""


class CameraDisconnectedError(CameraError):
    """The device is unreachable or no longer connected; a reconnect may fix it."""


class CameraTimeoutError(CameraError):
    """A camera operation did not finish within its time bound."""


class ExposureStalledError(CameraTimeoutError):
    """The camera accepted an exposure and kept answering, but never finished it.

    Issue #177: ZWO's ASCOM driver, after NINA opened another ZWO camera, kept
    reporting Connected and Exposing while no exposure ever completed. A plain
    reconnect does not help there; the Alpaca session escalates (``alpaca_stall_recovery``).
    """


class CameraConfigurationError(CameraError):
    """The device rejected a setting or reported an unusable configuration."""


class FrameSkipped(CameraError):
    """This frame could not be taken or kept, but the camera is fine.

    The shared capture loop skips to the next frame without counting an error
    or reconnecting: e.g. another client (NINA) is exposing on a shared Alpaca
    camera, or the delivered image is not the frame this session asked for.
    """


__all__ = [
    "FrameSkipped",
    "CameraError",
    "CameraDisconnectedError",
    "CameraTimeoutError",
    "ExposureStalledError",
    "CameraConfigurationError",
]
