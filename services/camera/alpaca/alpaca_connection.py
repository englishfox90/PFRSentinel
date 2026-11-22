"""The ``CameraConnection``-shaped half of an Alpaca session.

The shared capture loop and ``CaptureSessionBase`` reach a session's
``_connection`` for ``sdk_lock``, ``disconnect(release_sdk=)`` and identity;
this class gives them those without any ZWO knowledge:

- ``sdk_lock`` is per instance. It serialises this client's writes against its
  own capture thread; ``SDK_LOCK`` would make a network camera and a USB one
  wait on each other for no reason.
- ``connect`` resolves the device number by UniqueID through the server's
  Management API (a server may renumber its devices), connects, reads the
  static facts and configures gain, offset and a full-frame ROI. A UniqueID
  that the server no longer lists fails the connect, like ZWO's serial gate.
- ``disconnect`` is idempotent and never sends ``Connected=false`` unless
  ``release_on_disconnect`` is set. ``release_sdk`` is accepted and ignored.
- It never writes config: the learned UniqueID / Name are on ``unique_id`` /
  ``camera_name`` for the caller to persist on the main thread.
- ``warnings`` (``SessionWarnings``) outlives every device object, so a
  warn-once stays once across reconnects.
- A host field typed as ``host:port`` (``obs-pc:11111``, ``[fe80::1]:11111``)
  is split; a port that is not a number fails the connect with that reason.
"""
import threading
from typing import Callable, Optional

from services.logger import app_logger

from ..camera_source import alpaca_profile_key
from ..errors import CameraConfigurationError, CameraError
from .alpaca_device import AlpacaCameraDevice
from .alpaca_http import AlpacaHttpClient, ci_get, lan_session, split_host_port  # noqa: F401
from .alpaca_protocol import ALPACA_SDK, AlpacaInvalidOperationError, AlpacaNotImplementedError
from .alpaca_transport_text import not_alpaca_server_text
from .alpaca_warnings import SessionWarnings

CLEANUP_LOCK_TIMEOUT_S = 5.0


class AlpacaIdentityError(CameraConfigurationError):
    """The server answered, but not with the camera this config points at."""


def _describe_devices(devices) -> str:
    kinds = sorted({str(ci_get(d, "DeviceType", "?")) for d in devices})
    return ", ".join(kinds) if kinds else "nothing"


