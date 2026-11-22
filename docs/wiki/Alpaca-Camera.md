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
   | Host | Empty | Address of the computer running the Alpaca server, for example `192.168.1.20` or `observatory-pc`. You can also type the port with it, as `observatory-pc:11112` (or `[fe80::1]:11112` for an IPv6 address); a port typed here is used instead of the Port box. |
   | Port | 11111 | The server's port. 11111 is the ASCOM default. |
   | Device | 0 | The camera's number on that server, usually 0. |

4. Click **Test Connection**. It connects, reads what the camera is, and lets go, without changing any camera setting and without starting capture. A pass reads, for example, `✓ ASI676MC: 3552 × 3552, 12-bit, colour`. A failure shows the reason in red; hover it for the full text, which is also in the [Logs](Logs).
5. Click **Start Capture**.

The app bar's main button reads **Connect Camera** while no host is set. In Alpaca Camera mode it runs **Discover**.

Once a host is set, the camera pill at the right of the status strip reads **Alpaca · not connected** until a **Test Connection** passes or capture runs against that host, port and device; after that it reads "Connected · idle" as usual. Changing the host, port or device, or a failed Test Connection on them, puts it back to **Alpaca · not connected**. **Start Capture** works either way.

A passed **Test Connection** also records the camera's unique ID and name, so its settings are saved under that camera from then on. Retyping the address — even back to the same value — keeps what was learned for it while the app is open.

While capture is running, the Camera Connection card is disabled ("Stop capture to change cameras"). To change cameras, stop capture first.

---

## What Works the Same

These work as described on [Capture Settings](Capture-Settings):

