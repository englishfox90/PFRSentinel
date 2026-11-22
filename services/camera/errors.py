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


class CameraConfigurationError(CameraError):
    """The device rejected a setting or reported an unusable configuration."""


__all__ = [
    "CameraError",
    "CameraDisconnectedError",
    "CameraTimeoutError",
    "CameraConfigurationError",
]
