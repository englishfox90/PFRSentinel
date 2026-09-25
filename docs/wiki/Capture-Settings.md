# Capture Settings

The **Capture** tab configures where images come from. The **Capture Mode** card at the top switches between **Directory Watch** and **ZWO Camera**, and the settings below it change to match the selected mode. New installs start in **ZWO Camera** mode.

---

## Directory Watch

Directory Watch mode monitors a folder for new image files written by other capture software (NINA, SharpCap, etc.).

| Setting | Default | Description |
|---------|---------|-------------|
| Watch Directory | Empty | Folder to monitor. Type a path or pick one with **Browse**. |
| Include Subfolders | On | Also watch folders inside the watch directory. |

When a new file is detected and has finished being written, PFR Sentinel reads any metadata file saved alongside the image, processes the image, and pushes it to all active outputs.

---

## ZWO Camera

ZWO Camera mode captures directly from a connected ZWO ASI camera. The settings are split into collapsible cards.

### Camera Connection

| Setting | Description |
|---------|-------------|
| SDK Path | Path to the ZWO SDK library: `ASICamera2.dll` on Windows, `libASICamera2.dylib` on macOS, `libASICamera2.so` on Linux. On Windows this points at the copy installed with PFR Sentinel, so most users never need to change it. **Browse** opens a file picker filtered to that kind of file. |
| Camera | Drop-down of detected cameras, listed by model name (for example `ZWO ASI676MC`). |
| Detect | Scans for connected cameras in the background. The button reads **Detecting...** while the scan runs. |

On startup, PFR Sentinel scans for cameras automatically if it can find the SDK library. If it can't, the startup scan is skipped without a message. Clicking **Detect** then shows a **Camera Detection Failed** message explaining why, and the log lists every folder that was searched.

#### Where the SDK library is looked for

> **New in the next release** — not available in version 3.7.7 or earlier.

If SDK Path is empty or points at a file that no longer exists, PFR Sentinel searches for the library itself, in this order, and the log says which copy it used:

1. The PFR Sentinel program folder.
2. An `sdk` folder inside the PFR Sentinel data folder (`%LOCALAPPDATA%\PFRSentinel\sdk` on Windows, `~/Library/Application Support/PFRSentinel/sdk` on macOS, `~/.local/share/PFRSentinel/sdk` on Linux).
3. The usual system library folders: `/usr/local/lib` and the Homebrew folders on macOS; `/usr/lib`, `/usr/local/lib` and the distribution's architecture folder on Linux.

#### ZWO cameras on macOS and Linux

> **New in the next release** — not available in version 3.7.7 or earlier.

PFR Sentinel runs from source on macOS and Linux, and the ZWO library is not included there. Camera capture on these systems has not yet been confirmed on real hardware, so treat it as experimental.

1. Get the library. On Linux, if INDI's ZWO driver (`libasi`) is installed, it is already there and nothing more is needed. Otherwise download the **ASI Camera SDK (Linux & Mac)** from the ZWO website's developer page, and take the file for your computer: `lib/mac/libASICamera2.dylib`, or `lib/x64/` (Intel/AMD PC) or `lib/armv8/` (64-bit Raspberry Pi and similar) for `libASICamera2.so`. Use a current SDK — old copies found elsewhere online do not know newer cameras such as the ASI676MC.
2. Copy it into the `sdk` folder listed above, or anywhere you like and point **SDK Path** at it.
3. Linux only: install the udev rule that ships with PFR Sentinel, then unplug and replug the camera.

   ```bash
   sudo install -m 644 installer/linux/asi.rules /etc/udev/rules.d/
   sudo udevadm control --reload-rules
   ```

   Without it the camera is listed but will not open unless PFR Sentinel runs as root, and long exposures can time out or come back corrupted because the USB buffer is too small. PFR Sentinel writes a warning to the log when either setting is missing.

The USB reset step of camera recovery (**Revive**) is Windows-only. On macOS and Linux, recovery reconnects the camera and can restart the app, but cannot power-cycle the USB device.

**Which camera gets selected:**

