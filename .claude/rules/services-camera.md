---
globs: "services/camera/**/*.py,services/camera_profiles.py"
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
- The SDK library is `ASICamera2.dll` on Windows, `libASICamera2.dylib` on macOS, `libASICamera2.so` on Linux. **Never spell the name or a location** — ask `services/zwo_sdk_library.py`: `resolve_library_path(configured)` returns the configured file if it exists, else the first copy found (bundled, `<app-data root>/sdk/`, system library folders). Custom path is configurable via the Capture tab.
- Always hand `zwoasi.init()` the absolute path the locator returns, never a bare filename: macOS and Linux have no `os.add_dll_directory()` equivalent, so a bare name never finds a copy sitting next to the app.
- Only the Windows DLL is committed and bundled. The macOS/Linux libraries are per-CPU downloads the user (or INDI's `libasi`) installs — see `docs/dev/ZWO_SDK_PLATFORMS.md` for the reasoning, the Linux udev rule (`installer/linux/asi.rules`) and the `usbfs_memory_mb` check in `linux_usb_preflight.py`.
- SDK init can fail if no camera is connected — handle the absence gracefully, don't crash on startup.

## Reconnect
- Cameras drop off USB occasionally — the reconnect path in `camera_connection.py` must remain idempotent. Don't add state that prevents repeated init attempts.
- Windows USB reset uses `CM_Reenumerate_DevNode()` via ctypes. Wrap in try/except and fall back gracefully on non-Windows or unprivileged sessions.
- There is deliberately **no USB reset on macOS or Linux** (issue #39). Recovery there is `stop_capture()`, the plain reconnect and the app-restart policy, so none of those may come to depend on a USB reset having happened.

## Disconnect / cleanup
- The cleanup safety net is layered, and since the camera package split it spans two classes: `ZWOCamera` owns `__del__` and `__enter__`/`__exit__` (`services/camera/zwo_camera.py`); `CameraConnection` owns the `_cleanup_lock` that serialises teardown (`services/camera/camera_connection.py`). Don't remove any of them — improper cleanup leaves the USB device hung until reboot.
- `stop_capture()` (`CaptureSessionBase`, `services/camera/capture_session_base.py`, shared by every backend) aborts the in-progress exposure and any running calibration, then joins the worker thread with a **3-second timeout**. Preserve that flow — the aborts are what make the short join safe. Dropping the aborts and keeping a short join will race; an unbounded join can deadlock shutdown.

## Per-camera profiles
- Camera-specific settings live under `camera_profiles[<key>]`, keyed by the **hardware serial**: settings belong to a physical body, not a model, and two ASI676MC bodies need different gains. The serial can only be read off an open handle (`ASIGetSerialNumber`, `services/camera/camera_identity.py`), so it is learned on the first clean connect.
- The model name — index suffix stripped — remains the fallback key for configs and cameras that predate the serial. A legacy name-keyed profile is copied to the serial key on first use so the body keeps its tuned settings; the name profile is left as a template for a same-model body that hasn't learned its own serial yet.
- `prune_bogus_profiles()` removes artefacts of the old name-based keying: keys matching `Camera <n>` or containing `(Index:`.
- Reach profiles through `Config`'s delegators (`config.get_camera_profile(name, serial)`) — don't import `services/camera_profiles.py` elsewhere.
- When a setting is missing from the per-camera profile, fall back to `DEFAULT_CAMERA_PROFILE` — never crash on a missing key.
- `scripts/fix_cameras.py` resets corrupted profiles. Don't roll your own reset logic in the main app.

## Auto-exposure
- Target mean brightness = **100**. Adjust by ±30% only when current brightness is outside the **80–120** band — keeps the loop from oscillating on noise.
- Always respect `max_exposure` from config when raising exposure.

## ASCOM Alpaca backend (`services/camera/alpaca/`, issue #43)
Read `docs/ALPACA_CAMERA_PLAN.md` (the Decisions at the top override the body) before structural changes. `camera_source: "zwo" | "alpaca"` is read only when `capture_mode == "camera"` — never add a third `capture_mode`; ask `services/camera/camera_source.py` (`camera_source`, `is_alpaca`, `alpaca_settings`, `active_camera_identity`) instead of reading the keys yourself.
- **Bayer pattern comes from the device, never assumed.** `alpaca_sensor` derives it from SensorType + BayerOffsetX/Y (+ StartX/StartY), and `flip_bayer_pattern` makes it follow the software flip. A profile's `bayer_pattern` is stored but never used to decode an Alpaca frame, and `apply_camera_settings` must not write it over the detected one. SensorType Color (rank-3 RGB) passes through as `'RGB'` (no Bayer FITS); CMYG / CMYG2 / LRGB read as `'MONO'` with a one-time warning.
- **Two Bayer vocabularies, and they are not the same string.** `physical_bayer_pattern()` is the ASCOM / FITS name (top-left tile, row by row). `bayer_pattern_for()` is the *pipeline* name — what `BAYER_PATTERN`, profiles and `debayer_raw_image` mean — which is the physical string reversed, because `debayer_raw_image` hands the name to OpenCV's `COLOR_Bayer<XY>2RGB` and OpenCV names a tile 180° turned (a physical RGGB mosaic decodes red only through `BG2RGB`; that is also why the ASI676MC above is "BGGR"). Feed the pipeline name to the debayer; show the physical one to people. Mixing them swaps red and blue silently.
- **Seconds on the wire.** StartExposure's `Duration` is seconds. The GUI's milliseconds become seconds once, when the session is built (`session_factory`, `exposure_ms / 1000.0`); the only other conversion is µs → s inside the device's zwoasi facade (`AlpacaCameraDevice.set_control_value(ASI_EXPOSURE, µs)`, which `CameraCalibration` calls). Don't add a third.
- **Per-device lock, never `SDK_LOCK`.** `AlpacaConnection.sdk_lock` is a per-instance `threading.Lock`; the process-global `SDK_LOCK` would make a network camera and a USB one wait on each other. Live writes take it with the same 2 s timed acquire as ZWO.
- **Profile key `alpaca:<UniqueID>`** (from the Management API), or `alpaca:<host>:<port>:<n>` before the UniqueID is learned; `persist_alpaca_identity` moves a host-keyed profile to the UniqueID key on the main thread. The prefix keeps `prune_bogus_profiles` off a device named "Camera 1". The connection never writes config and never touches the `zwo_*` keys. A saved UniqueID the server no longer lists fails the connect (the analogue of the serial gate); a renumbered device is followed by UniqueID.
- **No USB reset, no SDK.** No USB ladder, no Revive, no SDK reset, no app restart for an Alpaca session; recovery is the shared loop's reconnects plus the controller's restart schedule (an Alpaca start that fails before the first frame is retried under `camera_auto_recovery`). Gate ZWO-only paths on `capabilities.usb_reset` / `backend`, never on "not Alpaca".
- **`release_on_disconnect` (default off).** Off, `disconnect` aborts only our own exposure and never sends `Connected=false` (V3) / `Disconnect` (V4): on ASCOM Remote the connection may be shared with NINA, and dropping it would disconnect NINA too. Test Connection (`alpaca_probe`) follows the same rule and writes no gain / offset / ROI. `release_sdk=` is accepted and ignored.
- **LAN session ignores system proxies.** Every client is built from `lan_session()` (`alpaca_http`, re-exported by `alpaca_connection`; `trust_env = False`): Windows' proxy bypass does not cover bare LAN IPs, so a configured proxy would otherwise swallow every request. Keep injecting `session_factory` rather than constructing `requests.Session()` directly.
- **Every call is bounded** (connect/read timeouts, size-scaled download deadline, exposure + 30 s), so there is no C-level wedge to recover from.
- **Test against the simulator**, not a hand-rolled server: `python scripts/dev/alpaca_camera_sim.py --port 11112` serves a spec-following camera with UDP discovery (sensor type, Bayer offset, size, MaxADU, gain/offset modes, ImageBytes on/off are flags). Port 11111 is PFR's own SafetyMonitor default. In-process tests use `tests/alpaca_fake.py`; the real-socket tests (`test_alpaca_sim.py`, `test_alpaca_session_live.py`) are `requires_network`.
