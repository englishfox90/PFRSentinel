# ASCOM Alpaca camera source (issue #43): design and phased plan

Design and phased plan for issue #43. Line numbers are against `85cb511`; they drift as the phases land.

## Decisions (2026-11-22)

- **Seam:** shared `CaptureSessionBase` + duck-typed `ExposureHandle`; ZWO's capture path, quirks and SDK lock stay as they are. No third `capture_mode`; `camera_source: zwo|alpaca` under `capture_mode == "camera"`.
- **White balance:** a stored `asi_auto` runs as `gray_world` on an Alpaca session; config is not rewritten.
- **Start retry:** an Alpaca start that fails before the first frame is retried under `camera_auto_recovery` (a rig that boots before its Alpaca server). ZWO unchanged.
- **SensorType 1 (Color):** Rank-3 RGB passes through (`'RGB'` branch, no Bayer FITS). CMYG / CMYG2 / LRGB read as mono with a one-time warning.
- **Redaction:** `alpaca_camera.host` and `unique_id` masked by path; `webserver_host` stays visible.
- **Pre-existing bugs** (§1.11 items 1–2) fixed in their own commits.

## 0. Why this seam

Keep the idea of a protocol plus capability flags. Do not rewrite the ZWO path onto a normalised low-level `CameraDriver`. The code already has two seams, and building on them leaves shipping ZWO code nearly untouched.

**Evidence:**

1. **The capture loop is already duck-typed against a session object.**
   - `tests/test_camera_auto_recovery.py:100-145` drives `zwo_capture_worker.capture_loop` with a `_FakeCamera`. The members it uses are the de-facto session contract:
     - `is_capturing`, `scheduled_capture_mode`, `auto_exposure`, `calibration_complete`, `calibration_manager`
     - `camera` (truthy handle with `get_dropped_frames`, `stop_exposure`), `_connection.disconnect(release_sdk=)`
     - `status_callback`, `on_frame_callback`, `on_error_callback(msg, is_fatal)`, `log`
     - `is_within_scheduled_window`, `consume_immediate_capture`, `effective_capture_interval`
     - `capture_single_frame`, `reconnect_camera_safe`, `run_calibration`, `adjust_exposure_auto`
   - Plus attributes set by the loop: `_last_frame_time`, `long_retry_mode_public`, `_recovery_requested`.
2. **Tests pin the ZWO worker's source text.**
   - `tests/test_camera.py:371-392` checks that `"✓ Camera reconnected successfully"` is followed by `_last_frame_time = time.time()` in `capture_loop`.
   - `tests/test_camera.py:495-520` checks that `capture_single_frame` contains `"get_roi_format()"` and `"Frame size mismatch"`, and does not contain `"width = camera_info['MaxWidth']"`.
   - Rewriting the ZWO frame path onto a new driver would fight these tests and risk the hard-won quirks: the `ASI_EXP_FAILED` retry (`zwo_capture_worker.py:76-145`), the readout under `sdk_lock` with `call_with_timeout` (`:158-185`), the ROI byte check (`:187-196`) and the cold-open absorb (`:553-587`).
3. **`CameraCalibration` touches hardware through only a small zwoasi subset** (`camera_calibration.py:107-144, 216, 259, 262, 270, 451, 482`):
   - `camera.start_exposure()`, `get_exposure_status()` compared to `asi.ASI_EXP_SUCCESS`/`ASI_EXP_FAILED`, `get_data_after_exposure()`, `get_camera_property()['MaxWidth'/'MaxHeight']`
   - `set_control_value(asi.ASI_EXPOSURE, int(s*1e6))`
   - It is constructed in exactly one place (`zwo_camera.py:375`), plus one test (`test_auto_exposure.py:298`).
4. **The generic `stop_capture` flow works over any handle.** It does `sdk_lock.acquire(2.0)` → `camera.stop_exposure()` → `join(3.0)` (`zwo_camera.py:557-603`). It is backend-neutral as long as the connection exposes `sdk_lock` and the handle has `stop_exposure()`.

**Therefore the design is:**

- **A. `CaptureSession` protocol** (the controller-facing object). `ZWOCamera` already satisfies it. Add `AlpacaCamera` alongside it. Move the backend-neutral half of `ZWOCamera` (schedule gate, interval, capture-now, start/stop/join, calibration wrapper) into a shared base class, moved verbatim.
- **B. `ExposureHandle` protocol** (the zwoasi-`Camera` subset that the shared loop and `CameraCalibration` call), plus a `SdkConstants` namespace contract (`ASI_EXP_*`, `ASI_EXPOSURE`, …). This is your "driver protocol", shaped to what the existing shared code already calls, so ZWO's `zwoasi.Camera` satisfies it unchanged. The Alpaca device implements its native API plus this thin facade. Exactly one unit conversion lives in the facade: µs→s for `set_control_value(ASI_EXPOSURE, µs)`. Everything else is in seconds.
- **C. Per-backend modules.**
  - ZWO keeps `capture_single_frame`, `CameraConnection`, `camera_reconnect` (the USB ladder) and white balance.
  - Alpaca gets its own `capture_single_frame`, connection and reconnect.
  - Both build metadata through one shared `build_frame_metadata()`, so token keys are identical by construction.
- **D. `CameraCapabilities` dataclass.** A static version per source is used before connecting (to hide widgets); a dynamic version per session is used after connecting (ranges, bit depth, Bayer pattern).

**Open question on the controller:** reuse `ui/controllers/camera_controller.py` with a session factory. Its restart ladder (`_auto_recovery_schedule`, dying-camera join, restart policy) is generic. Only these parts are ZWO-specific: `_UNRECOVERABLE_ERROR_PATTERNS` (`:23-28`), the USB reset worker (`:591-598, 623-652`), `revive_missing_camera` (`:663-669`), `_resolve_camera_index` (`:145-154`), `_persist_learned_serial` (`:300-309`) and the construction in `_start_capture_worker` (`:251-278`). Gate these on capabilities.

**Open question on capture mode:** do not add a third `capture_mode`. I found about 25 checks of `capture_mode == 'camera'` / `== 'watch'`:

- `ui/main_window/capture.py:74,93,103,144,148,218-219`
- `ui/main_window/output.py:96-97,116,247,493,509`
- `ui/main_window/window.py:620`
- `ui/panels/capture_settings.py:94,96,157-159`
- `ui/panels/timelapse_panel.py:529-530`
- `services/discord_alerts.py:334-335,377-378`
- `services/config.py:322`
- `ui/controllers/diagnostics_controller.py:180`
- the public status API mode enum `'camera'|'watch'|'idle'` (`services/api_status.py:34`, `services/api_docs.py:455`, `headless_runner.py:445`).

Add a new key `camera_source: "zwo"|"alpaca"`, read only when `capture_mode == "camera"`. In the UI, the Capture-mode `SegmentedWidget` gets three segments (Directory Watch | ZWO Camera | Alpaca Camera). Each segment maps to the pair (`capture_mode`, `camera_source`). That meets the issue's "capture-mode selector" literally and keeps the API and every `== 'camera'` site unchanged.

