# Output Settings

The **Output** tab controls where processed images go and who hears about them. Saved files, the built-in web server, Discord and Hermes notifications all run at the same time, so you can switch on any combination. The tab also holds the NINA plugin installer, storage cleanup and the Image Library settings. Everything except **File Output** sits in a collapsible card, and the cards start collapsed. Click a card's header to open it.

---

## File Output

Saves each processed image to disk.

| Setting | Default | Range | Description |
|---------|---------|-------|-------------|
| **Output Directory** | `%LOCALAPPDATA%\PFRSentinel\Images` | — | Folder for processed images. Type a path or click **Browse**. |
| **Filename** | `latestImage` | — | Name for each saved file, without the extension. Can include `{filename}`, `{session}` and `{timestamp}`. See [Output Filenames](Output-Filenames). |
| **Format** | jpg | jpg, png | Image format. The extension is added to the filename for you. |
| **JPG Quality** | 100 | 1–100 | JPEG compression quality. Only used when **Format** is jpg. |

**Output Framing** (new in the next release): if you draw a framing box on the [Image Processing](Image-Processing#output-framing) tab, the outputs on this tab carry the cropped frame: saved files, the web server's `/latest` image, Image Library copies (and so the image links sent to Hermes), Discord posts and the timelapse.

With the default filename, every frame overwrites the previous one. To keep every frame, add `{timestamp}` to the filename. You'll need to manage disk space yourself when you do, because **Storage Cleanup** can't do it at the moment (see below).

---

## Web Server

A built-in HTTP server that serves the latest image, a JSON status report and an interactive API reference. See [Web Server](Web-Server) for the endpoints and what they return.

| Setting | Default | Range | Description |
|---------|---------|-------|-------------|
| **Enable Web Server** | Off | — | Serves the latest image over HTTP. |
| **Host** | 127.0.0.1 | — | Address to listen on. `127.0.0.1` allows this PC only. `0.0.0.0` listens on every network interface, or enter one specific LAN or VPN address. |
| **Port** | 8080 | 1–65535 | TCP port. |
| **Image Path** | /latest | — | URL path for the latest image. |
| **API Docs** | — | — | An **Open API Docs** link to the server's interactive reference page, built from the current host and port. |

### Capture Control API

The same card holds the switch that lets NINA and other local tools start and stop capture over HTTP.

| Setting | Default | Description |
|---------|---------|-------------|
| **Enable Capture Control API** | Off | Turns on the start/stop endpoints. They run on the web server, so **Enable Web Server** must also be on. If it isn't, you'll get a warning. |
| **API Token** | — | Generated the first time you turn the control API on. It's hidden until you click **Show**. **Copy** puts it on the clipboard. **Regenerate** creates a new token, and any tool still using the old one stops working. The field stays empty while the control API is off. |

The bundled NINA plugin reads the token by itself, so you only need to copy it for other tools. See [Capture Control API](Capture-Control-API).

---

## NINA Plugin

Installs, updates or removes the PFR Sentinel plugin for N.I.N.A.

| Element | Description |
|---------|-------------|
| **Status** | Whether NINA was found and whether the plugin is installed, out of date, or installed for an older NINA plugin version. |
| **Install Plugin** | Copies the plugin into NINA. The button reads **Update Plugin** when a newer plugin is bundled, and **Reinstall Plugin** when the plugin is already installed. |
| **Remove** | Removes the plugin from NINA. |

Close NINA before you install, update or remove the plugin, then restart NINA to load the change. See [NINA Integration](NINA-Integration).

---

## Discord Integration

Posts alerts, periodic images and timelapse videos to a Discord channel through a webhook.

| Setting | Default | Description |
|---------|---------|-------------|
| **Enable Discord Alerts** | Off | Master switch for Discord. |
| **Webhook URL** | — | Your Discord webhook URL. It's hidden until you click **Show**. |
| **Post Errors** | Off | Posts capture and camera errors. |
| **Post Start/Stop** | Off | Posts when PFR Sentinel starts, when capture starts, and when you exit PFR Sentinel. With **Enable System Tray** on, closing the window only hides it, so nothing is posted. |
| **Post Timelapse Video** | Off | Posts each finished timelapse and attaches the video if it's 8 MB or smaller. |
| **Post Roof Changes** | Off | Posts when the roof classifier confirms the roof has opened or closed. Needs **Enable ML Analysis** on and the roof model installed. See [ML Models](ML-Models). |
| **Periodic Updates** | Off | Posts a status update at a regular interval. Turning it on shows **Interval** and **Include Latest Image**. |
| **Interval** | 60 min | 30–1440 min. Time between periodic updates. Each cycle can run up to 5 minutes early, chosen at random. |
| **Include Latest Image** | On | Attaches the latest saved image to periodic updates and roof-change posts. It's only shown while **Periodic Updates** is on, but it still applies to roof-change posts when that switch is off. |
| **Embed Color** | #0EA5E9 | Border colour of Discord messages. |

**Test Webhook** sends a test message and shows the result next to the button. See [Discord Integration](Discord-Integration) for message details.

---

## Hermes Webhook

Sends signed JSON notifications to a Hermes agent webhook, alongside Discord or instead of it. Turn on **Enable Hermes Webhook** to show these settings:

| Setting | Default | Description |
|---------|---------|-------------|
| **Webhook URL** | — | Base URL of the Hermes webhook route. It's hidden until you click **Show**. |
| **Secret** | — | Shared signing secret for the route. |
| **Post Errors** | Off | Sends error events. |
| **Post Startup/Shutdown** | Off | Sends an event when PFR Sentinel starts and when you exit it. |
| **Post Roof Changes** | Off | Sends confirmed roof open/close events. Needs **Enable ML Analysis** on. |
| **Post Timelapse** | Off | Sends an event when a timelapse video finishes. |
| **Post Calibration** | Off | Sends an event when **Calibrate Now** or a guided all-sky calibration succeeds. |
| **Periodic Image Updates** | Off | Sends periodic image events, using the Discord **Interval**. Only sent while **Enable Web Server** or **Enable Discord Alerts** is on. |
| **Route Events to Separate URLs** | Off | Shows a URL field for each event type. A blank field uses the base URL. |

**Test Webhook** sends a signed test event to the base URL. See [Hermes Notifications](Hermes-Notifications).

---

## Storage Cleanup

| Setting | Default | Range | Description |
|---------|---------|-------|-------------|
| **Enable Auto Cleanup** | Off | — | Deletes files automatically when the limit is exceeded. |
| **Max Size** | 10.0 GB | 0.1–1000 GB | Size limit. |
| **Strategy** | oldest | oldest, largest | Which files to delete first. |

> **Current limitation:** Storage Cleanup doesn't delete anything in the current version. It never runs during camera capture in the app. Where it does run (Directory Watch mode, and after each frame in headless mode), it checks the *watch* folder rather than the output folder, and it doesn't recognise either **Strategy** choice, so it stops without deleting. Don't rely on it to keep a disk from filling. If you save timestamped images, clear out old files some other way.

The Image Library and timelapse videos have their own separate retention settings.

---

## Image Library

Keeps a rolling history of small copies of recent frames. You can browse them in the app, and other programs can fetch them from the web server.

| Setting | Default | Range | Description |
|---------|---------|-------|-------------|
| **Enable Library** | On | — | Keeps small copies of recent frames that you can browse. |
| **Retention** | 7 days | 1–365 days | Deletes images older than this. |
| **Max Size** | 2.0 GB | 0.1–1000 GB | Deletes the oldest images once the library is bigger than this. |
| **Max Dimension** | 750 px | 100–4000 px | Length of the longest side of stored images. Only affects new frames. |
| **JPEG Quality** | 85 | 1–95 | Quality of stored images. Only affects new frames. |
| **Expose Web API** | On | — | Serves `/library` and `/library/image` from the web server. |

See [Image Library](Image-Library).

---

## Notes

- Settings save as soon as you change them.
- If **Enable Web Server** is on, the server starts when PFR Sentinel launches, and when you start capture if it isn't already running. It keeps running after you stop capture. Switching **Enable Web Server** on or off takes effect straight away while capture is running or the server is already up. If you turn it on while idle with no server running, it starts the next time you start capture.
- Changes to **Host**, **Port** or **Image Path** take effect the next time the server starts. To apply them, turn **Enable Web Server** off and on again while capturing, or restart PFR Sentinel.
- If the server can't start (for example, the address it should listen on isn't available yet), PFR Sentinel tries again every 15 seconds for as long as the server is enabled.
