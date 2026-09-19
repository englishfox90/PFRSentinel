# Meteor Detection

The Meteor Tracker watches your all-sky camera frames for meteor trails. It compares each new frame with the frames before it, looks for a straight streak that appears in one frame only, and filters out planes, satellites, equipment, and other look-alikes. When it finds a meteor it logs the event, saves a thumbnail crop, and adds a card to the Recent Detections list, where you can confirm the detection or reject it as a false positive. The feature runs during ZWO Camera capture only, and is marked **BETA** in the navigation rail.

---

## How It Works

The tracker is built for long exposures (typically 10–20 seconds per frame), where a meteor appears as a complete streak in a single frame and is absent from the frames either side of it.

1. **Detection frame** — each captured frame is converted to greyscale and scaled down (to 1280 px on its long side) before any stretching, so consecutive frames of an unchanging sky are directly comparable
2. **Frame stack** — the last 6 frames are kept. For every pixel, the tracker compares the brightest value in the stack with the average. Stars, equipment, and steady sky glow cancel out; a streak that appears in only one frame stands out
3. **Structure mask** — pixels that are bright in at least half of the stacked frames are masked out, with a small margin. This removes equipment edges and hot pixels, and also *moving* bright structure: the edge of the Moon as it drifts, or a telescope tube that sits in one place early in the stack and another after a slew. A meteor lights up only one frame, so it is never masked
4. **Automatic threshold** — the detection threshold is worked out from the noise level in the stack, and smoothed from frame to frame so it follows changing conditions such as passing cloud
5. **Sky circle** — detection is limited to the circular sky area of the fisheye image, with a soft edge so the circle's boundary is not mistaken for a streak. The circle comes from your [All-Sky Overlay](All-Sky-Overlay) calibration if you have one; otherwise it is detected automatically from the image
6. **Cloud removal** — large bright blobs are removed
7. **Exclusion zones** — areas you have marked with **Not a Meteor** are blanked out
8. **Line detection** — the tracker looks for straight lines at least **Min Trail Length** long, bridging small gaps in faint or broken trails. Wide, fuzzy shapes are rejected, and the trail must be at least faintly bright along its length
9. **Length limit** — a streak longer than half the frame width is rejected as a satellite, plane, or ISS pass rather than a verifiable meteor
10. **One-frame check** — every candidate is held for one frame before it is reported (see below)

### The one-frame check

A meteor is a one-frame event, so each candidate is checked against the frames either side of it:

- **Continues along the same line in the next frame** — it is a plane or satellite. Both detections are discarded, and that track is ignored for about the next 10 minutes so the rest of the pass is not reported.
- **Was already in the same place in the previous frame** — it is a persistent artefact, such as a streak left behind by star drift or a fixed faint feature, not a new event. It is not reported.
- **Does not move along its line in the next frame** — it is reported as a meteor. Its own line is then ignored for the next few frames, so the same meteor is not reported again while its frame is still in the stack.

This means detection starts once 6 frames have been collected after capture starts, and a meteor is reported one frame after it appears. The thumbnail is cut from the frame the meteor was in.

---

## Enabling Detection

Open **Meteor Tracker** from the navigation rail (in the Monitor group) and turn on **Enable Detection** in the Meteor Tracker card. Detection runs on every captured frame on a background thread, without holding up capture or image processing. If a detection pass is still running when the next frame arrives, that frame is added to the stack but not searched.

