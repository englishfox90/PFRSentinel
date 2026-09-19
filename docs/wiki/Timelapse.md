# Timelapse

The **Timelapse** tab records a video of each night from the frames PFR Sentinel captures. Every captured frame inside the recording window becomes one frame of the video, which is encoded as it goes, so there is no long render at the end of the night. Timelapse is designed for **ZWO Camera** capture mode. When **Directory Watch** mode is selected, the tab shows the notice "Timelapse recording is only available in Camera (ZWO) capture mode."

Timelapse recording needs ffmpeg, a free video encoder. If ffmpeg isn't found, the tab shows an install card instead of the recording settings.

The **Recording Window**, **Frame & Quality**, **Playback Speed Calculator** and **Output** cards start collapsed. Click a card's header to open it.

If you use [Output Framing](Image-Processing#output-framing), the video records the framed (cropped) image, the same as your saved files. Output Framing is new in the next release.

---

## ffmpeg Installation

The **ffmpeg Required** card offers two ways to install it:

| Option | Description |
|--------|-------------|
| Install via winget | Installs ffmpeg with the Windows Package Manager. A progress bar shows while it runs (allow up to 3 minutes). This button is hidden if winget isn't available on the PC. |
| Download manually | Opens the ffmpeg download page in your browser. Install ffmpeg and add it to your PATH. |

When the winget install succeeds and ffmpeg is found, the card shows "ffmpeg is ready." and the recording settings appear. If winget reports success but ffmpeg still can't be found, restart PFR Sentinel. PFR Sentinel also finds a winget-installed ffmpeg that hasn't been added to PATH.

---

## Daily Timelapse

| Setting | Default | Description |
|---------|---------|-------------|
| Enable Timelapse | Off | Turns timelapse recording on. Capture must be running for anything to be recorded. |

---

## Recording Window

The **Recording Window** card sets when frames are recorded.

| Window Mode | Description |
|-------------|-------------|
| Sunset / Sunrise | Default. Records between evening and morning twilight, calculated each day for your location. |
| Fixed Times | Records between a start and end time you choose. |
| Always On | Records every captured frame, around the clock. |
| Roof Open (Beta) | Records while the ML roof model reports the roof as open. |

### Sunset / Sunrise

| Setting | Default | Description |
|---------|---------|-------------|
| Twilight Depth | Astronomical (darkest) | Which twilight marks the start and end of the window. |

| Twilight Depth | Window runs between |
|----------------|---------------------|
| Astronomical (darkest) | Sun 18° below the horizon. Shortest, darkest window. |
| Nautical | Sun 12° below the horizon. |
| Civil | Sun 6° below the horizon. |
| Sunset / Sunrise | Sunset and sunrise. Longest window. |

The location comes from the latitude and longitude in the weather **Coordinates** row on the [Settings](Settings) tab (see [Weather Setup](Weather-Setup)). No API key is needed for this. Twilight times are worked out for your local calendar date, so the window is correct wherever you are in the world. (In version 3.7.6 and earlier, locations well west or east of Greenwich could get a wrong window: west of about UTC−5 the Civil and Sunset / Sunrise windows didn't close at dawn, and east of about UTC+8 every window ended a day late. This is fixed in the next release.)

The **Fixed Times** start and end below are used instead when:

- No latitude or longitude has been entered, or the values aren't a valid latitude and longitude.
- The sun never gets low enough for the chosen depth, which happens at high latitudes in summer. Choose a shallower depth, or use Fixed Times, if you're affected.

### Fixed Times

| Setting | Default | Description |
|---------|---------|-------------|
| Start → End | 18:00 → 06:00 | Start and end of the recording window (24-hour). Windows that cross midnight are supported. |

### Roof Open (Beta)

Recording follows the roof state from the roof classifier. Turn on **Enable ML Analysis** on the [Image Processing](Image-Processing) tab (see [ML Models](ML-Models)). If ML is off, or no frame has been classified yet, recording doesn't start.

### One video per night

- **Sunset / Sunrise** and **Fixed Times** produce one video per window. An overnight window is not split at midnight.
- **Always On** starts a new video at midnight, so you get one video per calendar day.
- **Roof Open** starts a new video each time the roof opens.

---

## Frame & Quality

| Setting | Range / Options | Default | Description |
|---------|-----------------|---------|-------------|
| Playback speed | 1–60 fps | 24 fps | Frame rate of the video. Each captured frame is shown for 1/fps of a second, so higher values make shorter, faster videos. |
| Output resolution | Original, 1920 px, 1440 px, 1280 px, 720 px | 1920 px | Scales the longest side of the video down to this size. Frames already smaller than this are left as they are. **Original** keeps the full captured size. |
| Video quality | Efficient · smallest file, Balanced · recommended, High quality · larger file, Maximum · largest file | Balanced | H.264 encoding quality. Higher quality means larger files. |
| Include overlays in video | Toggle | Off | When on, the video uses frames with your text and image overlays. When off, it uses the processed frame without overlays. |

- Overlay text that changes every frame (times, exposure, and so on) flickers at playback speed, which is why the overlay toggle is off by default.
- The all-sky overlay can also be burned into the video. Turn on **Timelapse video** on the [All-Sky Overlay](All-Sky-Overlay) tab as well as **Include overlays in video** here.
- 1920 px is the default because encoding a large sensor at full resolution is slow enough to fall behind the capture. Use **Original** only if your PC keeps up.
- Changes to these settings apply from the next video. If the size of the captured frames changes during a recording, the current video is closed and a new one started.

---

## Playback Speed Calculator

The **Playback Speed Calculator** card works out the frame rate for a video of the length you want.

| Setting | Range | Default | Description |
|---------|-------|---------|-------------|
| Session duration | 1–24 hr | 6 hr | Length of a typical night's recording. |
| Capture interval | — | — | Read-only. Shows the **Interval** from [Capture Settings](Capture-Settings); change it there. |
| Target video length | 5 s – 3 min | 30 s | How long you want the finished video to be. |

The result line shows the calculation (hours and interval, number of frames, target length, resulting fps). Click **Set FPS to N** to copy the result, limited to 1–60, into **Playback speed**.

The calculator uses the main **Interval**. If you use **Different rate within time window** scheduling, frames inside the window arrive at the **In-window interval**, so work out the frame count from that value.

---

## Output

| Setting | Range | Default | Description |
|---------|-------|---------|-------------|
| Output folder | — | Empty | Folder for finished videos. Leave empty to use `%LOCALAPPDATA%\PFRSentinel\timelapse\`. |
| Keep videos for | 1–365 days | 30 days | Intended retention period for old videos. |

**Note:** the hint under this setting says "Oldest files deleted automatically beyond this limit", but that is wrong: in the current version **Keep videos for** is saved and old videos are not deleted. Clear out the output folder yourself if disk space matters.

Videos are named `timelapse_YYYYMMDD.mp4` after the date the recording started. If a file for that date already exists (for example, the roof closed and reopened), `_2`, `_3` and so on are added instead of overwriting it.

Videos are written so they stay playable while recording is still going, and if the PC loses power or the camera drops out, the file stays playable up to the last completed second of video. At most one playback-second's worth of frames is lost (24 frames at the default 24 fps). At long capture intervals that can cover a lot of real time: 24 frames 30 seconds apart is 12 minutes of the night.

### Sharing finished videos

- **Discord** — turn on **Post Timelapse Video** on the [Output Settings](Output-Settings) tab. Videos up to 8 MB are attached; larger ones are announced with their size but not attached. See [Discord Integration](Discord-Integration).
- **Hermes** — the **Post Timelapse** option in the **Hermes Webhook** card on the [Output Settings](Output-Settings) tab sends a notification when a video finishes. See [Hermes Notifications](Hermes-Notifications).
- **YouTube** — the **YouTube Uploads** card on this tab uploads finished videos to your channel. See [YouTube Uploads](YouTube-Uploads) for setup.

---

## Status

The **Status** card at the bottom of the tab refreshes every 5 seconds:

- **Not recording** when idle.
- **● Recording · N frames · HH:MM:SS elapsed · filename** while a video is being recorded.

### Recording window forecast

> **New in the next release** — not available in version 3.7.6 or earlier.

Below the recording state, the Status card shows the recording window that is open now, or the next one to open, so you can check your settings before night falls:

| Line | When it appears |
|------|-----------------|
| Next window: today 19:12 → 05:48 (10h 36m) · opens in 3h 02m | The window hasn't opened yet. The day reads today, tomorrow, or a weekday. |
| Window open: 19:12 → 05:48 (10h 36m) · closes in 6h 20m | The window is open now. |
| Window: always on, so every captured frame is recorded | **Always On** mode. |
| Window: follows the roof state, so it can't be projected ahead | **Roof Open (Beta)** mode. |
| Window: couldn't be worked out from the current settings | The window couldn't be calculated. |

When the window is open and timelapse is enabled but nothing is recording, a second line reads "Recording starts with the next captured frame, so capture must be running." The timelapse only records frames that capture delivers.

In **Sunset / Sunrise** mode, if the Fixed Times are being used instead, a line explains why:

- "No location in Weather settings, so the fixed times are used instead."
- "The Weather settings location isn't a valid latitude/longitude, so the fixed times are used instead."
- "Astronomical twilight doesn't happen that night at this latitude, so the fixed times are used instead." (The name matches the chosen **Twilight Depth**.)

The button beside it reads **Show in folder** while recording (opens the folder with the video selected) and **Open video** once the recording has finished. The **Timelapse** entry in the navigation rail shows a dot while recording is active.

---

## Stopping and Closing

- **Stop capture** — the current video is finished in the background, so Stop stays responsive. "Finalizing timelapse video…" and then "Timelapse saved: *filename*" appear in the notification bell.
- **Close the app** — if a video is still being finished, a "Saving timelapse video, please wait…" dialog stays up until it's done (for up to 75 seconds).
- **Start capture again** — recording resumes normally, starting a new video (or a numbered one, if today's already exists).

---

## Unattended Reliability

- **ffmpeg keeps failing to start** — PFR Sentinel removes the empty or near-empty video it left behind, then waits before retrying: 15 seconds, doubling each time up to 5 minutes. Frames captured while it waits are not recorded. You won't end up with a folder full of broken one-frame videos. The error ffmpeg reported is written to the [Logs](Logs) tab.
- **ffmpeg stops partway through a healthy recording** — a new video is started straight away.
- **ffmpeg missing while timelapse is enabled** — a warning is logged at most once a minute, and nothing else is affected.
- **Slow disk or encoder** — encoding runs separately from capture, so a slow encoder drops the occasional timelapse frame rather than stalling capture or freezing the app.
- **Changing processing settings** — when you change a setting and the last frame is processed again to show the result, that re-processed frame is not added to the video. Only new captures are recorded.

---

## Using the Recording Window for Capture

> **New in the next release** — not available in version 3.7.6 or earlier.

**Scheduled Capture** on the [Capture Settings](Capture-Settings) tab can follow this tab's recording window. Set **Window source** to **Same as Timelapse** and choose a **Margin**, and the camera starts a little before recording begins and stops a little after it ends. You then only maintain one set of times.