- On a fresh install with exactly one camera connected, that camera is selected automatically.
- With several cameras connected and none saved, nothing is selected — pick one yourself. PFR Sentinel will not guess, so it never grabs a guide camera or a camera another program is using.
- If your saved camera is slow to appear after power-on, the automatic startup scan quietly retries up to 3 times, 5 seconds apart. A manual **Detect** does not retry.
- If your saved camera still isn't found, PFR Sentinel will not switch to a different camera. A notice appears on the card instead:
  - If the camera "is not connected", check the USB cable, then click **Detect Again**.
  - If the camera "is stuck", the driver can see it but the SDK can't open it. Click **Revive (USB Reset)** to switch the camera's USB device off and on again. This needs PFR Sentinel to be running as Administrator.

While capture is running, the **Camera** drop-down and **Detect** button are disabled ("Stop capture to change cameras"). To switch cameras, stop capture, pick the new camera, and start again.

### Exposure Settings

| Setting | Range | Default | Description |
|---------|-------|---------|-------------|
| Exposure | 0.001–3600 s | 0.1 s | Length of each exposure. When [Auto Exposure](Auto-Exposure) is on, this is only the starting value. |
| Gain | 0–600 | 100 | Camera gain (sensitivity). Higher is brighter but noisier. Auto Exposure never changes gain. |
| Interval | 0.1–3600 s | 300 s | How long to wait after each frame before starting the next one. Values below 1 s run as 1 s. |

### Auto Exposure

| Setting | Range | Default | Description |
|---------|-------|---------|-------------|
| Enable Auto Exposure | Toggle | On | Adjusts the exposure automatically to hold a target brightness. The two settings below appear only when this is on. |
| Target Brightness | 20–200 | 100 | Desired frame brightness on a 0–255 scale (20 = dark, 200 = bright). |
| Max Exposure | 0.1–3600 s | 30 s | Longest exposure Auto Exposure is allowed to use. |

See [Auto Exposure](Auto-Exposure) for how the algorithm works.

### Scheduled Capture

Scheduled Capture controls when the camera captures, and how often.

| Setting | Default | Description |
|---------|---------|-------------|
| Mode | Different rate within time window | See the modes table below. |
| Window source | Fixed times | Where the time window comes from: **Fixed times** or **Same as Timelapse**. Shown for the two window modes only. New in the next release. |
| Margin | 15 min | Range 0–180 min. Shown only with **Same as Timelapse**. New in the next release. |
| Window | 16:00 to 09:00 | Start and end of the window (24-hour). The window can cross midnight. Shown only with **Fixed times**. |
| In-window interval | 30 s | Time between captures inside the window. Values below 1 s run as 1 s. Shown only in **Different rate within time window** mode. |

**Modes:**

| Mode | Behaviour |
|------|-----------|
| Always capture | Captures 24/7 at the **Interval** from Exposure Settings. |
| Only within time window | Captures only inside the window. Outside it, capture pauses and the camera is disconnected. When the window opens again, the camera reconnects on its own. |
| Different rate within time window | Captures 24/7, using the **In-window interval** inside the window and the normal **Interval** outside it. Useful for fast captures at night and slow ones by day. |

When capture is paused outside the window, the status shows **Idle (off-peak, window ...)**. In **Only within time window** mode, run PFR Sentinel as Administrator. If the camera won't reopen when the window starts, automatic USB recovery needs those rights, and the log warns you when capture starts if they're missing.

#### Following the Timelapse window

> **New in the next release** — not available in version 3.7.6 or earlier.

With **Window source** set to **Same as Timelapse**, the capture window follows the recording window set on the [Timelapse](Timelapse) tab, widened by the **Margin** at both ends. This gives the camera time to reconnect and settle its exposure before recording starts, and saves you from maintaining two sets of times.

A caption under the settings shows the window in force, in the form `Tonight: timelapse window ±15 min (HH:MM - HH:MM)`. It updates as you change either tab.

How each Timelapse window mode is handled:

| Timelapse window mode | Capture window used |
|-----------------------|---------------------|
| Sunset / Sunrise | The calculated twilight window, plus the margin. Uses the latitude and longitude from [Settings](Settings). |
| Fixed Times | The Timelapse start and end times, plus the margin. |
| Always On | No time limit — capture is never paused. |
| Roof Open (Beta) | Can't be scheduled in advance, so the **Fixed times** start and end times are used instead. The caption says so. Those fields are hidden while **Same as Timelapse** is selected: switch **Window source** to **Fixed times** to see or change them, then switch back. |

This works whether or not timelapse recording is turned on.

### White Balance

