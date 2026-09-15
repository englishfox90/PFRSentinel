---
globs: "services/zwo_camera.py,services/camera_*.py"
description: ZWO ASI camera hardware constraints — easy to get wrong
---

# ZWO Camera — Critical Hardware Constraints

These are real bugs that have shipped in this project. Get them right.

## Bayer pattern
- **ASI676MC is BGGR**, not RGGB. Use `cv2.cvtColor(data, cv2.COLOR_BayerBG2RGB)`.
- Wrong pattern = red and blue channels swapped (sky looks orange, not blue).
- ZWO cameras output RAW8 Bayer data. Always debayer before any RGB processing.

## Exposure units
- **GUI: milliseconds. SDK: seconds.** Convert at the boundary, never anywhere else.
- All `*_ms` fields in config are milliseconds; all SDK calls take seconds.

## SDK initialization
- `ASICamera2.dll` lives in app root by default. Custom path is configurable via the Capture tab.
- SDK init can fail if no camera is connected — handle the absence gracefully, don't crash on startup.

## Reconnect
- Cameras drop off USB occasionally — the reconnect path in `camera_connection.py` must remain idempotent. Don't add state that prevents repeated init attempts.
- Windows USB reset uses `CM_Reenumerate_DevNode()` via ctypes. Wrap in try/except and fall back gracefully on non-Windows or unprivileged sessions.

## Disconnect / cleanup
- `ZWOCamera` relies on a layered safety net: `__del__`, `__enter__/__exit__`, and a `_cleanup_lock`. Don't remove any of these — improper cleanup leaves the USB device hung until reboot.
- `stop_capture()` aborts the in-progress exposure and any running calibration, then joins the worker thread with a **3-second timeout**. Preserve that flow — the aborts are what make the short join safe. Dropping the aborts and keeping a short join will race; an unbounded join can deadlock shutdown.

## Per-camera profiles
- Camera-specific settings live under `camera_profiles[<clean_camera_name>]`. The clean name strips index suffixes — two cameras with the same base name share one profile (a known limitation, not a bug).
- When a setting is missing from the per-camera profile, fall back to the global default — never crash on a missing key.
- `scripts/fix_cameras.py` resets corrupted profiles. Don't roll your own reset logic in the main app.

## Auto-exposure
- Target mean brightness = **100**. Adjust by ±30% only when current brightness is outside the **80–120** band — keeps the loop from oscillating on noise.
- Always respect `max_exposure` from config when raising exposure.