**Profile keying:** use `camera_profiles["alpaca:<UniqueID>"]`. The UniqueID comes from `GET /management/v1/configureddevices`, matched on `DeviceType == "Camera"` and `DeviceNumber`. If it is missing, fall back to `"alpaca:<host>:<port>:<n>"`. The prefix means a bare-name key is never used, so `prune_bogus_profiles` (`camera_profiles.py:23-37`, which deletes `Camera <n>`) cannot hit an Alpaca device that happens to be named "Camera 1". `_resolve_key` (`camera_profiles.py:40-58`) copies a same-named legacy profile once as a template (for example "ZWO ASI676MC" through ZWO's ASCOM driver inherits the tuned gain). That is acceptable, but document it.

---

## 1. Inventory of ZWO touch points

### 1.1 SDK primitives
| Location | What | Alpaca equivalent |
|---|---|---|
| `services/zwo_sdk_library.py` (whole) | library locator | none; hide SDK path |
| `services/camera/camera_connection.py:143-181` `initialize_sdk` | zwoasi import + `asi.init` | none (`AlpacaCamera.initialize_sdk()` returns True, so headless can call it) |
| `camera_connection.py:183-219` `reset_sdk_completely` | SDK reset | none |
| `services/camera/sdk_lock.py` `SDK_LOCK` (process-global) | serialises the DLL | **per-device** `threading.Lock` on `AlpacaConnection`. Must not be `SDK_LOCK`, or ZWO and Alpaca would serialise each other. |
| `camera_utils.py:13-51` `call_with_timeout` / `SDKTimeoutError` | bound uninterruptible C calls | HTTP timeouts instead; not needed |
| `services/camera/camera_detect.py` (105), `camera_index_resolver.py` (118), `ui/main_window/camera_detect.py:17-190` | enumerate by SDK index | `alpaca_discovery.py` (UDP 32227 + management API) |
| `services/camera/camera_config.py:140-217` `configure_camera` | gain, exposure, `BANDWIDTHOVERLOAD=40`, `BRIGHTNESS`=offset, `FLIP`, ROI/RAW8-16 with retry | `AlpacaConnection.configure`: Gain/Offset clamped to Min/Max (or index into `Gains`/`Offsets`); BinX/BinY=1; StartX/StartY=0; NumX/NumY = CameraXSize/YSize |
| `camera_config.py:33-77` `wait_for_controls_ready` | ASI676MC cold boot | none |
| `ui/controllers/camera_controller.py:102-143` `detect_cameras` | **dead code**; the real path is `ui/main_window/camera_detect.py` | n/a |

### 1.2 Capture loop / frame
| Location | What | Alpaca |
|---|---|---|
| `zwo_capture_worker.py:26-37` `_get_temperature` (`ASI_TEMPERATURE/10`) | temperature tokens | `CCDTemperature` (°C float); NotImplemented → "N/A" |
| `zwo_capture_worker.py:40-248` `capture_single_frame` | exposure µs, `ASI_EXP_*` poll at 0.05 s, `EXP_FAILED` retry, readout + `get_roi_format` under lock, size check, `build_frame`, metadata dict (keys `:206-242`) | own `alpaca_capture_frame.capture_single_frame` |
| `zwo_capture_worker.py:251-750` `capture_loop` | generic recovery loop | **reuse as-is** (moved to `capture_loop.py`) |
| `zwo_capture_worker.py:262-276` | admin warning for gated mode, read via `getattr(conn,'_usb_disable_enable_func')` | skipped automatically (attribute absent) |
| `zwo_capture_worker.py:376` | off-peak `disconnect(release_sdk=True)` | `AlpacaConnection.disconnect` accepts the kwarg; there is no SDK to release |
| `zwo_capture_worker.py:518-527` `get_dropped_frames` | USB warning | facade returns 0 |
| `zwo_capture_worker.py:692-699` | USB/Device Manager troubleshooting text | needs a `troubleshooting_hints` hook (Phase 2) |
| `zwo_capture_worker.py:622-655` | 8 s pre-reconnect settle, 3 s post settle, warm-up absorb | keep as-is; also sensible for ASCOM Remote / driver re-handshake |
| `services/camera/frame_builder.py:189-217` `build_frame`, `camera_utils.py:214-264` `debayer_raw_image` | debayer (`bayer_map`; unknown → BGGR) | reuse; add `'MONO'` (and optionally `'RGB'`) branch |
| `services/raw_frame_export.py:34-46` | Bayer FITS from `RAW_BAYER` + `BAYER_PATTERN` | works if Alpaca bytes are row-major (H,W) uint8/uint16 |

### 1.3 Auto-exposure
| Location | What | Alpaca |
|---|---|---|
| `camera_calibration.py:71-277` `run_calibration` | uses the zwoasi subset above (`start_exposure` bounded at 10 s, poll `ASI_EXP_*`, `get_data_after_exposure`, reshape via MaxWidth/MaxHeight) | runs unchanged on the Alpaca facade (`get_camera_property` → `{'MaxWidth': NumX, 'MaxHeight': NumY,…}`) |
| `camera_calibration.py:315-494` `adjust_exposure_auto` | pure algorithm plus `set_control_value(ASI_EXPOSURE)` | facade stores the pending exposure |
| `zwo_camera.py:369-388, 686-745` | `_init_calibration_manager`, `run_calibration`, `adjust_exposure_auto` wrappers, PostHog `calibration_completed` | move to the shared base |
| Note (pre-existing) | calibration writes `ASI_EXPOSURE` **without** `sdk_lock` (`camera_calibration.py:216,259,262,270,451,482`) while UI live writes take it | preserve in Phase 1; flag |

### 1.4 Connection / reconnect / USB reset
| Location | Alpaca |
|---|---|
| `camera_connection.py:106-137` USB reset init, `_is_running_as_admin` | none |
| `camera_connection.py:234-508` `connect`: retry, name gate, serial gate (`:373-387`), config writes `zwo_selected_camera_name/serial` (`:394-406`), "Invalid size" → USB disable/enable (`:483-506`) | `AlpacaConnection.connect`: PUT `Connected` + identity gate on UniqueID. It must **not** write `zwo_*` keys. |
| `camera_connection.py:617-736` `disconnect` (`_cleanup_lock`, 5 s lock timeout, USB reset on close failure) | idempotent `disconnect` with its own `_cleanup_lock`; PUT `Connected=false` only if `release_on_disconnect` |
| `services/camera/camera_reconnect.py:11-357` (stable detection, `run_recovery_ladder` USB soft → SDK reset → disable/enable, `reconnect_without_recovery`, `reconnect_safe`) | `AlpacaConnection.reconnect_safe`: re-handshake → re-resolve device number by UniqueID → reconfigure. No ladder. |
| `services/usb_reset_win.py`, `ui/controllers/camera_usb_recovery.py` (`UsbResetWorker`) | none |
| `camera_controller.py:23-28` unrecoverable DLL patterns, `:344-350`, `:591-598` wedge USB toggle, `:623-652`, `:663-669` Revive | gate on `caps.usb_reset` / `caps.backend == 'zwo'` |
| `ui/controllers/camera_restart_policy.py` | generic; reuse |
| `ui/main_window/window.py:432-460` `_check_admin_privileges` | skip when source is alpaca |
| `ui/main_window/capture_watchdog.py:20-99` | generic (reads `_last_frame_time`, `long_retry_mode_public`, `_recovery_requested`); text at `:87` says "ZWO SDK", make it source-aware |

### 1.5 Identity / profiles
| Location | Alpaca |
|---|---|
| `config_defaults.py:79-86` (`zwo_sdk_path`, `zwo_camera_index`, `zwo_camera_name`, `zwo_selected_camera`, `zwo_selected_camera_serial`) | new `alpaca_camera{host,port,device_number,unique_id,name,…}` |
| `camera_identity.py:93-106` `persist_camera_serial` (writes `zwo_selected_camera_serial`) | `persist_alpaca_identity` (unique_id, name), written on the main thread |
| `camera_utils.py:63-73` `get_selected_camera_name` | new `active_camera_identity(config)` |
| `services/camera_profiles.py:40-88` | reuse with `serial="alpaca:<UniqueID>"` |
| Profile readers: `camera_controller.py:176-220`, `camera_settings.py:13-36`, `_camera_settings_widget.py:418-426, 564-571`, `headless_runner.py:141-162, 246-250`, `meteor_controller.py:599-606`, `diagnostics_controller.py:185`, `capture.py:218` | switch to the helper |
| `config_migrate.py:39-60` | untouched |

### 1.6 White balance
| Location | Alpaca |
|---|---|
| `camera_white_balance.py` (`ASI_WB_R/B`, `asi_auto`) | no hardware WB. Software path `apply_white_balance` (`camera_utils.py:267-309`): `gray_world`, `manual` + `apply_software_gains`. |
| `zwo_camera.py:648-659` `set_white_balance_live` | Alpaca: store the config only |
| `camera_settings.py:99-106` | same |

### 1.7 RAW16
| Location | Alpaca |
|---|---|
| `zwo_camera.py:407-462` `set_raw16_mode`, `camera_connection.py:410-418` | no toggle. Bit depth from MaxADU (≤255 → 8, else 16 with left-shift normalisation). |
| `camera_settings.py:135-156`, `capture.py:387-403` | `set_raw16_mode` returns False, and the switch is hidden |
| `dev_mode.use_raw16` (`config_defaults.py`) | ZWO only |

### 1.8 ZWO-specific UI
| Widget (file:line) | Alpaca treatment |
|---|---|
| `ui/panels/capture_settings.py:57-68` mode description + `'camera'` → "ZWO Camera" segment | 3 segments; description text per source |
| `_camera_settings_widget.py:65-80` SDK Path row + Browse (`:387-395`) | hide |
| `_camera_settings_widget.py:82-95` Camera combo + Detect (`:397-416`, `630-670`) | hide; show Alpaca connection card |
| `_missing_camera_notice.py` (Revive / Detect Again, `IS_WINDOWS`) `_camera_settings_widget.py:96-110` | hide |
| `_camera_settings_widget.py:124-129` gain spin | range from caps |
| `:271-345` WB card: `_WB_MODES=["asi_auto","manual","gray_world"]` (`:19`), hardware R/B sliders + `low_gain_warning` | filter out `asi_auto`; hide hardware sliders; optional software R/B gains |
| `:350-355` offset 0–255 | range from caps; hide if unsupported |
| `:357-361` flip | keep (software flip, Bayer pattern adjusted) |
| `:363-367` Bayer combo (`_BAYER_PATTERNS` `:20`) | disabled, showing the detected pattern |
| `:369-382, 672-698` RAW16 switch + status | hide; caption shows "N-bit (MaxADU m)" |
| `ui/panels/settings_panel.py:99-104` auto-recovery text mentions USB resets | add "(USB steps: ZWO only)" |
| `ui/panels/live_monitoring.py:495,504` "Connect a ZWO ASI camera" | "Connect a camera" |
| `ui/panels/timelapse_panel.py:76` "Camera (ZWO) capture mode" | "Camera capture mode" |
| `ui/main_window/camera_detect.py:61-65` startup auto-detect | skip when source is alpaca |
| `ui/main_window/capture.py:71-85` `_update_start_button` (needs `available_cameras`) | alpaca: host set |
| `ui/main_window/window.py:613-624` `_camera_ready` | alpaca: host set |
| `ui/main_window/capture.py:375-391` `_update_camera_capabilities` | pass caps |
| `ui/main_window/output.py:96-97`, `services/discord_alerts.py:334-335,377-378`, `output.py:704` "ZWO Camera Capture"/"ZWO Camera" | `capture_source_label(config)`; ZWO text stays identical |

### 1.9 Readers of `controller.zwo_camera` (keep the attribute, add a `camera_session` alias)
- `ui/controllers/camera_settings.py:122,143`
- `diagnostics_controller.py:160,201`
- `image_processor.py:225-227`
- `ui/components/app_bar.py:258-262`
- `ui/main_window/capture_watchdog.py:32`
- `output.py:519,707-710`
- `window.py:639-641`
- `capture.py:383`

All of these read generic members (`exposure_seconds`, `exposure_remaining`, `exposure_start_time`, `gain`, `auto_exposure`, `target_brightness`, `effective_capture_interval`, `schedule_gate`, `scheduled_window_label`, `is_in_time_window`, `supports_raw16`, `sensor_bit_depth`, `offset`, `max_exposure`, `use_raw16`, `bayer_pattern`, `wb_config`, `capture_thread`, `wait_for_capture_thread_exit`, `stop_capture`, `disconnect_camera`, `auto_recovery_enabled`, `camera_name`, `camera_serial`).

### 1.10 Headless, status API, diagnostics, tray, NINA
- **Headless:** `services/headless_runner.py:18,45,141-162,228-310` (builds `ZWOCamera`: `initialize_sdk` → `detect_cameras` → `connect_camera(index)`), `:352,360,384` (generic), `:437-445` (status, `mode="camera"`), `:535` disconnect. Headless never runs calibration (pre-existing). The Alpaca branch goes through the factory.
- **Status API:** `services/api_status.py:66-113` `build_schedule(config, zwo_camera)` is generic. Add an additive `camera_source` field to `CAPTURE_FIELDS` and `build_capture_snapshot`. `tests/test_api_status.py:109-114` only asserts a superset.
- **Diagnostics:** `diagnostics_controller.py:156-160, 176-188, 200-216`; `services/diagnostics_bundle.py:25-29` redaction markers (`serial` is masked; host and UniqueID are not).
- **Tray:** `ui/system_tray_qt.py:83,149-157` is generic and goes through `window.start_capture`.
- **NINA API:** `web_control`/`api_control` go through `window.start_capture`/`stop_capture` and are generic. NINA reads `mode` from status, which stays `"camera"`.

### 1.11 Pre-existing bugs found (report separately; do not fold into the refactor)
1. **`camera_controller.py:168` and `:320` call `self.zwo_camera.disconnect()`, but `ZWOCamera` has no `disconnect`** (only `disconnect_camera`; `_connection.disconnect`). The `AttributeError` is swallowed. At `:316-325` (worker connected, but the user pressed Stop meanwhile) the capture loop already started by `cam.start_capture` keeps running orphaned, with the USB handle open and frames still emitted. Fix: off-thread `stop_capture()` + `disconnect_camera()`, mirroring `stop_capture` at `:382-405`.
2. **`ui/main_window/capture.py:403`: `self.capture_panel.raw16_switch`.** `CaptureSettingsPanel` has no `raw16_switch` (it lives on `camera_widget`), so the revert-on-failure raises `AttributeError` inside the slot.
3. `CameraControllerQt.detect_cameras` (`:102-143`) and `ZWOCamera.update_exposure` (`zwo_camera.py:673-684`) are dead.
4. Mono ZWO bodies (for example the ASI462MM used in `tests/test_camera.py:140`) are debayered as BGGR. The new MONO branch makes a ZWO fix possible via `camera_info['IsColorCam']` later.

---

## 2. The interfaces

### 2.1 `services/camera/driver.py` (new, ~110 lines, typing only)
```python
class SdkConstants(Protocol):          # zwoasi module satisfies it; alpaca provides ALPACA_SDK
    ASI_EXP_IDLE: int      # 0
    ASI_EXP_WORKING: int   # 1
    ASI_EXP_SUCCESS: int   # 2
    ASI_EXP_FAILED: int    # 3
    ASI_GAIN: int          # 0
    ASI_EXPOSURE: int      # 1   value in microseconds
    ASI_BRIGHTNESS: int    # 5   offset
    ASI_TEMPERATURE: int   # 8   value in 0.1 °C
    ASI_FLIP: int          # 9
    ASI_IMG_RAW8: int      # 0
    ASI_IMG_RAW16: int     # 2

@runtime_checkable
class ExposureHandle(Protocol):         # zwoasi.Camera subset used by capture_loop + CameraCalibration
    def start_exposure(self, is_dark: bool = False) -> None: ...
    def stop_exposure(self) -> None: ...
    def get_exposure_status(self) -> int: ...                 # one of ASI_EXP_*
    def get_data_after_exposure(self, buffer_=None) -> bytes | bytearray: ...   # row-major H*W, uint8 or uint16 LE
    def get_camera_property(self) -> dict: ...                # MaxWidth, MaxHeight, Name, BitDepth, PixelSize, ElecPerADU, IsColorCam, SupportedVideoFormat
    def set_control_value(self, control_type: int, value: int, auto: bool = False) -> None: ...
    def get_control_value(self, control_type: int) -> tuple[int, bool]: ...
    def get_roi_format(self) -> tuple[int, int, int, int]: ...  # (w, h, bins, image_type)
    def get_dropped_frames(self) -> int: ...
    def close(self) -> None: ...
```
The values above are the real zwoasi values. I checked them in this environment: `0 1 2 3 / 0 1 5 8 9 / 0 2`.

### 2.2 `services/camera/session.py` (new, ~130 lines, typing only)
`CaptureSession(Protocol)` lists the full surface from §1.9 plus the loop contract from §0.1:

- **identity:** `backend: str`, `camera_name`, `camera_serial` (the profile serial; Alpaca uses `"alpaca:<UniqueID>"`), `capabilities: CameraCapabilities`
- **state:** `is_capturing`, `capture_thread`, `camera`, `asi`, `_connection`, `exposure_seconds`, `exposure_start_time`, `exposure_remaining`, `gain`, `offset`, `flip`, `bayer_pattern`, `wb_config`, `wb_mode`, `use_raw16`, `auto_exposure`, `max_exposure`, `target_brightness`, `capture_interval`, `calibration_manager`, `calibration_mode`, `calibration_complete`, `auto_recovery_enabled`, scheduling attributes (`scheduled_capture_mode`, `scheduled_capture_enabled`, `scheduled_start_time`, `scheduled_end_time`, `scheduled_window_interval`, `schedule_gate`), `supports_raw16`, `sensor_bit_depth`, `current_bit_depth`, `camera_info`, `_last_frame_time`, `long_retry_mode_public`, `_recovery_requested`
- **callbacks:** `on_frame_callback`, `on_log_callback`, `on_error_callback(msg, is_fatal=False)`, `on_calibration_callback(bool)`, `status_callback`
- **methods:** `initialize_sdk()`, `detect_cameras()`, `connect_camera(camera_index=0)`, `reconnect_camera_safe()`, `disconnect_camera()`, `start_capture(on_frame_callback, on_log_callback=None)`, `stop_capture()`, `wait_for_capture_thread_exit(timeout=10.0)`, `capture_single_frame()`, `capture_loop()`, `run_calibration()`, `adjust_exposure_auto(img_array)`, `is_within_scheduled_window()`, `is_in_time_window()`, `scheduled_window_label()`, `effective_capture_interval` (property), `request_immediate_capture()`, `consume_immediate_capture()`, `set_exposure(s)`, `set_gain(g)`, `set_capture_interval(s)`, `set_offset_live(v)`, `set_flip_live(v)`, `set_white_balance_live(mode, r, b)`, `set_raw16_mode(enabled) -> bool`, `log(msg)`

### 2.3 `services/camera/capabilities.py` (new, ~110 lines)
```python
@dataclass(frozen=True)
class CameraCapabilities:
    backend: str                      # 'zwo' | 'alpaca'
    display_name: str                 # 'ZWO ASI (USB)' | 'ASCOM Alpaca (network)'
    needs_sdk_path: bool              # SDK Path row
    index_selection: bool             # camera combo + Detect + missing-camera notice
    network_connection: bool          # host/port/device card + Discover + Test
    usb_reset: bool                   # Revive, USB ladder, admin warning (ZWO and IS_WINDOWS)
    hardware_white_balance: bool      # 'asi_auto' mode, R/B hardware sliders
    raw16_toggle: bool                # dynamic: supports_raw16
    flip: bool                        # ZWO hardware; Alpaca software (pattern-adjusted)
    offset: bool
    gain: bool
    bayer_from_device: bool           # Bayer combo read-only
    bayer_pattern: str | None         # detected (alpaca) or None
    bit_depth: int | None             # sensor/transfer depth
    gain_range: tuple[int, int] | None
    offset_range: tuple[int, int] | None
    exposure_range_s: tuple[float, float] | None
    temperature: bool
    troubleshooting_hints: tuple[str, ...]

ZWO_STATIC: CameraCapabilities       # needs_sdk_path, index_selection, usb_reset=IS_WINDOWS, hw WB, flip, offset, gain
ALPACA_STATIC: CameraCapabilities    # network_connection, software flip, bayer_from_device, no usb/hw-WB/raw16
def static_capabilities(source: str) -> CameraCapabilities: ...
def zwo_capabilities(connection) -> CameraCapabilities: ...   # dataclasses.replace(ZWO_STATIC, raw16_toggle=..., bit_depth=...)
```
`troubleshooting_hints` for ZWO must be the exact 5 strings at `zwo_capture_worker.py:693-699`.

### 2.4 `services/camera/errors.py` (new, ~40 lines)
- `CameraError(Exception)`
- `CameraDisconnectedError(CameraError)`
- `CameraTimeoutError(CameraError)`
- `CameraConfigurationError(CameraError)`

The Alpaca code raises these: `AlpacaTransportError` and `AlpacaNotConnectedError` subclass `CameraDisconnectedError`. **Phase 1 does not change any ZWO raise site.** The loop and controller key on message text (`_UNRECOVERABLE_ERROR_PATTERNS`, "Camera closed" warm-up), and there is no gain from touching them. Optionally make `SDKTimeoutError` subclass `CameraTimeoutError`; that is behaviour-neutral because every catch is `except Exception` or `except SDKTimeoutError`.

### 2.5 Adapting ZWO (Phase 1, verbatim moves)
- **`services/camera/capture_session_base.py`** (new, ~330 lines): `class CaptureSessionBase`. Moved verbatim from `zwo_camera.py`:
  - the generic `__init__` state (`:31-36` callbacks, `:51-109` capture/schedule/exposure/calibration state, minus the ZWO-only `white_balance_r/b`, `wb_mode`, `offset`, `flip`, `use_raw16`, which stay in ZWO `__init__` in the same order)
  - `log` `:190-194`, `_gate_verdict` `:196-210`, `_legacy_window`, `is_within_scheduled_window`, `is_in_time_window`, `scheduled_window_label` `:212-246`
  - capture-now `:248-257`, `effective_capture_interval` `:259-264`, `_init_calibration_manager` `:369-388`, `capture_loop` (delegates to the shared loop)
  - `start_capture` `:495-516`, `wait_for_capture_thread_exit` `:518-555`, `stop_capture` `:557-603`
  - `set_exposure` `:605-607`, `set_gain` `:609-611` (clamp via class attribute `_GAIN_LIMITS = (0, 600)`), `set_capture_interval`, `run_calibration` `:686-727`, `adjust_exposure_auto` `:729-745`
- **Keep `__del__`, `__enter__` and `__exit__` literally in `ZWOCamera`.** `.claude/rules/services-camera.md` ("Disconnect / cleanup") names `zwo_camera.py` as their owner. `AlpacaCamera` defines its own.
- **`ZWOCamera(CaptureSessionBase)`** keeps the ZWO parts:
  - the `CameraConnection` wiring, legacy aliases and properties `:115-172`
  - `initialize_sdk`, `reset_sdk_completely`, `detect_cameras`, `reconnect_camera_safe`, `connect_camera`, `_configure_camera`, `set_raw16_mode`, `disconnect_camera`, `capture_single_frame`
  - `_set_control_live`, `_run_live`, `set_white_balance_live`, `set_offset_live`, `set_flip_live`, `update_exposure`
  - new: `backend = 'zwo'`, `capabilities` property
  - Expect about 430 lines.
- **`services/camera/capture_loop.py`** (new, ~510 lines): `capture_loop` moved verbatim. `zwo_capture_worker.py` re-exports it: `from .capture_loop import capture_loop  # re-exported for existing callers`. `zwo_capture_worker.py` drops to about 220 lines.
- **`services/camera/frame_metadata.py`** (new, ~100 lines):
  - `temperature_fields(celsius: float | None) -> dict` keeps the `display`/`celsius_str`/`fahrenheit_str` formatting from `:29-37` exactly.
  - `build_frame_metadata(*, camera_name, exposure_seconds, gain, start_time, temp, width, height, stats, arrays, raw_bytes, bit_depth, bayer_pattern, wb_config, camera_bit_depth, pixel_size, elec_per_adu) -> dict` uses the exact key order of `:206-242`: `CAMERA, EXPOSURE, EXPOSURE_START_UTC, GAIN, TEMP, TEMPERATURE, TEMP_C, TEMP_F, RES, CAPTURE AREA SIZE, FILENAME, SESSION, DATETIME, BRIGHTNESS, MEAN, MEDIAN, MIN, MAX, STD_DEV, P25, P75, P95, RAW_RGB_NO_WB, RAW_RGB_16BIT, RAW_BAYER, RAW_GEOMETRY, WB_CONFIG, CAMERA_BIT_DEPTH, IMAGE_BIT_DEPTH, BAYER_PATTERN, PIXEL_SIZE, ELEC_PER_ADU`.
  - ZWO's `capture_single_frame` calls it, but keeps `get_roi_format()` and `"Frame size mismatch"` in its own body (pinned by tests).
- **`services/camera/session_params.py`** (new, ~110 lines): `read_capture_params(config, auto_recovery: bool) -> dict` moves `camera_controller.py:174-220` verbatim. Phase 2/3 adds the alpaca keys.
- **`services/camera/session_factory.py`** (new, ~90 lines): `build_zwo_session(params, gate, camera_index, on_error, on_calibration) -> ZWOCamera` moves `camera_controller.py:251-278` verbatim, including the order of the post-construction attribute writes. Phase 2 adds `build_alpaca_session(params, gate, on_error, on_calibration) -> AlpacaCamera` and `build_session(params, …)` dispatching on `params['camera_source']`.
- **Controller:** add `@property camera_session` (returns `self.zwo_camera`; storage name unchanged) and a `capabilities()` helper.

### 2.6 Risky spots: must not change
- **`sdk_lock`:** every `with sdk_lock` and timed acquire in `zwo_capture_worker.py:68,83,124,158`, `zwo_camera.py:426, 577-586, 631-646`, `camera_connection.py:300-308, 682-719`. Moving `stop_capture` to the base must keep `self._connection.sdk_lock`. `ZWOCamera._connection` is a `CameraConnection` whose `sdk_lock is SDK_LOCK`.
- **The cleanup safety net:** `ZWOCamera.__del__`/`__enter__`/`__exit__`, `CameraConnection._cleanup_lock`, and `disconnect` clearing `self.camera` in `finally`.
- **The 3-second `stop_capture` join with prior aborts** (calibration abort → locked `stop_exposure` → join 3.0, keeping the reference when the thread is alive): move it verbatim.
- **Cold-open quirk:** `warmup_pending`/`reconnect_spent` semantics in the loop (`:311-313, 432, 442, 548-587, 644`). This is a pure move.
- **Auto-recovery ladder:** `camera_reconnect.py` and `CameraConnection.connect` USB escalation are untouched (`camera_connection.py` is frozen at 757 lines, ceiling 788).
- **Test monkeypatching:** `tests/test_camera_auto_recovery.py:149-152` monkeypatches `zwo_capture_worker.time` and `.capture_error`. After the move this **silently stops patching** (the real `time.sleep` would run: 8 s + 3 s waits per cycle, and tests hang). Change the fixture in the same commit to patch `services.camera.capture_loop`. Source-inspection tests at `tests/test_camera.py:382-383` still pass, because `inspect.getsource` follows the function object.
- **Constructor ordering:** `__init__` sets `on_log_callback` **before** `CameraConnection(logger=self.log)` (`zwo_camera.py:31-38`). The base `__init__` must run first, or at least set the callbacks first.
- **Patch target:** `tests/test_camera.py:228,920` patch `services.camera.zwo_camera.CameraConnection`. Keep the import name in `zwo_camera.py`.

---

## 3. Alpaca module design (`services/camera/alpaca/`)

| File | ~Lines | Responsibility |
|---|---|---|
| `__init__.py` | 15 | re-export `AlpacaCamera`, `discover_cameras` |
| `alpaca_protocol.py` | 110 | Error numbers (`NOT_IMPLEMENTED=0x400`, `INVALID_VALUE=0x401`, `VALUE_NOT_SET=0x402`, `NOT_CONNECTED=0x407`, `INVALID_OPERATION=0x40B`, driver 0x500-0xFFF). `CameraState` (0 Idle, 1 Waiting, 2 Exposing, 3 Reading, 4 Download, 5 Error). `SensorType` (0 Mono, 1 Color, 2 RGGB, 3 CMYG, 4 CMYG2, 5 LRGB). Exceptions: `AlpacaError(CameraError)` (number, message), `AlpacaNotImplementedError(AlpacaError)`, `AlpacaNotConnectedError(CameraDisconnectedError)`, `AlpacaTransportError(CameraDisconnectedError)`. `ALPACA_SDK` `SimpleNamespace` with zwoasi-identical constant values. |
| `alpaca_http.py` | 220 | `AlpacaHttpClient(host, port, device_number, *, connect_timeout=3.0, request_timeout=10.0, session_factory=requests.Session)`. `get(member, timeout=None)`, `put(member, timeout=None, **params)`, `get_image(timeout, use_imagebytes, abort_event)`, `management_configured_devices()`. Every request carries `ClientID` (random 1..2^31-1 per process) and `ClientTransactionID` (`itertools.count`). GET uses query params; PUT uses form-encoded fields with exact casing (`Connected`, `Duration`, `Light`, `Gain`, `Offset`, `BinX`, `BinY`, `StartX`, `StartY`, `NumX`, `NumY`). Response keys are read case-insensitively. `ErrorNumber != 0` maps to an exception class. `requests.ConnectionError`/`Timeout`/HTTP 5xx/bad JSON raise `AlpacaTransportError`; HTTP 400 raises `AlpacaError(INVALID_VALUE)`. A `_lock` serialises short calls; image download uses a **second** `Session` with no lock, `stream=True` and `iter_content(1 MiB)` checking `abort_event`, so a stop or recovery can interrupt a long download without waiting. |
| `alpaca_imagebytes.py` | 170 | `parse_imagebytes(payload) -> np.ndarray(H,W)`. Header is `struct '<11i'` (44 bytes): MetadataVersion(=1), ErrorNumber (non-zero → UTF-8 message at DataStart → raise), ClientTransactionID, ServerTransactionID, DataStart, ImageElementType, TransmissionElementType, Rank, Dim1, Dim2, Dim3. Transmission dtype map: 1 int16, 2 int32, 3 float64, 4 float32, 5 uint64, 6 uint8, 7 int64, 8 uint16, 9 uint32 (little-endian). Data is `[x][y]`: `np.frombuffer(..., count=Dim1*Dim2).reshape(Dim1, Dim2).T`, then `np.ascontiguousarray`. Rank 3 → `(Dim1,Dim2,Dim3).transpose(1,0,2)`. Validates length. `parse_imagearray_json(obj)` is the fallback for `Type`/`Rank`/`Value` (`np.asarray(Value, dtype=np.int64).T`). The client chooses by response `Content-Type`; send `Accept: application/imagebytes` when `use_imagebytes` is set. |
| `alpaca_sensor.py` | 110 | Pure functions. `bayer_pattern_for(sensor_type, offset_x, offset_y, start_x=0, start_y=0) -> 'RGGB'|'GRBG'|'GBRG'|'BGGR'|'MONO'|'RGB'`: base RGGB, shift columns by `(offset_x+start_x)%2` and rows by `(offset_y+start_y)%2`. That gives (0,0) RGGB, (1,0) GRBG, (0,1) GBRG, (1,1) BGGR. Do not assume BGGR. CMYG/CMYG2/LRGB → `'MONO'` with a one-time warning. `flip_bayer_pattern(pattern, flip)` (h flip swaps columns, v flip swaps rows). `to_raw_bytes(arr, max_adu) -> (bytes, bit_depth)`: if `max_adu <= 255`, clip to uint8 (8-bit); otherwise clip to [0, max_adu] and left-shift by `16 - max_adu.bit_length()` into uint16 (16-bit). This matches ZWO's MSB-aligned RAW16, so `debayer_raw_image`'s `>>8` and the 16-bit histogram stay correct. |
| `alpaca_discovery.py` | 150 | `discover_servers(timeout=2.0, port=32227) -> list[(ip, alpaca_port)]`: UDP socket bound `('', 0)`, `SO_BROADCAST`, send `b"alpacadiscovery1"` to `255.255.255.255` (plus `<broadcast>`), collect `{"AlpacaPort": n}` replies until timeout, dedupe. `discover_cameras(timeout) -> list[AlpacaDeviceInfo(host, port, device_number, name, unique_id)]` via `GET /management/v1/configureddevices`, filtered on `DeviceType.lower() == 'camera'`. That filter excludes our own SafetyMonitor (`services/alpaca_safety/server.py:18-19` answers on the same UDP port). A socket factory is injectable for tests. IPv6 multicast is out of scope. |
| `alpaca_device.py` | 330 | `AlpacaCameraDevice(client)`. **Native API:** `connect(timeout=30)`, `disconnect()`, `read_static()` → `AlpacaDeviceInfo`-like dataclass (Name, Description, DriverInfo, DriverVersion, InterfaceVersion, SensorName, SensorType, BayerOffsetX/Y only when SensorType ≥ 2, CameraXSize/YSize, PixelSizeX, ElectronsPerADU, MaxADU, ExposureMin/Max, CanAbortExposure, CanStopExposure, Gain mode (GainMin/GainMax or Gains), Offset mode (OffsetMin/OffsetMax or Offsets), CanGetCoolerPower/CCDTemperature probe, MaxBinX; each optional item tolerant of NotImplemented → None). `start(duration_s, light=True)`, `image_ready()`, `camera_state()`, `abort()` (Abort if CanAbort, else Stop if CanStop, else no-op; 2 s timeout), `download(abort_event) -> np.ndarray`, `ccd_temperature()`, `set_gain/set_offset`, `set_full_frame_roi()`. **zwoasi facade (implements `ExposureHandle`):** `set_control_value(ASI_EXPOSURE, µs)` stores `µs/1e6` clamped to [ExposureMin, ExposureMax]; `ASI_GAIN` → `set_gain`; `ASI_BRIGHTNESS` → `set_offset`; `ASI_FLIP` → store. `start_exposure()` → `start(pending_s)`. `get_exposure_status()`: ImageReady → SUCCESS; CameraState 5 → FAILED; Idle without ImageReady within `pending_s + 2 s` of start → WORKING, otherwise IDLE (some drivers read Idle briefly after StartExposure). `get_data_after_exposure()` → `to_raw_bytes(download())`. `get_camera_property()` → `{'Name','MaxWidth','MaxHeight','BitDepth','PixelSize','ElecPerADU','IsColorCam','SupportedVideoFormat':[0]}`. `get_control_value(ASI_TEMPERATURE)` → `(int(round(t*10)), False)`. `get_roi_format()` → `(NumX, NumY, BinX, RAW16 if bit_depth==16 else RAW8)`. `get_dropped_frames()` → 0. `stop_exposure()` → `abort()`. `close()` → `disconnect()` (a no-op unless `release_on_disconnect`). |
| `alpaca_connection.py` | 280 | `AlpacaConnection` mirrors the `CameraConnection` surface used by the shared code: `camera` (device or None), `asi=ALPACA_SDK`, `sdk_lock` (**per instance**), `_cleanup_lock`, `camera_name`, `camera_serial` (profile key), `unique_id`, `camera_info`, `supports_raw16=False`, `bit_depth`, `current_bit_depth`, `auto_recovery_enabled`. `connect(settings)`: build client → resolve device number by `unique_id` via the management API if set (a server may renumber devices; this is the analogue of the serial-first resolver) → PUT Connected=true → `read_static` → identity gate (UniqueID mismatch → fail, like the ZWO serial gate) → `configure`. `configure(settings)`: gain/offset clamp, full-frame ROI, BinX=1. `reconnect_safe(...)`: one handshake + configure; on `auto_recovery_enabled=False` exactly one attempt. `disconnect(stop_exposure_callback=None, release_sdk=False)`: idempotent; abort exposure with 2 s timeout; PUT Connected=false only if `release_on_disconnect`; `release_sdk` is ignored. `verify_identity()`. **Never writes config.** |
| `alpaca_camera.py` | 330 | `AlpacaCamera(CaptureSessionBase)`. `backend='alpaca'`, `_GAIN_LIMITS` from caps, `troubleshooting_hints` (server reachable, driver connected in ASCOM Remote, firewall on port, device number). `initialize_sdk()` → True. `detect_cameras()` → `[f"{name} (Alpaca {host}:{port} #{n})"]` from the management API. `connect_camera(camera_index=0)` (index ignored), `reconnect_camera_safe`, `disconnect_camera`, `capture_single_frame` → `alpaca_capture_frame`. `set_offset_live` / `set_flip_live` / `set_white_balance_live` store values and push gain/offset through `_run_live` with the per-device lock (2 s timed acquire, same UX as ZWO). `set_raw16_mode` → False. `effective_wb_config()`: when `asi_auto`, use `gray_world`; when `manual`, force `apply_software_gains=True`. Has its own `__del__`/`__enter__`/`__exit__` → `disconnect_camera`. `capabilities` is dynamic (pattern, bit depth, ranges). |
| `alpaca_capture_frame.py` | 220 | `capture_single_frame(session)`: honour `_recovery_requested`; clamp to `max_exposure` (same message as ZWO) and to ExposureMin/Max; PUT gain only on change. `start(exposure_seconds)` takes seconds: the GUI's ms → s happens once, in `session_params` (`/1000.0`). Sleep in 0.2 s slices until `exposure - 0.2 s` (checking `is_capturing`/`_recovery_requested`, abort on either), then poll `imageready` every 0.1 s (0.25 s if exposure > 1 s), with a `camerastate` liveness check every ~2 s. Tolerate up to 3 consecutive transient poll failures (1 s apart) before raising. Exposure timeout is `exposure + 30 s`. Download timeout is `max(30, frame_bytes / 1 MB/s)`. Then `to_raw_bytes`, software flip on the mosaic plus `flip_bayer_pattern`, `bayer_pattern_for(...)`, `build_frame(...)` and `build_frame_metadata(...)`: `CAMERA`=Name, TEMP from CCDTemperature, `CAMERA_BIT_DEPTH` = bit_length(MaxADU), `PIXEL_SIZE`=PixelSizeX, `ELEC_PER_ADU`=ElectronsPerADU. Records `last_download_s` and bytes for diagnostics. |

**Shared edits in Phase 2:**
- `capture_loop.py:692-699`: `hints = getattr(camera, 'troubleshooting_hints', None) or _ZWO_HINTS`. The ZWO default tuple is the exact current 5 strings.
- `camera_utils.debayer_raw_image`: add `if bayer_pattern == 'MONO': rgb = cv2.cvtColor(img_array, cv2.COLOR_GRAY2RGB)` (and optionally `'RGB'` → reshape (H,W,3)) **before** the `bayer_map` lookup, so the existing patterns and the BGGR default are untouched.
- `session_factory.build_alpaca_session`.

**Threading:**
- The capture thread is owned by `CaptureSessionBase.start_capture`.
- Discovery and Test Connection run on controller daemon threads.
- No Qt anywhere in `services/camera/alpaca`.

**24/7 reconnect policy:**
- The shared loop gives 5 reconnects (8 s settle, 2^n backoff capped at 32 s), then long-retry at 5 m / 15 m / 1 h. The controller ladder (30, 120, 300, 900, 1800 s) and the restart policy follow.
- Error mapping: NotConnected (0x407) or transport error → `CameraDisconnectedError` → the loop reconnects. `InvalidValue` on gain/offset → log once, clamp, continue. `NotImplemented` on optional properties → capability off.
- There is no C-level wedge (every call is bounded), so the wedge-USB branch is skipped for Alpaca.

---

## 4. Config

`services/config_defaults.py`. `Config.load` merges dicts one level deep (`services/config.py:254-261`), so new nested keys land on old configs, and old configs get `camera_source="zwo"` with no behaviour change. No migration is needed.

```python
"camera_source": "zwo",          # "zwo" | "alpaca"; read only when capture_mode == "camera"
"alpaca_camera": {
    "host": "",                    # IP / hostname of the Alpaca server
    "port": 11111,                 # ASCOM default; note PFR's own SafetyMonitor default is also 11111
    "device_number": 0,
    "unique_id": "",               # learned on connect: identity gate + profile key
    "name": "",                    # last-seen device Name (display/logs only)
    "use_imagebytes": True,        # Accept: application/imagebytes; JSON fallback when off or unsupported
    "connect_timeout_s": 3.0,
    "request_timeout_s": 10.0,
    "release_on_disconnect": False # PUT Connected=false on stop (off: never disconnect a device another client may share)
},
```

New `services/camera/camera_source.py` (pure, ~110 lines):
- `camera_source(config)` (validated; unknown → `'zwo'`)
- `alpaca_profile_key(alpaca_cfg)` → `"alpaca:<UniqueID>"`, or `"alpaca:<host>:<port>:<n>"`
- `active_camera_identity(config) -> (name, profile_serial)`. The ZWO branch is exactly the current `get_selected_camera_name` + `zwo_selected_camera_serial`.
- `capture_source_label(config)`. ZWO returns the exact current "ZWO Camera Capture".
- `camera_ready(config, controller)`, a replacement for `window._camera_ready` that is net-negative in lines.
- `persist_alpaca_identity(config, unique_id, name) -> bool`.

Profiles are reached only through `config.get_camera_profile(name, serial)` (rule: don't import `camera_profiles` elsewhere).

Diagnostics redaction: add the marker `'unique_id'`, and mask `alpaca_camera.host` unless it is loopback (a small post-pass in `redact_config`). A generic `'host'` marker would also mask `webserver_host` (see §7).

---

## 5. UI

**Placement.** `ui/panels/capture_settings.py:64-68`: three segments, `watch` / `zwo` / `alpaca`.
- `_on_mode_changed(key)` writes `capture_mode` (`'watch'` or `'camera'`) and `camera_source`, then calls `camera_widget.apply_capabilities(static_capabilities(source))`.
- `load_from_config` selects the segment from the pair.
- The description text depends on the source.
- New signals: `alpaca_discover_clicked`, `alpaca_test_clicked`.

**`_camera_settings_widget.py`** (698 lines; extract before adding):
- Move `_build_connection_card` (`:65-110`) and the SDK/detect slots (`:387-416`) into **`ui/panels/_zwo_connection_card.py`**.
- New **`ui/panels/_alpaca_connection_card.py`**:
  - host `LineEdit`, port `SpinBox` (1-65535), device `SpinBox` (0-99)
  - Discover `PushButton` plus a results `ComboBox` ("Name — host:port #n")
  - Test Connection `PushButton` plus a status `CaptionLabel`
  - writes `alpaca_camera.*` and clears `unique_id` when host/port/device is edited (like `_on_camera_selected` clearing the serial)
- New **`ui/panels/_camera_capability_gate.py`**: `apply_capabilities(widget, caps)`, display logic only.
- `_save_to_camera_profile` / `load_from_config` use `active_camera_identity`. The legacy `zwo_{key}` mirror (`:425-426`) is written only for zwo.

**Hide/disable list per widget for Alpaca** (all ZWO behaviour unchanged):
- **ZWO connection card:** SDK Path row, camera combo, Detect, `MissingCameraNotice` with Revive / Detect Again → hidden (`needs_sdk_path`, `index_selection`, `usb_reset` false).
- **Alpaca connection card:** shown (`network_connection`).
- **Exposure / Interval / Auto-exposure (target, max exposure):** shown. After connect, the max-exposure spin maximum is limited to `exposure_range_s[1]`.
- **Gain spin:** range = `gain_range`; disabled if `gain` is false. In list mode the tooltip lists the `Gains` names.
- **Scheduled capture card:** shown, unchanged.
- **WB mode combo:** items filtered to `["manual","gray_world"]`. If the stored mode is `asi_auto`, show `gray_world` without writing config; the load guard keeps config unchanged, and switching back to ZWO restores `asi_auto`.
- **Hardware R/B sliders and low-gain warning:** hidden. Optional software Red/Blue gain spins bound to `white_balance.manual_red_gain`/`manual_blue_gain` are shown in manual mode when `hardware_white_balance` is false.
- **Offset spin:** range = `offset_range`; hidden if `offset` is false.
- **Flip combo:** shown (software flip).
- **Bayer combo:** disabled, set to the detected pattern after connect, tooltip "Reported by the camera (SensorType/BayerOffset)".
- **RAW16 switch:** hidden. `raw16_status` shows "16-bit transfer (MaxADU 4095 → 12-bit sensor)" or "8-bit".

**Discovery flow.** New **`ui/controllers/alpaca_discovery_controller.py`** (~120 lines, `QObject`):
- Signals: `devices_found = Signal(list, str)`, `test_result = Signal(bool, str, dict)`.
- `discover()` → daemon thread → `discover_cameras(2.0)` → emit.
- `test_connection(params)` → daemon thread → `AlpacaConnection.connect` + `read_static` + disconnect → emit a summary.
- The main window wires panel signals → controller → panel setters, and guards re-entrancy like `_detection_in_progress` (`camera_detect.py:67-77`).

**Controller and main-window edits:**
- **`camera_controller.py`:**
  - `start_capture` builds params via `read_capture_params` (with `camera_source`).
  - The worker skips `_resolve_camera_index` for alpaca and builds via `build_session`.
  - `_is_unrecoverable_error` and both USB-reset entry points return early unless `capabilities().usb_reset`. A wedged Alpaca thread reschedules, then escalates after `_MAX_WEDGED_SKIPS`.
  - `_persist_learned_serial` dispatches to `persist_alpaca_identity` (main thread).
  - Watch the size: 726 now, about 650 after Phase 1; if needed, move the wedge/USB helpers to `ui/controllers/camera_wedge_recovery.py`.
- **`camera_settings.apply_camera_settings`:** the identity guard compares `active_camera_identity` with the session's `camera_serial`/`camera_name`. Skip the Bayer assignment (`:86-87`) when `caps.bayer_from_device`, or the profile Bayer value clobbers the detected pattern on every settings change. Skip offset when unsupported.
- **`ui/main_window/camera_detect.py:61`:** return early when source is alpaca.
- **`capture.py:71-85`:** Start button alpaca branch (host non-empty).
- **`capture.py:375-391`:** `capture_panel.apply_camera_capabilities(cam.capabilities)`; keep `update_camera_capabilities` as a wrapper.
- **`window.py`** (747 lines: edits must be net ≤ 0): `_camera_ready` → `camera_ready(...)` helper; `_check_admin_privileges` skips alpaca, and the helper call replaces lines.
- **`output.py`** (738 lines): discord mode text via `capture_source_label`; `build_capture_snapshot(..., camera_source=...)`.
- **`capture_watchdog.py:87`:** source-aware text.

---

## 6. Phased plan

### Phase 1: ZWO behind the session/handle seam (no behaviour change)
**Create:**
- `services/camera/driver.py`, `session.py`, `capabilities.py`, `errors.py`
- `capture_session_base.py`, `capture_loop.py`, `frame_metadata.py`
- `session_params.py`, `session_factory.py`

**Modify:**
- `zwo_camera.py` (inherit; move generic code out)
- `zwo_capture_worker.py` (loop moved out + re-export; metadata via helper)
- `services/camera/__init__.py` (exports)
- `ui/controllers/camera_controller.py` (params/factory; `camera_session` alias)
- `tests/test_camera_auto_recovery.py` fixture (patch `capture_loop` module)
- `.claude/rules/services-camera.md` cleanup sentence if any owner moved

**Tests to add first (characterisation, against current code):**
- `tests/test_frame_metadata.py`: drive ZWO `capture_single_frame` with a mocked SDK (reuse the pattern at `tests/test_camera.py:938-963`); assert exact keys, order and values for a fixed clock. Then refactor and re-run.
- `tests/test_camera_session_contract.py`: `ZWOCamera` has every `CaptureSession` member; `zwoasi.Camera` method names cover `ExposureHandle`.
- `tests/test_capabilities.py`.

**Must not change:**
- log strings
- `sdk_lock` acquisition points and timeouts
- the 2.0 s / 3.0 s / 5.0 s / 8.0 s values
- PostHog event names and properties
- the `ZWOCamera` constructor signature
- the patch target `services.camera.zwo_camera.CameraConnection`
- `camera_connection.py` and `camera_reconnect.py` (not touched at all)

**Parallel lanes (no file overlap):**
- **A:** `capture_loop.py` + `frame_metadata.py` + `zwo_capture_worker.py` + the auto-recovery test fixture + `test_frame_metadata.py`.
- **B:** `capture_session_base.py` + `zwo_camera.py`. Import `capture_loop` from `zwo_capture_worker` (the re-export) so it doesn't depend on A's timing.
- **C:** `session_params.py` + `session_factory.py` + `camera_controller.py`.
- **D:** `driver.py`, `session.py`, `capabilities.py`, `errors.py` + contract tests.
- Merge order D → A → B → C. Run the full default suite (`pytest -m "not requires_camera and not requires_network and not requires_ml_models"`) and `scripts/ci/check_file_sizes.py`.

### Phase 2: Alpaca services and tests (no UI)
**Create:**
- `services/camera/alpaca/{__init__,alpaca_protocol,alpaca_http,alpaca_imagebytes,alpaca_sensor,alpaca_discovery,alpaca_device,alpaca_connection,alpaca_camera,alpaca_capture_frame}.py`
- `services/camera/camera_source.py`
- `tests/alpaca_fake.py`: an in-process fake following the `tests/allsky_synth.py` helper pattern. It is a `requests.Session` stand-in routing `/api/v1/camera/{n}/<member>` and `/management/v1/configureddevices` to a `FakeAlpacaCamera` state machine with:
  - injectable clock, SensorType/offset/MaxADU/size
  - a scene generator (known Bayer mosaic)
  - ImageBytes or JSON-only response mode
  - fault injection: N `ConnectionError`s, ErrorNumber 0x407, Timeout on download, renumbered device, Idle-after-start blip

**Modify:**
- `capture_loop.py` (troubleshooting hook)
- `camera_utils.debayer_raw_image` (MONO/RGB branch)
- `session_factory.py` (`build_alpaca_session`, `build_session`)
- `session_params.py` (alpaca keys)
- `capabilities.py` (`ALPACA_STATIC`)
- `config_defaults.py` (keys in §4)

**Tests (default suite, no sockets):**
- `test_alpaca_imagebytes.py`: header fields, every element type, exact `[x][y]` transpose on a non-square frame, Rank 3, error payload, truncated payload, JSON fallback equals ImageBytes.
- `test_alpaca_sensor.py`: 4 offsets × start parity, mono, CMYG→MONO warning, flip adjust, MaxADU 255/4095/16383/65535 normalisation.
- `test_alpaca_http.py`: ClientID and incrementing ClientTransactionID present, case-insensitive keys, error mapping, timeout → transport error.
- `test_alpaca_discovery.py`: fake UDP socket, JSON reply parse, Camera filter excludes SafetyMonitor, dedupe.
- `test_alpaca_connection.py`: handshake, UniqueID gate, renumbered device resolved by UniqueID, idempotent disconnect, `release_on_disconnect` respected.
- `test_alpaca_camera.py`:
  - metadata keys and order equal ZWO's (import the key list from the Phase 1 golden test)
  - Duration sent = profile ms / 1000
  - `max_exposure` and ExposureMin/Max clamps
  - abort on `stop_capture` within 3 s during a long download
  - `_recovery_requested`
  - poll blip tolerance
  - colour correctness: red-top/blue-bottom scene under all 4 offsets shows R high where expected
- `test_alpaca_capture_loop.py` (shared loop + fake, patched clock):
  - outage → reconnect → resume
  - recovery off → one reconnect then fatal and handle released
  - gated off-peak never PUTs `Connected=false` by default
  - Alpaca troubleshooting hints used
- `test_alpaca_calibration.py`: `CameraCalibration` converges on the facade; µs→s conversion only in the facade.
- `test_camera_source.py`.
- Extend `test_camera_session_contract.py` to `AlpacaCamera`.
- `test_debayer_mono.py` (new branch; existing patterns unchanged).
- `@pytest.mark.requires_network`: `test_alpaca_live_loopback.py` with a real `http.server` fake on 127.0.0.1:0 plus a UDP discovery reply.

**Must not change:** any ZWO module except the two shared edits (the hint default must be identical, and the debayer branch is additive).

**Parallel lanes:**
- **E:** protocol/http/imagebytes/sensor/discovery + their tests + `alpaca_fake.py`.
- **F:** `camera_source.py` + config keys + `test_camera_source.py`.
- **G** (after E): device/connection/camera/capture_frame + loop/calibration tests + factory.

### Phase 3: wiring, UI, docs
**Create:**
- `ui/panels/_zwo_connection_card.py`, `_alpaca_connection_card.py`, `_camera_capability_gate.py`
- `ui/controllers/alpaca_discovery_controller.py`
- `docs/wiki/Alpaca-Camera.md`

**Modify (UI):**
- `ui/panels/capture_settings.py`, `_camera_settings_widget.py`, `settings_panel.py` (text), `live_monitoring.py` (`:495,504`), `timelapse_panel.py` (`:76`)
- `ui/main_window/{capture.py, camera_detect.py, window.py, output.py, capture_watchdog.py}` and the window wiring for discovery signals
- `ui/controllers/{camera_controller.py, camera_settings.py, diagnostics_controller.py, image_processor.py (no change needed; alias), meteor_controller.py}`

**Modify (services):**
- `headless_runner.py`: `_init_camera` dispatches on source; the alpaca branch is factory + `connect_camera()` + `persist_alpaca_identity`; `_active_profile`/`_load_config` use the identity helper; the ZWO branch stays verbatim.
- `api_status.py` (`camera_source` field + `CAPTURE_FIELDS` row → OpenAPI picks it up)
- `discord_alerts.py` (labels)
- `diagnostics_bundle.py` (redaction)
- `diagnostics_controller.py` (summary `camera_source`; live settings gain `backend`, `driver_info`, `interface_version`, `sensor_type`, `max_adu`, `bayer_pattern`, `imagebytes`, `last_download_s`, `MB/s`)

**Tests:**
- `test_capture_source_selector.py` (3 segments ↔ config pairs; offscreen Qt)
- `test_camera_capability_gate.py` (ZWO caps show SDK/Revive/RAW16/hardware WB; Alpaca hides them, Bayer combo disabled with the detected value, WB items filtered, config not rewritten on load)
- `test_alpaca_connection_card.py`
- `test_alpaca_discovery_controller.py` (fake discover fn; signal delivered)
- `test_camera_controller_alpaca.py` (no `resolve_camera_index`, no USB worker, unrecoverable patterns ignored, identity persisted on the main thread)
- `test_camera_settings_alpaca.py` (Bayer not clobbered; identity guard by profile key)
- `test_headless_alpaca.py`
- Extend `test_api_status.py` (`camera_source`), `test_diagnostics_bundle.py` (host/unique_id redaction), and `test_discord*.py` (ZWO text unchanged)

**Docs:**
- `docs/wiki/Capture-Settings.md`, `Auto-Exposure.md`, `Settings.md` (USB steps ZWO-only), `Capture-Control-API.md` (`camera_source`), `Diagnostics-Export.md`, `Home.md`, `_Sidebar.md`. Each gets the "New in the next release" note.
- `CLAUDE.md`: capture modes (camera = ZWO SDK or Alpaca), the project structure line for `services/camera/alpaca/`, data flow, and test-table rows for every new test file.
- `.claude/rules/services-camera.md`: an Alpaca section covering:
  - pattern from SensorType/BayerOffset, never assumed
  - seconds in StartExposure
  - per-device lock, never `SDK_LOCK`
  - profile key `alpaca:<UniqueID>`
  - no USB reset
  - `release_on_disconnect` semantics

**Parallel lanes:**
- **H:** UI panels + gate + discovery controller + UI tests.
- **I:** services wiring (headless, `api_status`, diagnostics, discord, `camera_settings`, `camera_controller`).
- **J:** docs, wiki and rule file. J also owns `CLAUDE.md` (every lane touches the test table, so J takes the others' rows at the end).
- H and I share only `ui/main_window/capture.py`; assign it to H.

---

## 7. Risks and open questions

1. **The capture-loop move silently de-patches tests** (`zwo_capture_worker.time` monkeypatch). Change the fixture in the same commit; otherwise the suite hangs or slows down without failing.
2. **File-size cliffs:**
   - `zwo_capture_worker.py` is exactly 750: no edits before lane A shrinks it.
   - `window.py` is 747, `output.py` 738, `camera_controller.py` 726, `_camera_settings_widget.py` 698. Plan net-negative edits or extract first.
   - `camera_connection.py` (frozen exception) is untouched.
3. **Multi-client Alpaca semantics.** On ASCOM Remote, `Connected` can be shared across clients, so `Connected=false` from PFR could disconnect NINA. The default `release_on_disconnect=False` avoids that. Two clients exposing the same camera will still conflict; document "one imaging client per camera".
4. **Bit depth.** Drivers differ: ZWO's ASCOM driver reports MaxADU 65535 and others report 4095 with right-aligned data. The left-shift normalisation relies on MaxADU being honest. Expose `CAMERA_BIT_DEPTH` in diagnostics and allow a future override key if a driver lies.
5. **WB mode is global** (`white_balance.mode`). Mapping `asi_auto` → `gray_world` for Alpaca at session level (without rewriting config) is the least surprising option. Confirm with the owner.
6. **Start before the first frame.** `_on_capture_start_done` (`:351-357`) does not auto-recover a start that fails before the first frame. A rig booting before its Alpaca server is up will not retry. Decide whether Alpaca should retry on start (I recommend yes, under `camera_auto_recovery`).
7. **Headless never runs auto-exposure** (`headless_runner.py:360` calls `capture_single_frame` directly). This is pre-existing and applies equally to Alpaca; out of scope.
8. **Port collision.** The Alpaca default port 11111 equals PFR's own SafetyMonitor default (`config_defaults.py:196`). Pointing the camera at localhost:11111 hits our server; the configureddevices filter then gives a clear "no Camera device" error. Add that message.
9. **SensorType 1 (Color, Rank-3 RGB) and CMYG/LRGB.** Phase 2 treats CMYG/LRGB as MONO with a warning. The RGB passthrough needs an `'RGB'` branch in `debayer_raw_image` and in `raw_frame_export` (no Bayer FITS); confirm scope.
10. **Diagnostics redaction of `alpaca_camera.host`.** A generic `'host'` marker would also mask `webserver_host` (0.0.0.0 vs 127.0.0.1 is useful support info). I recommend a path-specific rule; confirm.
11. **Calibration race (pre-existing).** `CameraCalibration` issues `ASI_EXPOSURE` writes without `sdk_lock` while UI live writes take it. This is preserved byte-for-byte in Phase 1 and noted for a separate fix. The Alpaca device lock makes its own facade safe.
12. **Pre-existing bugs** from §1.11: the orphaned capture loop at `camera_controller.py:316-325`, and the `raw16_switch` `AttributeError` at `capture.py:403`. I recommend separate small PRs with tests before Phase 3, because Phase 3 touches the same lines.

### Critical files for implementation
- `/home/user/PFRSentinel/services/camera/zwo_camera.py`
- `/home/user/PFRSentinel/services/camera/zwo_capture_worker.py`
- `/home/user/PFRSentinel/services/camera/camera_calibration.py`
- `/home/user/PFRSentinel/ui/controllers/camera_controller.py`
- `/home/user/PFRSentinel/ui/panels/_camera_settings_widget.py`

Also central:
- `/home/user/PFRSentinel/tests/test_camera_auto_recovery.py`
- `/home/user/PFRSentinel/ui/controllers/camera_settings.py`
- `/home/user/PFRSentinel/services/headless_runner.py`
- `/home/user/PFRSentinel/services/config_defaults.py`
- `/home/user/PFRSentinel/ui/panels/capture_settings.py`
- `/home/user/PFRSentinel/services/camera/frame_builder.py`
- `/home/user/PFRSentinel/services/camera/camera_utils.py`