| Mode | Description |
|------|-------------|
| asi_auto | Leaves white balance to the camera SDK. |
| manual | You set the **Red** (1–99, default 75) and **Blue** (1–99, default 99) channel values with sliders. |
| gray_world | Default. A software algorithm balances the colour channels using mid-tone pixels. **Low %** (0–49, default 5) masks the darkest pixels and **High %** (51–100, default 100) masks the brightest. |

The modes appear in the drop-down with the names shown above.

Switching to **gray_world** takes effect on the running capture straight away. Any other white balance change (switching to **asi_auto** or **manual**, switching between them, or changing **Red**/**Blue**) takes effect the next time capture starts.

### Advanced Settings

| Setting | Range | Default | Description |
|---------|-------|---------|-------------|
| Offset | 0–255 | 20 | Black level offset, which stops shadows clipping to pure black. |
| Flip | None / Horizontal / Vertical / Both | None | Mirrors the image. |
| Bayer Pattern | BGGR / RGGB / GRBG / GBRG | BGGR | Colour filter pattern used for debayering. Most ZWO colour cameras, including the ASI676MC, use BGGR. The wrong pattern swaps red and blue (for example, an orange sky). |
| Use RAW16 Mode | Toggle | Off | Captures at the sensor's full bit depth (12–14 bit) instead of 8-bit. |

The **Use RAW16 Mode** toggle stays disabled until a camera connects. A caption then reports support, either "✓ Camera supports RAW16 (12-bit ADC)" or "✗ Camera does not support RAW16 (12-bit ADC, RAW8 only)", with your camera's bit depth in place of 12. RAW16 can be switched while capturing. If the camera rejects RAW16 at full resolution when it connects, capture falls back to RAW8. The toggle still shows On in that case; the [Logs](Logs) tab reports the fallback.

---

## Per-Camera Profiles

Exposure, Gain, Target Brightness, Max Exposure, the manual white balance values, Offset, Flip and Bayer Pattern are stored per camera. Selecting a camera loads its own settings.

Profiles are tied to each camera's hardware serial number, which PFR Sentinel learns the first time the camera connects. Two cameras of the same model therefore keep separate settings. Until a camera has connected once, it uses the settings saved for its model name.

The other settings on this tab (Interval, Enable Auto Exposure, Scheduled Capture, White Balance mode and the gray-world **Low %**/**High %**) apply to whichever camera is in use.

---

## Camera Recovery

PFR Sentinel is built to run unattended, so it tries hard to recover a camera that stops responding:

- **Capture errors** — the camera is disconnected, and after a short pause to let the USB bus settle, it reconnects. This is tried up to five times.
- **Still unreachable** — PFR Sentinel keeps retrying at growing intervals (5 minutes, 15 minutes, then hourly) instead of giving up. It reports once that the camera is unreachable, and again when capture recovers.
- **Camera stuck in the SDK** — PFR Sentinel tries a USB reset (Administrator rights required). If that doesn't clear it, the app can restart itself to load a fresh copy of the SDK. It only does this inside the **Fixed times** capture window (or at any time in **Always capture** mode), only after at least one successful frame this session, and no more than three times an hour.

Details of each attempt are written to the [Logs](Logs) tab.

### Turning recovery off

> **New in the next release** — not available in version 3.7.6 or earlier.

If you don't want PFR Sentinel resetting the camera's USB device or restarting itself — on a shared USB hub, say — turn off **Automatic camera recovery** under [Settings](Settings#system). With it off:

- A capture error gets **one** plain reconnect: disconnect, a short pause, reopen. No USB reset, no SDK reset, no retry loop.
- ZWO cameras often fail the very first frame after being reopened, so that one failure is absorbed with a quiet second reopen. It counts as part of the same reconnect.
- If the reconnect fails, or frames still fail after it, capture stops, the camera is released, and an error is reported. It stays stopped until you click **Start**.
- In **Only within time window** mode, a camera that won't reopen at the start of the window also stops capture rather than retrying.
- The app never restarts itself, and the Administrator warning at startup is not shown.

The **Revive (USB Reset)** button still works, because you press it yourself. The switch takes effect immediately, including during a running capture.

---

## Notes

- Settings save as soon as you change them.
- Most changes take effect on a running capture without restarting it, including exposure (when Auto Exposure is off), gain, interval, auto-exposure targets, offset, flip, Bayer pattern and scheduling. Switching to **gray_world** white balance applies straight away; switching to or between **asi_auto** and **manual**, or changing the manual **Red**/**Blue** values, takes effect the next time capture starts.
- Camera detection runs in the background, so the window stays responsive.
