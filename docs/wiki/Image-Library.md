# Image Library

The Library tab keeps a rolling history of every processed frame so you can look back over a night without digging through the output folder. Frames are grouped into nights, each night can be scrubbed through like a video, and a coloured condition band shows at a glance when the roof was open, when the sky was clear, and where capture stopped. The Library works in both capture modes (ZWO camera and directory watch) and keeps updating while capture is running.

Open it from **Library** in the Publish group of the navigation rail. It uses the full width of the window.

---

## How Frames Are Archived

Each time a frame finishes processing, a small copy is saved to the library in the background, so archiving never slows down capture.

- The archived copy is the finished output image (after processing and overlays), downscaled so its longest edge is no larger than **Max Dimension** (750 px by default) and saved as a JPEG.
- The all-sky overlay is only included if you have turned on **Web server (also Image Library)** under **Burn overlay into output** (see [All-Sky Overlay](All-Sky-Overlay)).
- If you crop your outputs with **Output Framing** on the [Image Processing](Image-Processing#output-framing) tab, the archived copy is the cropped image.
- Alongside each image the library records the capture time, exposure, gain, sensor temperature, camera name, resolution, roof status and sky condition (from the [ML models](ML-Models)), cloud cover (from [weather](Weather-Setup)), and star count, seeing and FWHM when star detection ran.
- Frames are stored in `%LOCALAPPDATA%\PFRSentinel\Library`, in one folder per date, with an index file (`library.db`) alongside them. This is separate from your normal output directory, and the output directory's storage cleanup does not affect it.

The library also runs in headless mode, where it archives frames and serves the web endpoints without a window.

---

## Library Settings

Library settings are in the **Image Library** card on the [Output](Output-Settings) tab.

| Setting | Default | Range | Description |
|---------|---------|-------|-------------|
| **Enable Library** | On | On / Off | Keep a browsable history of downscaled frames. When off, no new frames are archived and the `/library` web endpoints are turned off too; frames already stored stay browsable in the app. |
| **Retention** | 7 days | 1 - 365 days | Frames older than this are deleted. |
| **Max Size** | 2.0 GB | 0.1 - 1000.0 GB | When the library grows past this size, the oldest frames are deleted until it fits. |
| **Max Dimension** | 750 px | 100 - 4000 px | Longest edge of stored images. Applies to new frames only. |
| **JPEG Quality** | 85 | 1 - 95 | Compression quality of stored images. Applies to new frames only. |
| **Expose Web API** | On | On / Off | Serve the library over the built-in web server at `/library` and `/library/image`. Has no effect while **Enable Library** is off. See [Web Server](Web-Server). |

### Retention

Both limits apply together, whichever is reached first: frames past the retention age are removed, then the oldest remaining frames are removed if the library is still over **Max Size**. Clean-up runs in the background as frames are archived, at most every 15 minutes, and removes both the image and its index entry. Date folders are removed once they are empty. Because clean-up is driven by new frames, nothing is removed while capture is stopped or the library is disabled.

If you capture at a short interval, the size limit is usually what decides how much history you keep. Raise **Max Size** or lower **Max Dimension** / **JPEG Quality** if you want more nights.

---

## Nights

A "night" runs from local noon to the following local noon, so an evening-to-morning session is never split at midnight. A frame captured at 03:00 on 5 March belongs to the night of 4 March. All times in the Library are the PC's local time.

---

## Session List

The first screen lists one card per night, newest first.

### Controls

| Control | Description |
|---------|-------------|
| **Show** | Range of nights to list: **All nights**, **Last 7 days**, or **Last 30 days**. |
| Session count | Total nights and frames in the selected range, e.g. "12 sessions · 48,210 frames". |
| **Refresh** | Reloads the list. Normally not needed, because the list updates itself (see [Live Updates](#live-updates)). |

If nothing has been archived for the selected range, the list shows "No images archived for this range yet."

### Session Cards

Each card shows:

| Element | Description |
|---------|-------------|
| Cover image | A frame from the middle of the night. |
| Date | The night's date (the evening the session started). |
| Time range | Local time of the first and last frame, e.g. "19:42 – 06:15". |
| Status badge | **clean** (green) when nothing notable happened; **roof closed** (red) when the roof never opened all night; or the length of the longest capture gap, e.g. "1h 12m gap" (red). A gap takes priority over roof closed. |
| Condition band | The whole night as a coloured bar (see [Condition Band](#condition-band)). |
| Stats | Frame count, the lowest sensor temperature, the peak star count, and the best sky quality (seeing) of the night. Star and seeing figures only appear on nights where star detection ran. |
| Percent clear | The share of the night's frames coloured green on the condition band: clear with the roof open, or clear with the roof state unknown. Shown in green. |

Click a card to open that night.

---

## Condition Band

The same colour scheme is used on the session cards and under the night scrubber. The dark grey capture-gap runs only appear on the session cards: the scrubber gives every frame an equal slice, so gaps are shown there as pins instead (see [Scrubber](#scrubber)).

| Colour | Meaning |
|--------|---------|
| Green | Roof open and sky clear |
| Amber | Roof open but sky not clear |
| Red | Roof closed |
| Dark grey | Capture gap: no frames for more than one hour |
| Light grey | Unknown: no roof, sky, or cloud information was stored for these frames |

How each frame is classified:

- **Roof** comes from the roof classifier, so roof colours only appear when ML models are enabled on the [Image Processing](Image-Processing) tab.
- **Sky** is "clear" when the sky classifier reports **Clear**. Any other sky result (for example **Partly Cloudy**) counts as not clear.
- If there is no sky result, cloud cover from OpenWeatherMap is used instead: 30% or less counts as clear.
- If the roof state is unknown, the frame is still coloured green or amber from the sky or cloud data; with no data at all it is grey.

Without ML models or weather configured, bands will mostly be grey.

---

## Night View

Clicking a session card opens the night view. Click **Library** (top left) to go back to the session list. The header shows "Night · " and the date, plus a red badge with the longest capture gap if the night had one.

The night opens on its **latest** frame, so you see how the night ended (or, while capturing, the current frame).

### Preview

The large image shows the frame under the playhead, with its capture time and frame number at the top centre (e.g. "23:14 · frame 1,204").

### Current Frame Panel

To the right of the preview, the **CURRENT FRAME** panel shows a condition badge (**Clear**, **Cloudy**, **Roof closed**, or **Unknown**) and these details for the frame:

| Field | Description |
|-------|-------------|
| Sky | Sky condition from the sky classifier; if unavailable, "Clear" or "Cloudy" derived from cloud cover. Green for clear, amber for cloudy, grey when the roof was closed. |
| Roof | Roof status: green for Open, red for Closed. |
| Seeing | Seeing label and FWHM, e.g. "Good · 2.3 px". |
| Stars | Detected star count. |
| Clouds | Cloud cover percentage from the weather service. |
| Sensor | Camera sensor temperature. |
| Exposure | Exposure time in seconds. |
| Gain | Camera gain. |
| Camera | Camera name. |
| Resolution | Size of the stored library image (not the original frame). |

Fields with no data show "—".

### Events Tonight

Below the frame details, **EVENTS TONIGHT** lists what happened during the night in time order:

| Event | Description |
|-------|-------------|
| Capture gap | A break of more than one hour between frames, shown with its length, e.g. "1h 05m gap". |
| **Roof opened** / **Roof closed** | The first frame where the roof classifier reported a change. |
| **Meteor** | A meteor detection from [Meteor Detection](Meteor-Detection), matched to the nearest frame. Several detections in one frame show as "Meteor ×3". |

Click an event to jump straight to that frame. Clicking a capture gap jumps to the first frame after the gap. A night with nothing to report shows "No events tonight".

---

## Scrubber

The scrubber along the bottom is a timeline of the whole night:

- **Filmstrip**: thumbnails sampled evenly across the night.
- **Condition band**: the colour for each frame, lined up under the filmstrip.
- **Event pins** above the filmstrip: a circle for a capture gap, a triangle pointing up for roof opened or down for roof closed, and a diamond for a meteor. Hover a pin to see what it is and when it happened; click it to jump to that frame.
- **Playhead**: the vertical line marking the frame shown in the preview.
- **Time labels** underneath, showing the capture time of the frames at those positions.

Every frame gets an equal slice of the timeline, so the scrubber is laid out by frame rather than by clock time. A long capture gap therefore shows as a pin, not as empty space.

Click anywhere on the scrubber, or drag along it, to move the playhead; the preview follows as you drag.

### Playback Controls

| Control | Description |
|---------|-------------|
| Previous / Next | Step back or forward one frame. |
| **Play** / **Pause** | Play through the night from the playhead. Playback stops at the last frame. |
| **1×**, **8×**, **30×** | Playback speed: how many frames each step advances. The selected speed is highlighted. |
| Frame counter | Position in the night, e.g. "frame 1,204 / 3,880 · drag to scrub". |

---

## Live Updates

While capture is running, the Library keeps itself up to date without pressing **Refresh**:

- **Session list**: when it is on screen, it reloads within a couple of seconds of new frames being archived. If you are elsewhere, it reloads the next time you return to it.
- **Open night**: new frames for that night are added to the timeline as they are archived. If the playhead is parked on the last frame (and playback is not running), it follows each new frame and the filmstrip extends to the live edge. If you have moved the playhead back to look at earlier frames, it stays where it is while the timeline grows.

---

## Notes

- Deleting frames by hand from the Library folder is safe: missing images are dropped from the index the next time the app starts.
- Library images are for browsing. For full-resolution output, use file output on the [Output](Output-Settings) tab, and for video use [Timelapse](Timelapse).
