# Diagnostics Export

> **New in the next release** — not available in version 3.7.6 or earlier.

**Export Diagnostics** gathers everything needed to investigate a problem into a single ZIP file that you can attach to a GitHub issue: recent logs, your settings with secrets and location removed, the all-sky calibration, and a raw frame showing what the camera actually saw. It is available in every build of PFR Sentinel; it is not a developer-only feature.

---

## Creating a Bundle

1. Open the [Logs](Logs) tab.
2. If you are reporting an image problem in ZWO camera mode, leave capture running so the bundle can include a fresh frame.
3. Click **Export Diagnostics**.

While the export runs, the button reads **Exporting…** and a status line below the controls shows progress:

- "Preparing diagnostics bundle…"
- "Capturing a fresh raw frame (up to N s)…" (only when the camera is capturing)
- "Saving raw frame…"
- "Writing diagnostics bundle…"

When it finishes, the status line shows "Diagnostics bundle saved:" followed by the file path, a **Diagnostics bundle ready** message appears at the top of the window, and File Explorer opens on the folder containing the ZIP. If the export fails, a **Diagnostics export failed** message shows the reason.

The export runs in the background, so capture and the rest of the app keep working while it runs.

### Where the ZIP Is Saved

```
%LOCALAPPDATA%\PFRSentinel\diagnostics\PFRSentinel_diagnostics_YYYYMMDD_HHMMSS.zip
```

Each export creates a new file, named with the date and time it was made. Old bundles are not deleted automatically.

---

## The Fresh Raw Frame

When a ZWO camera is capturing, the export asks the camera to take its next frame straight away instead of waiting out the normal capture interval, then waits for that frame to arrive. It never cuts short an exposure that is already in progress, so the wait depends on your exposure time: up to twice the exposure plus 30 seconds, but never less than 45 seconds or more than 10 minutes.

If no new frame arrives in time, or capture stops while waiting, the last frame already captured is used instead and the bundle's summary says so.

If capture is not running, the most recent frame captured since the app was started is used. If there has not been one, the bundle is still created without a frame; start capture and export again to include one.

---

## What's in the ZIP

| File | Contents |
|------|----------|
| `logs/` | Application log files changed in the last 3 days (see [Logs](Logs)). |
| `config.redacted.json` | All of your settings, with sensitive values replaced by `<redacted>` (see below). |
| `frames/raw_..._bayer.npy` or `frames/raw_..._bayer.fits` | ZWO camera mode only. The raw sensor data exactly as read from the camera: not debayered, stretched, or scaled. |
| `frames/raw_..._unprocessed.png` | ZWO camera mode: the full frame after debayering and white balance, but before stretch, resize, cropping, and overlays. Directory watch mode: the last full frame after auto-stretch, but before resize, cropping, and overlays (there is no raw sensor data in watch mode). |
| `frames/raw_..._metadata.json` | The frame's metadata: exposure, gain, temperature, image statistics, white balance settings, and similar values. |
| `frames/latest_output.jpg` (or `.png`) | The most recent finished output image, i.e. the image you may be reporting a problem with. If **Output Framing** is on, this is the cropped image. |
| `allsky/allsky_calibration.json` | Your current all-sky lens calibration, if one exists. |
| `allsky/allsky_calibration.previous.json` | The previous calibration kept as a backup, if one exists. |
| `allsky/custom_...` | A custom calibration file, if your all-sky settings point to one outside the usual location. |
| `summary.json` | App version, Windows version, capture mode, whether capture or directory watching was running, the selected camera name, the live camera settings (exposure, gain, offset, auto-exposure, maximum exposure, target brightness, capture interval, RAW16, Bayer pattern, white balance), notes explaining anything that was skipped, and a list of included and missing files. |

Items that don't exist on your system (for example, no all-sky calibration) are simply left out and listed as missing in `summary.json`.

---

## What Is Redacted

Before your settings are written to `config.redacted.json`, any setting whose name contains one of the following is replaced with `<redacted>`:

| Kind | Setting names containing |
|------|--------------------------|
| Secrets | `token`, `secret`, `password`, `api_key`, `apikey`, `client_secrets` |
| Webhooks and addresses | `webhook`, `url` |
| Location | `latitude`, `longitude`, `location`, `elevation` |
| Identifiers | `distinct_id` (the anonymous analytics ID), `serial` |

This covers, for example, your Discord webhook, OpenWeatherMap API key, web server control token, notification webhook URLs and secrets, and your observatory coordinates.

Settings that are empty stay empty, so it is still possible to tell "not configured" apart from "configured but hidden".

### What Is Not Redacted

Check the ZIP before sharing it publicly. The following are included as they are:

- **Log files.** Redaction applies to the settings file only. Logs are copied unchanged and can contain anything the app logged, such as folder paths, camera names, and web addresses. Your observatory coordinates, weather city name, and OpenWeatherMap API key are not written to the log, so they don't appear there. (Version 3.7.6 and earlier did log the coordinates and city, and could log the API key in weather error messages; those older logs are in a different folder and are not included in the bundle, but check them before sharing them any other way. See [Logs](Logs#log-files-on-disk).)
- **File and folder paths** in settings and logs. These often include your Windows user name (for example `C:\Users\YourName\...`).
- **Camera name** and camera settings in `summary.json`.
- **Images.** The raw frame and latest output show whatever the camera sees, including any text overlays such as the date, time, or weather.

---

## Attaching the Bundle to a GitHub Issue

1. Open the ZIP and check its contents, especially the log files.
2. Go to the [PFR Sentinel issues page](https://github.com/englishfox90/PFRSentinel/issues) and click **New issue**, then choose **Bug report**.
3. Fill in the version (shown in the window title and in [Settings](Settings)), capture mode, and a description of the problem.
4. Drag the ZIP file from File Explorer into the **What happened?** box, or into a comment on the issue after it has been created. GitHub uploads it and inserts a link.
5. Submit the issue.

If the ZIP is too large for GitHub to accept, it is usually the raw frame from a high-resolution camera. Upload the ZIP to a file-sharing service and paste the link into the issue instead.
