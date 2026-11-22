# Alpaca Camera

> **New in the next release** — not available in version 3.8.0 or earlier.

**Alpaca Camera** capture mode takes frames from a camera served over the network by an **ASCOM Alpaca** server, instead of from a ZWO camera plugged into this computer. Everything after the frame arrives — processing, overlays, outputs, timelapse, the all-sky overlay, ML models and the meteor tracker — works as it does in ZWO Camera mode.

Use it when:

- the camera is plugged into a different computer, for example the observatory PC, and you want PFR Sentinel to run somewhere else
- the camera is not a ZWO camera but has an ASCOM driver
- you run PFR Sentinel on macOS or Linux and the camera's driver only exists for Windows

On a Windows PC, **ASCOM Remote** can serve any camera that has an ASCOM driver (ZWO's ASCOM driver included) as an Alpaca device. Some cameras and other programs have an Alpaca server built in.

**What has been tested so far:** PFR Sentinel's Alpaca support has been checked against a simulated camera that follows the ASCOM Alpaca specification, and that same simulator against ASCOM's official Python client (alpyca). It has **not yet been tested with real camera drivers**. If you try it with your camera, please report how it went on the [issues page](https://github.com/englishfox90/PFRSentinel/issues), with a [Diagnostics Export](Diagnostics-Export) if something is wrong.

---

## Setting It Up

1. Start the Alpaca server on the computer the camera is plugged into (for example ASCOM Remote), and make sure the camera is set up in it.
2. In PFR Sentinel, open the **Capture** tab and choose **Alpaca Camera** in the **Capture Mode** card.
3. In the **Camera Connection** card, click **Discover**. The button reads **Searching...** while it runs. Cameras that answer appear in the **Found** list as `Name — host:port #n`. Pick yours.

   If yours doesn't appear, type the address yourself:

   | Setting | Default | Description |
   |---------|---------|-------------|
   | Host | Empty | Address of the computer running the Alpaca server, for example `192.168.1.20` or `observatory-pc`. |
   | Port | 11111 | The server's port. 11111 is the ASCOM default. |
   | Device | 0 | The camera's number on that server, usually 0. |

4. Click **Test Connection**. It connects, reads what the camera is, and lets go, without changing any camera setting and without starting capture. A pass reads, for example, `✓ ASI676MC: 3552 × 3552, 12-bit, colour`. A failure shows the reason in red; hover it for the full text, which is also in the [Logs](Logs).
5. Click **Start Capture**.

The app bar's main button reads **Connect Camera** while no host is set. In Alpaca Camera mode it runs **Discover**.

While capture is running, the Camera Connection card is disabled ("Stop capture to change cameras"). To change cameras, stop capture first.

---

## What Works the Same

These work as described on [Capture Settings](Capture-Settings):

- **Exposure**, **Interval** and **Scheduled Capture**, including following the timelapse window
- **[Auto Exposure](Auto-Exposure)**, including the calibration when capture starts. Exposures are kept within the range the camera reports, and the **Max Exposure** box can't go past the camera's longest exposure
- **Gain** and **Offset**, where the driver supports them. The range comes from the camera, and the row hint shows it once the camera has been tested or connected. A camera that offers a list of named gain settings instead of a number range takes the position in that list (0 for the first). If the driver has no offset, the Offset row is hidden
- **Flip**
- **Gray-world white balance**
- **Per-camera profiles**: settings are stored for each Alpaca camera, separately from your ZWO cameras
- The sensor temperature, when the camera reports it. Otherwise the temperature tokens read `N/A`
- Every output, the [Timelapse](Timelapse), the [All-Sky Overlay](All-Sky-Overlay), [ML Models](ML-Models), the [Meteor Tracker](Meteor-Detection) and the [Capture Control API](Capture-Control-API)

Elsewhere in this wiki, "ZWO Camera mode" in the sense of "capturing from a camera, not a folder" also covers Alpaca Camera mode, unless a page says otherwise.

---

## What Is Different

| Setting | In Alpaca Camera mode |
|---------|-----------------------|
| SDK Path, Camera drop-down, Detect | Hidden. The camera is addressed by host, port and device number instead. |
| **Revive (USB Reset)** and the missing-camera notice | Hidden. There is no USB device to reset over a network. |
| **Use RAW16 Mode** | Hidden. The bit depth comes from the camera: one whose largest value (its MaxADU) is 255 or less gives 8-bit frames, anything above gives 16-bit frames. The caption under the setting says which, for example "16-bit frames (12-bit sensor)". |
| White balance **asi_auto** | Not offered. There is no hardware white balance. If **asi_auto** is saved from ZWO Camera mode, the drop-down shows **gray_world** and frames are balanced with gray world. Your saved choice is not changed, so switching back to ZWO Camera mode brings **asi_auto** back. |
| White balance **manual** | The camera's hardware **Red**/**Blue** sliders are replaced by software **Red** and **Blue** gains, applied to the image. 1.00 leaves a colour as it is. |
| **Bayer Pattern** | Greyed out. The pattern is read from the camera (its sensor type and Bayer offset), never guessed, and follows the **Flip** setting. A mono camera shows "Mono (no debayer)". A camera that sends finished colour images shows "RGB from the camera". |
| **Flip** | Done by PFR Sentinel on each frame, not by the camera. |
| Region and binning | Always the full sensor, unbinned. |

Sensors with CMYG or LRGB filters are not supported for colour: their frames are treated as mono, and the log says so once.

### Profiles carried over from a ZWO camera

An Alpaca camera's settings are stored under its own ID, which PFR Sentinel learns the first time it connects. If you already have settings saved for a camera with the same name — for example the same ZWO camera seen through ZWO's ASCOM driver — those settings are copied once as a starting point. After that the two are kept separately.

---

## When the Connection Drops

A network camera recovers the same way a USB one does, minus the USB steps:

- **Capture errors** — PFR Sentinel reconnects to the server, finds the camera again by its ID even if the server has given it a different device number, and carries on. This is tried up to five times.
- **Still unreachable** — it keeps retrying at growing intervals (5 minutes, 15 minutes, then hourly) and reports once that the camera is unreachable. The log lists troubleshooting steps for a network camera: the server is running and reachable, the camera is connected in the server, no firewall is blocking the port, and the device number matches.
- **Capture won't start** — with **Automatic camera recovery** on (see [Settings](Settings#system)), a start that fails before the first frame is retried, so a rig whose camera server comes up after PFR Sentinel still starts capturing on its own. With it off, a failed start stays stopped until you click **Start Capture** again.
- With **Automatic camera recovery** off, a capture error gets one plain reconnect, then capture stops, as described in [Turning recovery off](Capture-Settings#turning-recovery-off).

The USB reset and the app restart are never used for an Alpaca camera.

---

## Sharing the Camera with NINA

Use **one imaging program per camera**. If NINA (or anything else) is taking exposures with the same camera, PFR Sentinel's exposures and NINA's will collide. A server such as ASCOM Remote may let both connect, but the camera can only take one exposure at a time.

When capture stops, PFR Sentinel stops its own exposure but leaves the camera connected on the server. On ASCOM Remote the connection can be shared between programs, so disconnecting it could disconnect the camera from NINA as well. **Test Connection** follows the same rule.

If PFR Sentinel is the only program using the camera and you want it to disconnect the camera when capture stops, close PFR Sentinel and set `release_on_disconnect` to `true` under `alpaca_camera` in `config.json` (in the PFR Sentinel data folder). The other Alpaca settings there — `connect_timeout_s` (3), `request_timeout_s` (10) and `use_imagebytes` (on: download images in the compact ImageBytes format, falling back to JSON when the server can't) — rarely need changing.

---

## Port 11111 and PFR's Own Safety Monitor

11111 is the default port for Alpaca servers, and it is also the default port of PFR Sentinel's own [Alpaca Safety Monitor](ML-Models#alpaca-safety-monitor). If PFR Sentinel's camera settings point at its own safety monitor, Test Connection and Start Capture fail with:

> `127.0.0.1:11111 answers but has no Camera device (it lists: SafetyMonitor). Is this PFR's own Alpaca SafetyMonitor port, or another server? Check the host and port.`

If the camera server runs on the same computer as PFR Sentinel with the safety monitor turned on, give one of them a different port. When the safety monitor can't get its port, the log reads `Alpaca SafetyMonitor: can't listen on ...`.

**Discover** never offers the safety monitor as a camera.

---

## Network and Firewall

- **Discover** sends a broadcast on **UDP port 32227** and waits 2 seconds for replies. Broadcasts don't cross routers or most VPNs, so a server on another network won't be found: type its address instead.
- Discovery also needs the server to answer it. Some servers have discovery turned off.
- The camera itself is reached over HTTP on the server's port (11111 unless you changed it). On the server computer, allow incoming connections on that port, and on UDP 32227 for discovery, in its firewall.
- PFR Sentinel connects to the server directly and ignores any proxy configured on this computer.

---

## Troubleshooting

The messages below appear on the Camera Connection card after **Test Connection**, in the [Logs](Logs), or both.

| Message | What it means |
|---------|---------------|
| `Enter the server's host first` | **Test Connection** was clicked with the Host box empty. |
| `No Alpaca server host is configured` | Capture was started with no host set. |
| `No Alpaca cameras answered. Enter the server address instead.` | **Discover** found nothing. See [Network and Firewall](#network-and-firewall). |
| `Cannot reach Alpaca server for ... (host:port #n): ...` | Nothing answered at that address. Check the server is running, the host and port are right, and the firewall allows the port. |
| `Timed out on ... (host:port #n)` | The server accepted the connection but did not reply in time. |
| `No answer from host:port within N s` | Test Connection gave up waiting for the server. |
| `host:port answers but has no Camera device (it lists: ...)` | The server is there but has no camera. Often this is PFR's own safety monitor; see [Port 11111](#port-11111-and-pfrs-own-safety-monitor). |
| `host:port has no camera #n (cameras: ...)` | The device number is wrong. The message lists the cameras the server does have. |
| `The camera with UniqueID ... is no longer on host:port. Run Discover / Test Connection to pick it again.` | The server no longer lists the camera PFR Sentinel was set up with, for example after the camera was removed or replaced in the server. Pick it again from **Discover**. |
| `Device did not report Connected after connect` or `Device still connecting after N s` | The server could not connect to the camera itself. Check the camera is powered and set up in the server. |
| `Alpaca server host:port has no Management API; using device #n without an identity check` | A warning, not an error. The server can't list its devices, so PFR Sentinel uses the device number as given and can't notice if a different camera takes that number. |
| `Exposure failed (camera reported CameraState Error)` | The driver reported a failed exposure. Recovery reconnects and tries again. |
| `Exposure timeout: camera did not complete ...` | The exposure took more than 30 seconds longer than it should have. |
| `⚠ Exposure ...s is outside the camera's range ... — using ...s` | The exposure was moved into the range the camera accepts. |
| `Alpaca camera reports SensorType ...; colour reconstruction for it is not supported, so frames are treated as monochrome` | A CMYG or LRGB sensor; see [What Is Different](#what-is-different). |

If the image colours are wrong (for example red and blue swapped), the driver may be reporting its Bayer offset incorrectly. Please report it with a [Diagnostics Export](Diagnostics-Export).
