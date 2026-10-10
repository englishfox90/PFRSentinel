"""Linux-only checks for the two host settings ZWO cameras need.

Neither is something the app can fix without root, and both fail in ways that
look like a camera fault: without the udev rule the SDK lists the camera but
cannot open it, and with the kernel's default 16 MB usbfs buffer long or
full-resolution exposures time out or come back as broken frames. So name the
cause in the log once, next to the SDK init line, instead of leaving a
"camera open failed" with nothing behind it.

``installer/linux/asi.rules`` fixes both.
"""
from __future__ import annotations

import glob
import os
import sys
from typing import Callable, Optional

USBFS_MEMORY_PARAM = "/sys/module/usbcore/parameters/usbfs_memory_mb"
# ZWO's recommended value, and what their udev rule writes.
RECOMMENDED_USBFS_MB = 200

UDEV_RULE_DIRS = ("/etc/udev/rules.d", "/usr/lib/udev/rules.d", "/lib/udev/rules.d")
ZWO_USB_VENDOR_ID = "03c3"

_already_logged = False


def read_usbfs_memory_mb(param_path: str = USBFS_MEMORY_PARAM) -> Optional[int]:
    """Current usbfs buffer limit in MB, or None when it cannot be read."""
    try:
        with open(param_path, "r", encoding="ascii") as handle:
            return int(handle.read().strip())
    except (OSError, ValueError):
        return None


def has_zwo_udev_rule(rule_dirs=UDEV_RULE_DIRS) -> bool:
    """True when any installed udev rule mentions the ZWO vendor id."""
    for folder in rule_dirs:
        for rule_file in glob.glob(os.path.join(folder, "*.rules")):
            try:
                with open(rule_file, "r", encoding="utf-8", errors="replace") as handle:
                    if ZWO_USB_VENDOR_ID in handle.read().lower():
                        return True
            except OSError:
                continue
    return False


def linux_usb_warnings() -> list:
    """Warning lines for this host; empty when everything is in order."""
    warnings = []
    # 0 means "no limit", which is fine.
    usbfs_mb = read_usbfs_memory_mb()
    if usbfs_mb is not None and 0 < usbfs_mb < RECOMMENDED_USBFS_MB:
        warnings.append(
            f"⚠ usbfs_memory_mb is {usbfs_mb} MB; ZWO recommends {RECOMMENDED_USBFS_MB}. "
            "Long or full-resolution exposures may time out or return broken frames."
        )
    if os.geteuid() != 0 and not has_zwo_udev_rule():
        warnings.append(
            "⚠ No udev rule for ZWO cameras found — the camera will be listed "
            "but will not open for a non-root user."
        )
    if warnings:
        warnings.append(
            "  Fix: sudo install -m 644 installer/linux/asi.rules /etc/udev/rules.d/ "
            "&& sudo udevadm control --reload-rules, then replug the camera."
        )
    return warnings


def log_linux_usb_warnings(log: Callable[[str], None]) -> None:
    """Log the host warnings on Linux; a no-op everywhere else, and never raises."""
    global _already_logged
    if _already_logged or not sys.platform.startswith("linux"):
        return
    # Once per process: initialize_sdk() runs again on every reconnect.
    _already_logged = True
    try:
        for line in linux_usb_warnings():
            log(line)
    except Exception as e:
        log(f"Linux USB preflight skipped: {e}")