Detection runs in ZWO Camera mode only. Directory Watch mode does not feed the tracker. The tracker always searches the full frame, even if you crop your outputs with **Output Framing** on the [Image Processing](Image-Processing#output-framing) tab.

---

## Detection Settings

| Setting | Default | Range | Description |
|---------|---------|-------|-------------|
| Min Trail Length | 100 px | 10–500 | Minimum length, in pixels of the processed image (after any **Resize**), for a line to count as a meteor. Higher values reduce false positives but may miss short or faint meteors. |
| Adaptive Sensitivity | On | — | Intended to switch between an automatic threshold and a fixed one. **In the current version the threshold is always calculated automatically from sky noise, whichever way this is set.** |
| Fixed Diff Threshold | 25 | 5–100 | A manual threshold, greyed out while **Adaptive Sensitivity** is on. **Not currently used by the detector** (see above). |
| Detection Cooldown | 30 sec | 0–300 | After a detection is reported, the tracker stops searching for this many seconds, while still collecting frames. Prevents floods of reports during showers or bursts of activity. Set to 0 to search every frame. |

Earlier versions also had **Multi-Frame Confirmation** and a **Detection Method** selector. Both have been removed: the one-frame check above replaces multi-frame confirmation, and there is now a single detection method.

---

## Logging

| Setting | Default | Description |
|---------|---------|-------------|
| Save Detection Log | On | Appends each detection to a log file, one JSON record per line: the time, the number of trails, and each trail's coordinates, length, and angle. Confirmations are recorded too. |
| Log File | *(blank)* | Path to the log file. Leave blank to use the default, `%LOCALAPPDATA%\PFRSentinel\meteor_detections.jsonl`. Use **Browse** to pick a file. |
| Save Annotated Images | Off | Saves a full-frame JPEG copy of each detection with the trails drawn as green lines and their lengths labelled. |
| Annotated Image Dir | *(blank)* | Folder for annotated full-frame images. Leave blank to disable saving them, even when **Save Annotated Images** is on. |

The [Image Library](Image-Library) reads the detection log to show meteors in a night's events timeline. If you turn off **Save Detection Log**, new detections will not appear there.

### Thumbnails

A 300 × 300 pixel crop centred on the longest trail is always saved to `%LOCALAPPDATA%\PFRSentinel\meteor_thumbnails`, whatever the logging settings. The crop is saved clean; the green highlight is drawn by the app on top of it, so you can always inspect the raw streak. Thumbnails stay on disk when capture stops and when they drop off the Recent Detections list. They are only deleted when you click **Not a Meteor**.

---

## Session Status

The **Session Status** card shows live counters, updated every 5 seconds:

| Counter | Description |
|---------|-------------|
| Frames Analysed | Frames received since capture started, not counting frames skipped while the roof gate was closed. |
| Meteors Detected | Trails reported this session. A single detection event can contain more than one trail. |
| Last Detection | Date and time of the most recent detection. |

The counters reset when capture stops.

---

## Recent Detections

The **Recent Detections** card shows cards for up to 10 recent detections, newest first. Each card shows:

- The 300 × 300 pixel thumbnail, with a green box framing the longest streak and its length in pixels. The box frames the trail rather than drawing over it, so the streak itself stays visible
- The detection time
- The number of trails and the length of the longest one, for example "1 meteor • longest 184 px"

| Button | What it does |
|--------|--------------|
| **Hide Highlight** / **Show Highlight** | Toggles the green box so you can see the thumbnail without it. |
| **Confirm Meteor** | Marks the detection as a real meteor. Its thumbnail (and annotated image, if saved) is moved into a `confirmed` subfolder, the confirmation is added to the detection log, and the card shows **✓  Confirmed Meteor** in place of the buttons. |
| **Not a Meteor** | Marks the detection as a false positive. See below. |

The list is cleared when capture stops, but the files on disk are kept.

### Rejecting false positives

Clicking **Not a Meteor**:

1. Creates a permanent **exclusion zone**: a rectangle around the detected streak, extended by 80 pixels on every side
2. Saves the zone to your configuration, so it survives restarts
3. Deletes the detection's thumbnail and annotated image, and removes its card

Detection is skipped inside exclusion zones on all future frames. Use this for fixed trouble spots such as a telescope mount, an antenna mast, a roof edge, or branches that move in the wind. There is currently no way to view or remove exclusion zones inside the app, so use **Not a Meteor** for detections that keep recurring in the same place rather than for one-off events.

---

## Roof Gate

If **Enable ML Analysis** is on in [ML Models](ML-Models), meteor detection only runs while the roof is reported **Open**. It pauses when the roof is reported **Closed**, and also while the roof reading is uncertain. This prevents false detections from the roof surface or indoor lighting. When detection pauses, the frame stack is cleared, so after the roof opens again detection resumes once 6 new frames have been collected. The [Logs](Logs) record "Meteor: detection suspended — roof reported closed" and "Meteor: detection resumed — roof reported open".

If ML analysis is on but the roof model did not load (the ML status reads **✓ Sky model only**), there is never an Open reading, so detection stays paused. The Logs still say "roof reported closed" in this case.

If ML analysis has not produced a result since PFR Sentinel started, detection runs regardless of the roof. Turning **Enable ML Analysis** off part way through does not lift the gate: the tracker keeps using the last roof reading until PFR Sentinel is restarted. This gate is separate from the **Skip Sky Features When Roof Closed** option, which does not affect meteor detection.

---

## Understanding False Positives

Most false detections come from a few sources. Several are handled automatically; the rest you can deal with using the tools on this page.

| Source | How it is handled |
|--------|-------------------|
| Equipment edges, hot pixels | Removed automatically by the structure mask, because they are bright in most frames. Add an exclusion zone for any that still get through. |
| Moon moving across the frame, telescope slews | The drifting Moon edge and a tube that moves between frames are covered by the structure mask. |
| Planes and satellites | Rejected when the trail continues along the same line in the next frame, or when it is longer than half the frame. |
| Leftover streaks from star drift or fixed faint features | Rejected by the one-frame check, because they are still there in the next frame. |
| Clouds | Large bright blobs and wide, fuzzy shapes are rejected. |
| Anything past the edge of the sky circle | Masked out. Calibrating the [All-Sky Overlay](All-Sky-Overlay) gives the tracker an accurate sky circle. |
| Roof or indoor light with the roof closed | Paused by the roof gate when ML Models are enabled. |
| Short, recurring streaks near equipment | Raise **Min Trail Length**, or mark them with **Not a Meteor**. |

---

## Troubleshooting

The [Logs](Logs) include a meteor summary line ("Meteor heartbeat") every 15 minutes while detection is enabled, showing how many frames arrived and how many candidates were rejected at each step. Use it to see why a night produced no detections.

| Symptom | Likely cause | What to do |
|---------|--------------|------------|
| Nothing is ever detected | Directory Watch mode, the roof gate is closed, or capture has not yet collected 6 frames | Check you are in ZWO Camera mode, check the ML roof status, and look for the heartbeat line in the Logs. |
| "Meteor: frames stopped" in the Logs | Frames stopped reaching the tracker (capture stopped or stalled) | Check that capture is still running. |
| Warning that the hot mask covers a large part of the frame | Most of the image is being masked as bright structure, so detection cannot fire | Check exposure and the image for large bright areas such as moonlight or dawn. |
| Warning that there is no sky circle | No all-sky calibration and the circle could not be detected, so the whole frame is searched | Calibrate the [All-Sky Overlay](All-Sky-Overlay). |
| The same spot is reported repeatedly | A fixed feature that still gets through the masks | Click **Not a Meteor** on it. |
| Too many short false detections | Equipment or noise producing short lines | Raise **Min Trail Length**, for example to 150 px. |

---

## Tips

- **Start with the defaults.** 100 px minimum length and a 30 second cooldown suit most all-sky setups.
- **Calibrate the All-Sky Overlay** if you have not already — it gives the tracker an accurate sky circle.
- **Use exclusion zones** for persistent trouble spots rather than raising **Min Trail Length** for the whole sky.
- **Review your first few nights.** Confirm or reject the detections, and turn on **Save Annotated Images** if you want full-frame context, before relying on the log.
