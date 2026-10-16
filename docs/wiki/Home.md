# PFR Sentinel

PFR Sentinel is a live camera monitoring and image overlay system designed for observatories. It captures images — either from a watched directory or directly from a ZWO ASI camera — applies processing, auto-stretch and overlays, then distributes the result through several outputs at once. It can also start and stop capture from a N.I.N.A. sequence, keep a browsable archive of every night, and watch the sky for meteors. Built for 24/7 unattended operation on Windows.

---

## How It Works

Every capture cycle follows the same pipeline:

1. A new frame arrives (from the camera sensor or a watched folder)
2. The frame is resized, if you asked for a smaller output
3. Auto-stretch (MTF), auto brightness and saturation are applied
4. ML models optionally classify roof status and sky conditions from the unprocessed frame
5. From the next release, the frame is cropped to your [Output Framing](Image-Processing#output-framing) box, if you drew one
6. Text, image, and compass overlays are composited onto the frame
7. The result is pushed to every active output — file, web server, Discord, Hermes, and the Image Library
8. If enabled, the frame also feeds the timelapse recorder, the meteor tracker, and the all-sky calibration

All outputs run simultaneously, and the pipeline runs unattended for days at a time.

---

## Capture Modes

**ZWO Camera** captures directly from a connected ZWO ASI camera via the ASI SDK. This mode supports auto-exposure, gain control, white balance, RAW16 mode, per-camera settings, automatic recovery when the camera drops off USB, and scheduled capture (from the next release, a schedule can also follow the timelapse window). See [Capture Settings](Capture-Settings).

**Directory Watch** monitors a folder for new images written by other capture software (NINA, SharpCap, or anything else). PFR Sentinel detects each file as it lands, reads any metadata file saved alongside it, processes the image, and moves on.

---

## Outputs and Integrations

| Output | Description |
|--------|-------------|
| [File](Output-Settings) | Saves each processed frame to a folder, with [filename tokens](Output-Filenames) for building an archive |
| [Web Server](Web-Server) | Serves the latest frame at `/latest` and status at `/status` (JSON), plus the Image Library endpoints |
| [Discord](Discord-Integration) | Periodic image posts, plus error, roof-change and timelapse notifications |
| [Hermes](Hermes-Notifications) | Signed JSON webhook notifications, with optional per-event URLs |
| [Image Library](Image-Library) | A browsable archive of recent nights with a scrubber and a condition band |
| [Timelapse](Timelapse) | Nightly H.264 video via ffmpeg, with optional [YouTube upload](YouTube-Uploads) |
| [NINA](NINA-Integration) | A plugin that shows the pier camera inside NINA and adds Start/Stop Sentinel Capture sequencer instructions |
| [Capture Control API](Capture-Control-API) | Token-protected HTTP endpoints to start and stop capture from scripts |

---

## Application Layout

The window has four parts, top to bottom:

- **App bar** — pipeline status, the notifications bell, and the main **Connect Camera** / **Start Capture** / **Stop** button
- **Status strip** — WEB, DISCORD, ROOF, SKY, WEATHER and SEEING tiles, and the capture/camera state
- **Content** — the navigation rail on the left and the selected page. Most pages show the live preview beside their settings; Overlays, Library and Settings use the full width
- **Telemetry bar** — disk space, exposure, gain, sensor temperature, last capture time, and the app version

The navigation rail groups the pages:

| Group | Page | Purpose |
|-------|------|---------|
| Monitor | [Live Monitoring](Live-Monitoring) | Live preview with zoom, histogram, recent activity, and performance |
| Monitor | [Meteor Tracker](Meteor-Detection) | Automatic meteor trail detection with thumbnails and confirmation (beta) |
| Image | [Capture](Capture-Settings) | Capture mode, camera settings, and scheduled capture |
| Image | [Image Processing](Image-Processing) | Resize, auto-stretch (MTF), brightness and saturation, ML models, the ASCOM safety file, and Output Framing (next release) |
| Image | [Overlays](Overlay-Settings) | Text, image, and compass overlays with live preview |
| Image | [All-Sky](All-Sky-Overlay) | Constellations, stars, deep-sky objects and planets on a fisheye frame, with automatic and guided calibration |
| Publish | [Output](Output-Settings) | File output, web server, capture control API, NINA plugin, Discord, Hermes, and Image Library settings |
| Publish | [Timelapse](Timelapse) | Nightly timelapse recording and YouTube upload |
| Publish | [Library](Image-Library) | Browse past nights frame by frame |
| — | [Logs](Logs) | Application log viewer, and [Diagnostics Export](Diagnostics-Export) from the next release |
| — | [Settings](Settings) | Theme, start with Windows, system tray, weather API and coordinates, analytics, and updates |

---

## Key Features

- **[Auto-stretch (MTF)](Auto-Stretch)**: Midtone transfer function with configurable target median, shadow handling, dark scene colour correction, and SCNR green removal
- **[Auto-exposure](Auto-Exposure)**: Calibration followed by per-frame adjustment and clipping protection for hands-off brightness control
- **[ML scene classification](ML-Models)**: Local ONNX models for roof open/closed detection and sky conditions, with results available as overlay tokens
- **ASCOM safety file**: ML roof status can be written to a file monitored by NINA's GenericFile safety monitor — see [ML Models](ML-Models)
- **[Overlay token system](Overlay-Tokens)**: Tokens for camera metadata, image statistics, weather data, and ML predictions
- **Scheduled capture**: Capture only inside a time window, or at a different rate inside the window. Following the timelapse window with a margin is new in the next release
- **[Weather integration](Weather-Setup)**: OpenWeatherMap data cached for 10 minutes, surfaced in overlays and the status strip
- **[All-sky overlay](All-Sky-Overlay)**: Constellation lines, bright stars, Messier/NGC labels and planets projected onto fisheye frames, with automatic lens calibration and a guided calibration for difficult sites
- **[Meteor tracker](Meteor-Detection)**: Detects meteor trails across a stack of frames, rejects planes and persistent artefacts, and keeps thumbnails for review
- **[Image Library](Image-Library)**: A per-night archive with a scrubber, roof and cloud condition band, and live updates while capturing
- **[NINA integration](NINA-Integration)**: Install the NINA plugin from the Output tab; capture follows your sequence
- **Camera recovery**: Reconnects a dropped camera, resets the USB device, and restarts the app if needed
- **Per-camera profiles**: Exposure, gain, white balance, and other settings stored independently for each camera
- **[Diagnostics Export](Diagnostics-Export)**: One ZIP with logs, redacted settings and a fresh raw frame to attach to a bug report (new in the next release)
- **[Output Framing](Image-Processing#output-framing)**: Crop saved and published images to a box you draw, while detection and calibration keep the full frame (new in the next release)

---

## System Requirements

- Windows 10 or later
- For ZWO camera mode: ASICamera2.dll (bundled, or a folder you choose). On macOS and Linux, see [ZWO cameras on macOS and Linux](Capture-Settings#zwo-cameras-on-macos-and-linux)
- For timelapse: ffmpeg (installable via winget from the Timelapse tab)
- For weather data: a free OpenWeatherMap API key (see [Weather Setup](Weather-Setup))
- For the all-sky overlay and sun-based timelapse windows: your latitude and longitude, entered on the Settings tab
- For the NINA plugin: NINA 3.x on the same PC

---

## Getting Help

- Check the [Logs](Logs) page for errors
- Open a [bug report](https://github.com/englishfox90/PFRSentinel/issues/new/choose), attaching a [Diagnostics Export](Diagnostics-Export) if your version has it

---

## Links

- [GitHub Repository](https://github.com/englishfox90/PFRSentinel)
- [Releases](https://github.com/englishfox90/PFRSentinel/releases)
