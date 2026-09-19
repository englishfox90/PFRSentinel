# Live Monitoring

Live Monitoring is the operational view of PFR Sentinel: the latest processed frame, its histogram, and a running feed of activity. Around it, the app bar, status strip, and telemetry bar are visible on every tab and give an at-a-glance picture of the camera, the sky, and the outputs. This page describes all of those, along with the window layout and navigation rail.

---

## Window Layout

From top to bottom the window has:

1. **App bar**: app name, pipeline status animation, notification bell, and the main Start/Stop button.
2. **Status strip**: tiles for Web, Discord, Roof, Sky, Weather, and Seeing, plus the capture/camera state.
3. **Content area**: the navigation rail on the left, then the page content.
4. **Telemetry bar**: disk space and the latest frame's exposure, gain, sensor temperature, and capture time.

How the content area is used depends on the page selected in the navigation rail:

| Selected page | Layout |
|---------------|--------|
| **Live Monitoring** | The preview fills the whole content area. The histogram, activity feed, and performance line are hidden to give the image maximum room. |
| **Capture**, **Image Processing**, **All-Sky**, **Meteor Tracker**, **Output**, **Timelapse**, **Logs** | The live panel (preview, histogram, activity feed) on the left and the selected page's settings on the right. Drag the divider between them to resize; the split is remembered. |
| **Overlays**, **Library**, **Settings** | The selected page fills the content area and the live panel is hidden. The Overlays page has its own preview. |

The app reopens on the page you were last using.

---

## App Bar

| Element | Description |
|---------|-------------|
| App name | PFR Sentinel icon and name. |
| Pipeline status | A small animation that appears while a session is running and shows which stage the current frame is at. Hover it for a description: "Waiting for new image", "Capturing exposure", "Calibrating camera", "Applying histogram stretch", "Processing image", or "Sending to outputs". |
| Notifications | Bell icon with a badge counting the notifications in the list. Click it to open the notifications list (see below). |
| Main action button | Changes with the app's state (see below). |

### Main Action Button

| Button | When shown | What it does |
|--------|------------|--------------|
| **Connect Camera** | ZWO camera mode with no camera detected | Searches for connected ZWO cameras. |
| **Start Capture** | A camera is available, or directory watch mode is selected | Starts capture or directory watching. |
| **Stop** (red) | While capturing | Stops capture. |

In directory watch mode, **Start Capture** is greyed out until a valid watch folder is set. Hover it to see why: "Set a valid watch directory on the Capture tab".

### Notifications

Click the bell to open a list of recent in-app notifications, newest first. Each entry shows the message and the time it was added (HH:MM:SS in UTC, not your local time), with a coloured bar on the left: accent colour for information, amber for warnings, and red for errors. The list shows up to 30 entries, and the app keeps the most recent 50. Click the bin icon (**Clear all**) to empty the list and reset the badge.

Examples of notifications:

- Capture started or stopped, capture failures, and camera errors
- Camera detection results and USB camera revive attempts
- Web server started, or failed to start
- Timelapse finalizing and saved
- An update is available
- Settings problems found at startup
- Running without Administrator rights (USB camera recovery is limited)
- NINA plugin install/remove results and out-of-date plugin warnings

---

## Status Strip

The band below the app bar shows live observatory status. Tiles that have nothing to show read **Not configured**.

| Tile | Shows |
|------|-------|
| **WEB** | **On** when the web server is running, **Starting…** when it is enabled but not yet running, **Off** when disabled. See [Web Server](Web-Server). |
| **DISCORD** | **On** or **Off**, following the Discord setting on the [Output](Output-Settings) tab. |
| **ROOF** | **Open** (green) or **Closed** (red) from the roof classifier. Needs ML models enabled; see [ML Models](ML-Models). |
| **SKY** | Sky condition from the sky classifier: **Clear** (green), **Partly Cloudy** (amber), or other conditions such as **Overcast** (red). Shows **Roof closed** while the roof is closed, because the camera can't see the sky. Needs ML models enabled. |
| **WEATHER** | Temperature, condition, and cloud cover from OpenWeatherMap, e.g. "12.0°C · Clear · 20%". Needs an API key and a location or coordinates in [Settings](Settings). The tile keeps updating while capture is stopped, and dims if the weather data is more than 30 minutes old (for example, an invalid API key or no internet). |
| **SEEING** | Star count and FWHM from star detection, e.g. "412 stars · FWHM 2.3". Shows **—** until the first reading arrives, and **Roof closed** while the roof is closed. |

Roof, Sky, and Seeing are updated with each processed frame and are dimmed while capture is stopped, so you can tell a live reading from the last known one.