class AlpacaConnection:
    """Connection, identity and teardown for one Alpaca camera."""

    def __init__(self, settings: dict, logger: Optional[Callable[[str], None]] = None, *,
                 session_factory: Callable = lan_session,
                 connect_timeout_s: float = 30.0):
        self.settings = dict(settings or {})
        self._logger = logger
        self._session_factory = session_factory
        self._connect_timeout_s = float(connect_timeout_s)
        self.camera: Optional[AlpacaCameraDevice] = None
        self.asi = ALPACA_SDK
        self.sdk_lock = threading.Lock()
        self._cleanup_lock = threading.Lock()
        self.client: Optional[AlpacaHttpClient] = None
        self.static_info = None
        self.camera_info: dict = {}
        self.camera_name = self.settings.get("name") or None
        self.unique_id = self.settings.get("unique_id") or ""
        self.device_number = int(self.settings.get("device_number", 0) or 0)
        self.supports_raw16 = False
        self.bit_depth = 8
        self.current_bit_depth = 8
        self.auto_recovery_enabled = True
        self.last_error = ""
        self._management_missing = False
        self.renumbered_from = None
        self.warnings = SessionWarnings()

    # -- identity ---------------------------------------------------------
    def _address(self):
        raw_port = self.settings.get("port") or 11111
        try:
            return split_host_port(str(self.settings.get("host") or ""), int(raw_port))
        except ValueError:
            return str(self.settings.get("host") or "").strip(), int(raw_port)

    @property
    def host(self) -> str:
        return self._address()[0]

    @property
    def port(self) -> int:
        return int(self._address()[1] or 11111)

    def address_error(self) -> str:
        """Why the host field cannot be used ('' when it can)."""
        try:
            split_host_port(str(self.settings.get("host") or ""), 11111)
        except ValueError as exc:
            return str(exc)
        return ""

    @property
    def camera_serial(self) -> str:
        """The profile key: ``alpaca:<UniqueID>`` once known."""
        return alpaca_profile_key({"host": self.host, "port": self.port,
                                   "device_number": self.device_number,
                                   "unique_id": self.unique_id})

    def describe(self) -> str:
        return f"{self.host}:{self.port} #{self.device_number}"

    def log(self, message: str) -> None:
        if self._logger:
            self._logger(message)
        else:
            app_logger.info(message)

    # -- connect ----------------------------------------------------------
    def _new_client(self) -> AlpacaHttpClient:
        return AlpacaHttpClient(
            self.host, self.port, self.device_number,
            connect_timeout=self.settings.get("connect_timeout_s", 3.0),
            request_timeout=self.settings.get("request_timeout_s", 10.0),
            session_factory=self._session_factory)

    def resolve_device_number(self, client: AlpacaHttpClient) -> None:
        """Point ``client`` at the configured camera; learn its UniqueID.

        Raises ``AlpacaIdentityError`` when the server lists no Camera at all
        (e.g. the port is PFR's own SafetyMonitor), when the saved UniqueID is
        gone, or when no camera has the configured number.
        """
        try:
            devices = client.management_configured_devices()
        except AlpacaNotImplementedError as exc:
            if getattr(exc, "http_status", None) == 404:
                self._management_missing = True
            app_logger.warning(f"Alpaca server {self.host}:{self.port} has no Management API; "
                               f"using device #{self.device_number} without an identity check")
            return
        cameras = [d for d in devices
                   if str(ci_get(d, "DeviceType", "")).strip().lower() == "camera"]
        if not cameras:
            raise AlpacaIdentityError(
                f"{self.host}:{self.port} answers but has no Camera device "
                f"(it lists: {_describe_devices(devices)}). Is this PFR's own Alpaca "
                f"SafetyMonitor port, or another server? Check the host and port.")

        def number_of(device):
            try:
                return int(ci_get(device, "DeviceNumber"))
            except (TypeError, ValueError):
                return None

        if self.unique_id:
            match = next((d for d in cameras
                          if str(ci_get(d, "UniqueID", "") or "") == self.unique_id), None)
            if match is None:
                raise AlpacaIdentityError(
                    f"The camera with UniqueID {self.unique_id} is no longer on "
                    f"{self.host}:{self.port}. Run Discover / Test Connection to pick it again.")
            number = number_of(match)
            if number is not None and number != self.device_number:
                self.log(f"Alpaca camera {self.unique_id} moved from device "
                         f"#{self.device_number} to #{number}")
                self.renumbered_from = self.device_number
                self.device_number = number
        else:
            match = next((d for d in cameras if number_of(d) == self.device_number), None)
            if match is None:
                listed = ", ".join(f"#{number_of(d)} {ci_get(d, 'DeviceName', '')}".strip()
                                   for d in cameras)
                raise AlpacaIdentityError(
                    f"{self.host}:{self.port} has no camera #{self.device_number} "
                    f"(cameras: {listed})")
            self.unique_id = str(ci_get(match, "UniqueID", "") or "")
        client.device_number = self.device_number

    def connect(self, camera_settings: Optional[dict] = None, *, configure: bool = True) -> bool:
        """Open the camera; False (with ``last_error``) when it cannot be."""
        self.last_error = ""
        self._management_missing = False
        if not self.host:
            self.last_error = "No Alpaca server host is configured"
            self.log(f"✗ {self.last_error}")
            return False
        if self.address_error():
            self.last_error = self.address_error()
            self.log(f"✗ {self.last_error}")
            return False
        self._drop_stale()
        client = self._new_client()
        device = None
        try:
            self.resolve_device_number(client)
            device = AlpacaCameraDevice(
                client, use_imagebytes=self.settings.get("use_imagebytes", True),
                release_on_disconnect=self.settings.get("release_on_disconnect", False),
                warnings=self.warnings)
            self.log(f"Connecting to Alpaca camera at {self.describe()}...")
            device.connect(timeout=self._connect_timeout_s)
            info = device.read_static()
            self.static_info = info
            self.camera_name = info.name
            self.camera_info = device.get_camera_property()
            self.bit_depth = info.sensor_bit_depth
            self.current_bit_depth = info.transfer_bit_depth
            if configure:
                self._configure(device, camera_settings or {})
        except Exception as exc:
            self.last_error = self._failure_text(exc)
            app_logger.debug(f"Alpaca connect to {self.describe()} failed: {exc!r}")
            self.log(f"✗ Alpaca connect failed ({self.describe()}): {self.last_error}")
            if device is not None:
                device.disconnect()
            client.close()
            return False
        self.client = client
        self.camera = device
        self.log(f"✓ Connected to Alpaca camera '{info.name}' ({self.describe()}): "
                 f"{info.width}x{info.height}, {info.physical_bayer_pattern}, "
                 f"{info.sensor_bit_depth}-bit (MaxADU {info.max_adu})")
        return True

    def _drop_stale(self) -> None:
        """Close a client a skipped disconnect (cleanup-lock timeout) left open."""
        stale, self.client = self.client, None
        self.camera = None
        if stale is not None:
            try:
                stale.close()
            except Exception as exc:
                app_logger.debug(f"Alpaca: closing the previous client failed: {exc}")

    def _failure_text(self, exc) -> str:
        """What a failed connect tells the user: a 404 from both the Management
        API and the device means the port is some other web server."""
        if self._management_missing and getattr(exc, "http_status", None) == 404:
            return not_alpaca_server_text(self.host, self.port)
        return str(exc) or exc.__class__.__name__

    def _configure(self, device: AlpacaCameraDevice, settings: dict) -> None:
        device.set_full_frame_roi()
        for kind in ("gain", "offset"):
            if kind not in settings:
                continue
            try:
                getattr(device, f"set_{kind}")(settings[kind])
            except AlpacaInvalidOperationError as exc:
                # Another client is exposing: the frame path re-asserts it.
                self.warnings.limited(f"configure-{kind}",
                                      f"Alpaca camera refused {kind} for now: {exc}")
        if "exposure_sec" in settings:
            device.set_pending_exposure(settings["exposure_sec"])
        if "flip" in settings:
            device.flip = int(settings["flip"] or 0)

    def configure(self, settings: dict) -> None:
        if self.camera is not None:
            self._configure(self.camera, settings)

    def reconnect_safe(self, settings: Optional[dict] = None, **_ignored) -> bool:
        """One re-handshake: drop the old handle, resolve by UniqueID, configure."""
        self.disconnect()
        return self.connect(settings)

    def verify_identity(self) -> bool:
        """True unless the server now lists a different camera at our number."""
        if self.client is None or not self.unique_id:
            return True
        try:
            devices = self.client.management_configured_devices(timeout=3.0)
        except CameraError:
            return True
        for d in devices:
            if str(ci_get(d, "DeviceType", "")).lower() == "camera":
                try:
                    if int(ci_get(d, "DeviceNumber")) == self.device_number:
                        return str(ci_get(d, "UniqueID", "") or "") == self.unique_id
                except (TypeError, ValueError):
                    continue
        return False

    # -- teardown ---------------------------------------------------------
    def disconnect(self, stop_exposure_callback: Optional[Callable[[], None]] = None,
                   release_sdk: bool = False) -> None:
        """Idempotent; aborts our exposure, releases only if configured to."""
        if not self._cleanup_lock.acquire(timeout=CLEANUP_LOCK_TIMEOUT_S):
            app_logger.warning("Alpaca disconnect: cleanup already in progress; skipping")
            return
        try:
            device, client = self.camera, self.client
            if device is None and client is None:
                return
            if stop_exposure_callback is not None:
                try:
                    stop_exposure_callback()
                except Exception as exc:
                    app_logger.debug(f"Alpaca disconnect: stop callback failed: {exc}")
            if device is not None:
                device.abort()
                if device.disconnect() is True:
                    self.log("Alpaca camera released (Connected=false)")
            if client is not None:
                client.close()
        finally:
            self.camera = None
            self.client = None
            self._cleanup_lock.release()