- **Exposure**, **Interval** and **Scheduled Capture**, including following the timelapse window. The Exposure hint shows the range the camera accepts once it has been tested or connected
- **[Auto Exposure](Auto-Exposure)**, including the calibration when capture starts. Exposures are kept within the range the camera reports, and the **Max Exposure** box can't go past the camera's longest exposure
- **Gain** and **Offset**, where the driver supports them. The range comes from the camera, and the row hint shows it once the camera has been tested or connected. A camera that offers a list of named gain settings instead of a number range takes the position in that list (0 for the first); the hint names the selected setting, for example `High (gain setting 1 of 0 to 2)`, and the tooltip lists them all. If the driver has no offset, the Offset row is hidden
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
| **Use RAW16 Mode** | Hidden. The bit depth comes from the camera: one whose largest value (its MaxADU) is 255 or less gives 8-bit frames, anything above gives 16-bit frames. A driver that reports a power of two, such as 4096 or 65536, is read as one less (4095, 65535), the top value of that many bits. The caption under the setting says which, for example "16-bit frames (12-bit sensor)". |
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
- With **Automatic camera recovery** off, a capture error gets one plain reconnect, then capture stops, as described in [Turning recovery off](Capture-Settings#turning-recovery-off). A failed start logs "Capture failed and automatic recovery is off — not retrying. Click Start when the camera is ready."

The USB reset and the app restart are never used for an Alpaca camera.

While it recovers, the camera pill at the right of the status strip reads **Reconnecting to camera…** during a reconnect, and **Camera lost · retrying** while it waits for the next attempt. ZWO cameras show the same two states. Outage lines are logged as warnings, in network terms ("Waiting 8s before reconnecting to the Alpaca server..."), without a traceback for an expected disconnect.

Each image download has a deadline. Before anything has been measured it allows for a slow link (about 250 kB/s); after the first frames it is three times the time the last download took plus the request timeout, never less than 30 seconds. A download that stops arriving altogether fails after the request timeout (10 seconds by default). The check that notices a stuck capture waits for the exposure plus that download time, so a long exposure over a slow link is not mistaken for a stuck camera.

In headless mode with **Automatic camera recovery** off, a frame that fails again after its one reconnect pauses capture ("Alpaca camera failed again after its single reconnect; automatic recovery is off — not reconnecting").

---

## Sharing the Camera with NINA

Use **one imaging program per camera**. A server such as ASCOM Remote may let NINA and PFR Sentinel connect to the same camera, but the camera can only take one exposure at a time, so the two take turns and both lose frames.

If they do share one, PFR Sentinel protects the other program's work rather than fighting it:

- Before each exposure it reads the camera's gain, offset, binning and subframe back and puts its own settings back if another program changed them.
- If the other program is exposing, or the image that comes back isn't the one PFR Sentinel asked for (the wrong size, or a different exposure length), that frame is skipped. The log reads `⏭ Frame skipped: ...` with the reason, at most once every 5 minutes for the same reason, and capture tries again after one capture interval. A skipped frame is not treated as a fault: there is no reconnect.
- It only ever aborts an exposure it started itself, never another program's.

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

- **Discover** sends a broadcast on **UDP port 32227** through every network connection this computer has, including a second network card or a ZeroTier network, and waits 2 seconds for replies. Broadcasts don't cross routers, so a server on a network this computer isn't on won't be found: type its address instead.
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
| `Connection refused by host:port — is the Alpaca server running?` | The computer is there, but nothing is listening on that port. Start the server, or check the port. |
| `Host name '...' not found — check the server address` | The host name can't be looked up. Check the spelling, or use the IP address. |
| `No route to ... — check the address and that it is on this network` | The address can't be reached from this computer. |
| `No answer from host:port within N s` | The server didn't reply in time: it is busy or stalled, or a firewall is silently dropping the connection. |
| `host:port closed the connection — is it an Alpaca server?` | Something answered on that port and hung up. It is probably not an Alpaca server. |
| `Cannot reach the Alpaca server at host:port` | Any other network failure. The full detail is in the log at debug level. |
| `Server at host:port is not an Alpaca server (HTTP 404 on /management) — check the port` | A web server answered, but not an Alpaca one. Often the port belongs to another program. |
| `host:port answers but has no Camera device (it lists: ...)` | The server is there but has no camera. Often this is PFR's own safety monitor; see [Port 11111](#port-11111-and-pfrs-own-safety-monitor). |
| `host:port has no camera #n (cameras: ...)` | The device number is wrong. The message lists the cameras the server does have. |
| `The camera with UniqueID ... is no longer on host:port. Run Discover / Test Connection to pick it again.` | The server no longer lists the camera PFR Sentinel was set up with, for example after the camera was removed or replaced in the server. Pick it again from **Discover**. |
| `Device did not report Connected after connect` or `Device still connecting after N s` | The server could not connect to the camera itself. Check the camera is powered and set up in the server. |
| `⏭ Frame skipped: camera busy — another client is exposing (...)` | Another program is using the camera. See [Sharing the Camera with NINA](#sharing-the-camera-with-nina). |
| `⏭ Frame skipped: delivered image is WxH, not full frame` or `... not this frame's exposure` | The image that came back was another program's, or taken with its settings. PFR Sentinel restores its own settings and takes the next frame. |
| `The Alpaca server sent the image as JSON (N MB or more). JSON images over 64 MB are refused ...` | The server can only send images as JSON, which is too large to decode safely for this camera. Use a server that supports ImageBytes, or turn `use_imagebytes` back on if you switched it off. |
| `The previous capture is still shutting down; try Start again in a few seconds` | **Start Capture** was clicked while the last capture was still letting go of the camera. Wait a moment and click it again. |
| `Alpaca camera not reachable yet — retrying under auto-recovery` | Capture was started but the camera couldn't be opened. With **Automatic camera recovery** on, the start is retried; see [When the Connection Drops](#when-the-connection-drops). |
| `Alpaca server host:port has no Management API; using device #n without an identity check` | A warning, not an error. The server can't list its devices, so PFR Sentinel uses the device number as given and can't notice if a different camera takes that number. |
| `Exposure failed (camera reported CameraState Error)` | The driver reported a failed exposure. Recovery reconnects and tries again. |
| `Exposure timeout: camera did not complete ...` | The exposure took more than 30 seconds longer than it should have. |
| `⚠ Exposure ...s is outside the camera's range ... — using ...s` | The exposure was moved into the range the camera accepts. |
| `Alpaca camera reports SensorType ...; colour reconstruction for it is not supported, so frames are treated as monochrome` | A CMYG or LRGB sensor; see [What Is Different](#what-is-different). |

If the image colours are wrong (for example red and blue swapped), the driver may be reporting its Bayer offset incorrectly. Please report it with a [Diagnostics Export](Diagnostics-Export).