At the right-hand end, the capture/camera pill shows:

| State | Display |
|-------|---------|
| Capturing | Green **CAPTURE** pill with "Capturing · frame N", counting frames captured since the app started. The count goes up when a frame is captured, before it has been processed. |
| Idle | **CAMERA** "Connected · idle" |
| No camera | Red **CAMERA** "Disconnected" (or "Camera error" after a camera failure) |

---

## Preview

The **Preview** card shows the most recently processed frame, scaled to fit while keeping its aspect ratio, with your overlays drawn on it. Depending on your [All-Sky Overlay](All-Sky-Overlay) settings, the all-sky overlay can appear in the preview without being added to saved or published images. If you crop your outputs with **Output Framing** on the [Image Processing](Image-Processing#output-framing) tab, the preview shows the cropped image.

### Exposure Progress

During each exposure, a thin progress bar appears under the **Preview** heading, and a countdown at the top right shows the time remaining (in milliseconds, seconds, or minutes).

### Zoom and Pan

- **Scroll the mouse wheel** over the image to zoom in or out, centred on the cursor, up to 8×.
- **Click and drag** to pan while zoomed in.
- **Double-click** to return to the full view.

Zoom and pan are for inspection only and don't change the output. The zoom level is kept as new frames arrive, so you can watch one area of the sky.

### Preview Messages

When there is no live frame, a message is shown over the preview:

- **No camera connected**: "Connect a ZWO ASI camera, or choose a directory to watch for incoming frames."
- **Capture stopped**: the last frame stays on screen with "Showing last frame" and its capture time.

The message clears as soon as a new frame arrives.

---

## Histogram

The **Histogram** card plots the red, green, and blue channels of the latest frame, measured before the stretch is applied, so it reflects what the camera actually recorded.

In ZWO camera mode with auto-exposure turned on, two extra markers are drawn:

| Marker | Meaning |
|--------|---------|
| Yellow dashed line labelled **Target: N** | The auto-exposure target brightness (0-255 scale). |
| Red dashed line labelled **Clip** | The clipping threshold at 245. Data piling up to the right of this line is close to saturation. |

See [Auto Exposure](Auto-Exposure) for how these values are used.

---

## Recent Activity

The **Recent Activity** card is a short, scrolling feed of application messages, always following the newest. It keeps the last 100 messages and leaves out DEBUG messages so that important events stay visible. For the full log with filtering and search, use the [Logs](Logs) tab.

### Performance Line

Below the activity feed, a line shows processing performance:

```
Proc: 0.30s  |  Mem: 245 MB  |  Disk: 280.9 GB free
```

| Metric | Description |
|--------|-------------|
| Proc | Time taken to process the most recent frame. Updated with each frame. |
| Mem | Memory used by PFR Sentinel. Updated every 5 seconds. |
| Disk | Free space on the drive holding the output directory. Updated every 5 seconds. |

---

## Telemetry Bar

The thin bar along the bottom of the window shows technical details of the latest frame:

| Field | Description |
|-------|-------------|
| DISK | Free space on the drive holding the output directory, refreshed about every 10 seconds. |
| EXP | Exposure time of the latest frame, e.g. "7.91s" or "250ms". |
| GAIN | Camera gain of the latest frame. |
| SENSOR | Camera sensor temperature. |
| LAST CAPTURE | Capture time (HH:MM:SS) of the latest frame. |
| Version | The app version, at the far right. |

EXP, GAIN, and SENSOR show "—" when capture is stopped. With no camera connected, LAST CAPTURE is cleared as well.

---

## Navigation Rail

The rail on the left lists the app's pages in three groups, with **Logs** and **Settings** at the bottom:

| Group | Pages |
|-------|-------|
| **MONITOR** | [Live Monitoring](Live-Monitoring), [Meteor Tracker](Meteor-Detection) |
| **IMAGE** | [Capture](Capture-Settings), [Image Processing](Image-Processing), [Overlays](Overlay-Settings), [All-Sky](All-Sky-Overlay) |
| **PUBLISH** | [Output](Output-Settings), [Timelapse](Timelapse), [Library](Image-Library) |
| (bottom) | [Logs](Logs), [Settings](Settings) |

The selected page is highlighted in the accent colour. Click the menu icon at the top of the rail to collapse it to icons only, or expand it again.

Badges on the rail:

- **Meteor Tracker**: an amber **BETA** label.
- **Timelapse**: a dot while a timelapse is recording.
- **Settings**: a **!** badge when an update is available, cleared when you open Settings.

When the rail is collapsed, badges shrink to a small dot on the icon.
