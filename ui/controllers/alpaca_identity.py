"""What the app has learned about each Alpaca address it has talked to (issue #43).

An Alpaca camera's UniqueID and Name are learned at one (host, port, device)
address, and the camera's profile is keyed by that UniqueID. Retyping the
address — even back to the same value, one keystroke at a time — must not lose
them, or the settings fall back to an address-keyed profile with defaults.
The same goes for the capabilities a Test Connection or a session read (gain
and offset ranges, Bayer pattern, gain names): switching to ZWO and back should
not show the open 0–65535 ranges until the next test.

In memory only: after a restart the config's identity is the one at the
configured address, and capabilities are read again on the next test.
"""
from services.camera.camera_source import alpaca_settings


def alpaca_address(settings: dict) -> tuple:
    """``(host, port, device_number)`` of an ``alpaca_camera``-shaped dict."""
    settings = settings or {}
    try:
        port = int(settings.get('port', 0) or 0)
    except (TypeError, ValueError):
        port = 0
    try:
        device = int(settings.get('device_number', 0) or 0)
    except (TypeError, ValueError):
        device = 0
    return str(settings.get('host') or '').strip(), port, device


class AlpacaIdentityMemory:
    """UniqueID / Name per address, as learned from config, discovery or a test."""

    def __init__(self):
        self._known = {}

    def remember(self, settings: dict):
        unique_id = str((settings or {}).get('unique_id') or '').strip()
        name = str((settings or {}).get('name') or '').strip()
        address = alpaca_address(settings)
        if address[0] and (unique_id or name):
            self._known[address] = (unique_id, name)

    def identity_for(self, settings: dict) -> tuple:
        """``(unique_id, name)`` learned at this address, else ``('', '')``."""
        return self._known.get(alpaca_address(settings), ('', ''))

    def edited_settings(self, current: dict, values: dict) -> dict:
        """``alpaca_camera`` after the user typed ``values`` (host / port / device).

        The identity in ``current`` belongs to ``current``'s address, so it is
        remembered first; the result carries whatever was learned at the new
        address (nothing for an address never seen, like a manual ZWO pick
        forgetting the serial). A discovered device's ``values`` bring their
        own UniqueID and Name.
        """
        current = dict(current or {})
        self.remember(current)
        updated = {**current, **values}
        if 'unique_id' in values:
            self.remember(updated)
            return updated
        unique_id, name = self.identity_for(updated)
        updated['unique_id'], updated['name'] = unique_id, name
        return updated


class AlpacaCapabilityMemory:
    """The last capabilities (and gain names) read from each address."""

    def __init__(self):
        self._known = {}

    def remember(self, settings: dict, caps, gain_names=()):
        address = alpaca_address(settings)
        if address[0] and caps is not None:
            self._known[address] = (caps, tuple(gain_names or ()))

    def lookup(self, settings: dict):
        """``(caps, gain_names)`` for this address, else ``None``."""
        return self._known.get(alpaca_address(settings))

    def for_config(self, config):
        return self.lookup(alpaca_settings(config))
