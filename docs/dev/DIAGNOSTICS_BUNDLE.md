# Diagnostics bundle

One click on **Logs → Export Diagnostics** produces a ZIP the user can attach
to a GitHub issue. It exists because support threads kept stalling on "please
attach the log" and "what does the raw frame look like" (issues #10, #13).

## What goes in

| Entry | Source | Notes |
|---|---|---|
| `logs/sentinel.log*` | `%APPDATA%\PFRSentinel\logs` | Files modified in the last 3 days (rotations included) |
| `config.redacted.json` | `Config.data`, snapshotted on the GUI thread | Any key containing `token`, `secret`, `webhook`, `api_key`, `password`, `distinct_id`, `client_secrets`, `latitude`, `longitude`, `location`, `elevation`, `url`, `serial` is masked. Empty values stay empty so "not configured" is still visible. Paths (and so the Windows username) are **not** masked |
| `frames/raw_*_bayer.fits` | main-window raw cache | Unscaled sensor mosaic, `BAYERPAT` header. Camera mode only |
| `frames/raw_*_unprocessed.png` | same | Debayered + white balance, **before** stretch / sharpen / overlays / resize |
| `frames/raw_*_metadata.json` | same | Scalar metadata (exposure, gain, stats, WB config) |
| `frames/latest_output.*` | web server's latest path, else newest file in the output dir | The processed image the user is complaining about |
| `allsky/allsky_calibration*.json` | `%LOCALAPPDATA%\PFRSentinel` | Current + previous model, plus `allsky_overlay.calibration_file` when it points elsewhere |
| `summary.json` | controller | Version, OS, capture mode, live camera settings, notes on anything skipped, list of included files |

Output: `%LOCALAPPDATA%\PFRSentinel\diagnostics\PFRSentinel_diagnostics_<stamp>.zip`.
Explorer is opened on the folder when the export finishes.

## Fresh raw frame

When the camera is capturing, the controller calls
`ZWOCamera.request_immediate_capture()`. The capture worker's inter-frame wait
polls `consume_immediate_capture()` and breaks out early, so the user does not
sit through a long interval. The controller then waits for
`cached_raw_snapshot()`'s timestamp to change, bounded by
`2 × exposure + 30 s` (clamped to 45–600 s). If nothing arrives the last cached
frame is used and `summary.json` says so.

The wake only shortens the *wait*; it never interrupts an exposure in flight.

## Modules

- `services/diagnostics_bundle.py` — pure ZIP assembly + redaction (tested).
- `services/raw_frame_export.py` — FITS/PNG/JSON writers for a cached frame (tested).
- `ui/controllers/diagnostics_controller.py` — thread + Qt signals, owns the flow.
- `ui/panels/logs_panel.py` — button, progress caption, InfoBar results.
- `ui/main_window/output.py::cached_raw_snapshot()` — read-only view of the cache.

Unlike dev-mode raw saving this path is **not** gated on `DEV_MODE_AVAILABLE`;
its whole point is production users handing over what the camera saw.
