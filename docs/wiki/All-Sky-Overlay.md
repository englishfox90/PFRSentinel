# All-Sky Overlay

The All-Sky Overlay annotates your fisheye camera frames with astronomical labels — constellation lines and abbreviations, named bright stars, Messier and NGC/IC deep-sky objects, the planets, and the Moon. All positions are computed locally with no internet connection required. A lens calibration model fitted to your own camera projects sky coordinates onto the image, so labels land on the objects they name. By default the overlay appears only in the app's live preview; you can choose to burn it into saved images, web output, and timelapses.

The feature is designed for all-sky cameras: a fisheye lens on a square (or near-square) sensor, with the whole sky circle visible in the frame. Regular lenses, or wide sensors that crop the fisheye circle, will not calibrate correctly. If a frame is much wider than it is tall, calibration still runs, but a warning is written to the [Logs](Logs); it is added to the status line only if the calibration fails.

---

## How It Works

Each frame passes through a rendering pipeline that projects catalogue positions onto the image:

1. **Calibration model** — a fisheye lens model maps altitude/azimuth positions to pixel positions for your specific camera and lens
2. **Coordinate conversion** — catalogue positions are converted to local altitude/azimuth using your observatory's latitude, longitude, and the current time
3. **Equipment avoidance** — stars detected in the frame mark out open sky, so labels are not placed over the mount, pier, roof edges, or other obstructions
4. **Layer rendering** — constellations, bright stars, Messier objects, NGC/IC objects, and planets are drawn in that order, sharing one label-placement system so labels do not overlap
5. **Edge fading** — constellation lines fade out near the edge of the sky circle rather than cutting off abruptly

The overlay is drawn on a copy of the processed frame. Your clean image is never modified unless you turn on one of the **Burn overlay into output** options.

### When the overlay is drawn

The overlay and automatic calibration only run when it makes sense to look at the sky:

- **The sun must be below civil twilight** (6 degrees below the horizon). This check needs your latitude and longitude; without them it is skipped.
- **The roof must not be reported closed.** If [ML Models](ML-Models) are enabled and the roof classifier reports **Closed** on two frames in a row, the overlay and calibration pause. A single Closed frame is ignored (from the next release; in 3.7.7 and earlier one frame was enough, which could blank the overlay for a frame when the exposure changed). The overlay follows the roof reading in Directory Watch mode as well; in 3.7.7 and earlier it ignored the roof there. Rigs with no roof (for example an open-air all-sky camera) can turn off **Skip Sky Features When Roof Closed** in the ML settings so a misread "Closed" does not suppress them.
- **A calibration must exist.**

If you shrink images with **Resize**, the calibration is scaled to match automatically. If you crop your outputs with **Output Framing** on the [Image Processing](Image-Processing#output-framing) tab (new in the next release), the calibration is scaled and then shifted to match the cropped image, so a burned-in overlay still lines up with the sky; calibration itself keeps using the full frame. If the image has been cropped to a different shape some other way, the overlay is skipped rather than drawn in the wrong place.

---

## Requirements

- **Observatory coordinates** — enter **Latitude** and **Longitude** in the **Coordinates** row of the Weather API card on the [Settings](Settings) tab (see [Weather Setup](Weather-Setup)). A city name alone is not enough: calibration and the overlay need numeric coordinates, and calibration does not run while both are zero. An API key is not required for this.
- **An accurate PC clock** — star positions are computed from the computer's clock, so keep Windows time synchronised. A clock that is minutes off shifts every label.
- **Clear skies** — calibration needs to see stars. Frames with too few detectable stars are skipped automatically.
- **ZWO Camera mode for automatic calibration** — in Directory Watch mode the overlay still appears in the preview, and **Calibrate Now** and **Guided Calibration** work on the last full frame, before resize, cropping and overlays, but frames are not collected for automatic calibration and the burn-in options have no effect.

---

## Enabling the Overlay

Open **All-Sky** from the navigation rail (in the Image group).

### Enable Overlay

| Setting | Default | Range | Description |
|---------|---------|-------|-------------|
| All-Sky overlay enabled | Off | — | Master switch for the overlay. Automatic calibration also only runs while this is on. |
| Max objects visible | 15 | 5–50 (steps of 5) | Maximum number of labelled bright stars, Messier objects, NGC/IC objects, and planets combined. The brightest visible objects win. Constellations do not count towards this limit. |

### Burn overlay into output

Off by default: the overlay only shows in the app's live preview. Turn an option on to bake the overlay into that destination's actual pixels.

| Setting | Default | Description |
|---------|---------|-------------|
| Saved image (also Discord) | Off | Draw the overlay into the image saved to disk. Discord and other notifications that post the saved file get it too. |
| Web server (also Image Library) | Off | Draw the overlay into the image served by the [Web Server](Web-Server) and archived to the [Image Library](Image-Library). |
| Timelapse video | Off | Draw the overlay into frames written to the [Timelapse](Timelapse). |

These options apply in ZWO Camera mode. Directory Watch mode always keeps its outputs clean.

---

## Calibration

Before the overlay can place labels accurately, PFR Sentinel needs to learn how your fisheye lens maps the sky onto the sensor: the optical centre, the lens distortion, the camera's rotation and tilt, and whether the image is mirrored. It does this by detecting stars in your frames and matching them against the Yale Bright Star Catalogue.

**Calibration is specific to your installation.** It depends on exactly how the camera is mounted and pointed, so if you move, rotate, or re-mount the camera, recalibrate: use **Reset Calibration** and let it rebuild, or run **Guided Calibration**.

The calibration is saved to `%LOCALAPPDATA%\PFRSentinel\allsky_calibration.json` and loaded automatically on startup. Don't copy it to another installation.

### Lens Calibration card

The **Lens Calibration** card shows a coloured quality badge (hover it for a description), a status line such as "Calibrated: 142 stars, RMS=6.20px (good)", and five buttons:

| Button | What it does |
|--------|--------------|
| **Calibrate Now** | Runs an immediate single-frame calibration on the most recent clean frame. The button reads **Calibrating…** while it works. Needs a frame, so start capture first. |
| **Guided Calibration…** | Opens a dialog where you identify bright stars by hand. The dependable option for obstructed, tilted, or hazy views where automatic calibration cannot work out the orientation. See [Guided Calibration](#guided-calibration). |
| **Reset Calibration…** | Deletes the saved calibration after a confirmation prompt. See [Reset Calibration](#reset-calibration). |
| **Dump calibration buffer** | Saves the star positions automatic calibration has collected to a small file for a bug report. See [Dump calibration buffer](#dump-calibration-buffer). |
| **Reset Equipment Map…** | Forgets where the app has learned that telescopes, mounts and other equipment sit in the frame, after a confirmation prompt. New in the next release. See [Equipment avoidance](#equipment-avoidance). |

When **Calibrate Now** or **Guided Calibration** succeeds, a notification is posted if **Post Calibration** is enabled in your [Hermes Notifications](Hermes-Notifications) settings. Improvements made by automatic calibration do not send a notification.

### Quality levels

| Badge | Description | Requirements |
|-------|-------------|--------------|
| None | Not calibrated | No calibration, so the overlay is not drawn. Also shown for a saved automatic fit over fewer than 8 matched stars: that overlay is still drawn, but the model is not trusted and any later calibration replaces it. |
| Preliminary | Single image — rough overlay | Any accepted calibration that does not meet a higher level, such as a single-frame or guided calibration. |
| Acceptable | Multi-image — improving | 3+ frames, 30+ matched stars, RMS 15 px or better. |
| Good | Multi-image — accurate | 10+ frames, 100+ matched stars, RMS 12 px or better. |
| Excellent | Long baseline — best accuracy | 20+ frames spanning 60+ minutes, RMS 8 px or better. |

RMS is the typical distance, in pixels, between where matched stars appear and where the model predicts them. Lower is better.

A guided calibration is rated **Preliminary** because it rests on one frame and a handful of stars, not because it is unreliable: its orientation is pinned by stars you named yourself. Automatic refinement then raises the rating as frames accumulate.

> **New in the next release** — not available in version 3.7.7 or earlier.
>
> A fit whose star matches are no better than chance is capped at **Preliminary**, however many frames it spans. On a high-resolution camera, a wrong calibration still "matches" hundreds of stars by coincidence — any star that happens to fall within the matching distance counts — and its RMS then measures that distance rather than the sky. Every calibration now records the matching distance it was judged at and how far above chance its match count was, and a rating above Preliminary needs both to look like a real fit: an RMS well under the matching distance, and at least twice the matches chance would supply. Hover over the badge to see the reason when a rating has been capped. Calibrations saved by an earlier version have neither number recorded and keep their rating until they are next refined.

### When the badge turns amber

> **New in the next release** — not available in version 3.7.7 or earlier.

The badge describes how well the calibration fitted **when it was saved**. It is not re-measured every frame, so on its own it cannot tell you the overlay still lines up tonight. PFR Sentinel now says so when it has reason to doubt it: the badge turns amber, gains a suffix, and an explanation appears under the status line.

| Badge reads | What it means |
|-------------|---------------|
| **Good — unconfirmed** (any level) | The last three or more automatic refinements were rejected, and recent frames could not confirm the saved calibration either way. |
| **Good — check alignment** (any level) | The last three or more refinements were rejected **and** the saved calibration missed the bright stars in the last three frames. |
| **Preliminary — check alignment** | The saved calibration matched the stars in recent frames no better than chance would, in two automatic runs in a row. See below. |

Neither changes or deletes your calibration, and cloud causes both: a cloudy night hides the stars the check relies on. On a clear night, look at the overlay in the preview. If the constellation lines sit on their stars, nothing is needed and the badge returns to normal after the next successful refinement. If they don't, run [Guided Calibration](#guided-calibration). A calibration that still hits its bright stars never shows the caution, however many refinements are rejected.

> **New in the next release** — not available in version 3.7.7 or earlier.
>
> Each time automatic refinement runs, the saved calibration is now also checked against the same frames the refinement used: how many stars it matches, compared with how many a wrong calibration would match by coincidence. If it comes out no better than chance in two runs in a row, the badge drops to **Preliminary — check alignment**, its tooltip shows the measurement (how many stars matched against how many chance would supply) together with the rating from when the calibration was saved, and the status line reads "matches at chance level" until a later run clears it. This check works on obstructed and moonlit skies where the bright-star check cannot reach a verdict, which is why it can appear without the "refinements rejected" trigger above. It is skipped, rather than counted against the calibration, when the frames hold too few stars to judge by (cloud, or glare from the Moon). The calibration file itself is not changed, and nothing else is saved or re-pointed: the badge shows the live verdict, the file keeps its own rating.

> **New in the next release** — not available in version 3.7.6 or earlier.
>
> A lens model has eight unknowns, so a fit over only a handful of stars can report a flattering RMS while being badly wrong. Automatic calibration now needs at least 8 matched stars to succeed at all, and a saved model below that is rated **Not calibrated** so a better one can replace it. Guided Calibration is unaffected — the stars there are ones you identified yourself, and five is enough.

### Automatic calibration

With the overlay enabled and capture running in ZWO Camera mode, calibration runs in the background:

- **No calibration yet** — PFR Sentinel tries a quick single-frame calibration (at most once every 3 minutes), so clear, unobstructed skies get an overlay straight away. At the same time it collects star detections, and once it has at least 15 usable frames spanning 35 minutes or more it attempts a multi-frame calibration. The long wait is deliberate: a fisheye pointed near the zenith looks almost the same at many rotations, and only watching the sky turn over time reliably pins down the true orientation.
- **Refining an existing calibration** — once 3 or more frames spanning at least 5 minutes have been collected, the model is refined against them, no more often than every 2 minutes. If refinements keep failing, the wait between attempts doubles each time, up to 30 minutes.
- The status line shows progress, for example **Auto-calibrating…**, **Calibrating (15 frames)…**, or **Refining (20 frames)…**.

A refined model only replaces the current one if it is actually better: it must either reach a higher quality level without being more than 15% worse on RMS, or have a lower RMS with at least as many matched stars. Otherwise the current calibration is kept.

### Why an automatic calibration may be rejected

A fisheye fit can settle on a solution that matches some stars but gets the orientation wrong — mirrored east–west, at the wrong scale, or rotated. A wrong calibration is worse than none, because every later refinement would start from it. PFR Sentinel therefore checks new calibrations against independent evidence before saving them:

- **The celestial pole.** Over about 35 minutes of frames, stars circle the celestial pole while Polaris stays almost still. PFR Sentinel measures where the pole actually is in your image and rejects calibrations that put it somewhere else. This check only works in the northern hemisphere at latitudes of 20 degrees or more, and only when the measurement is trustworthy: if lights on the mount, pier, or site give contradictory readings from run to run, the pole is treated as unknown and the check is skipped rather than guessed.
- **Bright stars.** The calibration must line up with the brightest stars in the frame.
- **Continuity with a trusted calibration.** Once a calibration is trusted — it came from Guided Calibration, or the measured pole confirmed it — automatic replacements must agree with it: the same mirror orientation, a plate scale within 10%, and the pole in the same place.

If a **Calibrate Now** result disagrees with the measured pole, it is not saved and the status line reads "Calibration rejected — it disagrees with the measured celestial-pole position. Let capture run longer, then retry."

If refinements are rejected three times in a row, PFR Sentinel first checks whether the current calibration still lines up with the bright stars in recent frames. If it does, the calibration is kept. If not, it tries a fresh calibration from scratch.

> **New in the next release** — not available in version 3.7.6 or earlier.
>
> Because the current calibration has just failed that same bright-star check, a fresh result that also lines up with the bright stars replaces it outright, whatever the RMS says — a model that cannot find the bright stars does not get to veto one that can on a flattering-but-meaningless residual. (This override does not apply to a Guided Calibration; see [Guided calibration is trusted over automatic calibration](#guided-calibration-is-trusted-over-automatic-calibration).) Short of that, a fresh result still only replaces the current calibration when the pole check or a trusted calibration backs it up, or when it is clearly better — at least 15% lower RMS with at least as many matched stars. A near-identical score is not enough to swap one orientation for another.

> **New in the next release** — not available in version 3.7.7 or earlier.
>
> Two of those rules have tightened. First, when a fresh result is backed by the pole check or a trusted calibration, it must also stand on its own numbers before it can skip the RMS comparison: an RMS well under the matching distance it was judged at, and at least twice the matches chance would supply. A measured pole can be a light on the pier rather than Polaris, and on its own that must not be enough to install a calibration whose matches are coincidences. A result that fails this is held to the normal comparison instead. Second, a current calibration that has matched the stars no better than chance in two runs in a row (see [When the badge turns amber](#when-the-badge-turns-amber)) is treated like one that missed the bright stars: a fresh result that passes both checks replaces it outright, whatever the RMS says. A Guided Calibration is still never replaced this way.

> **New in the next release** — not available in version 3.7.6 or earlier.
>
> A fresh from-scratch attempt is expensive, so if it keeps getting rejected, PFR Sentinel waits longer before trying again each time (10, then 20, then 40, then 80 minutes). After four rejections in a row it stops trying and the status line reads **"Auto-calibration paused: 4 re-calibrations rejected — run Guided Calibration (All-Sky settings)"**. This means the current view can't be worked out automatically — most often a heavily obstructed, tilted, or hazy sky — and [Guided Calibration](#guided-calibration) is the way forward: it only needs you to identify a few stars by hand. Regular refinement of an existing calibration is unaffected and keeps running. The pause lifts on its own after about 12 hours, so a fresh attempt is made on a later night even if you don't act on it.

### Guided calibration is trusted over automatic calibration

A calibration built with **Guided Calibration** comes from stars you identified yourself, so it outranks automatic results:

- It is never rejected by the pole check. If the measured pole disagrees, a warning is written to the [Logs](Logs); if the overlay then looks wrong, re-check your star identifications.
- Automatic refinements must keep its mirror orientation, scale, and pole position before they can replace it. A multi-frame refinement that agrees with it and reaches a higher quality level does replace it, because a fit over many star matches across the whole sky is more accurate than one over a handful of clicks.

### Backup of the previous calibration

Before an automatic calibration overwrites the saved one, the previous file is copied to `%LOCALAPPDATA%\PFRSentinel\allsky_calibration.previous.json`. This keeps one step of history in case an automatic update makes the overlay worse. Results from **Calibrate Now** and **Guided Calibration**, which you asked for, are saved without making a backup.

---

## Guided Calibration

Use guided calibration when automatic calibration cannot get the orientation right: heavily obstructed views, strongly tilted cameras, hazy skies, or a camera that was just moved. It needs a recent frame (start capture first) and your latitude and longitude.

> **New in the next release** — not available in version 3.7.7 or earlier.

The **Guided All-Sky Calibration** dialog opens nearly full-screen and can be maximised. The latest frame, brightened so stars are visible, takes almost all of it; the controls are a narrow column on the right. The dialog stays open for the whole session — while it solves, if the solve fails, and until you have saved a result or cancelled.

### Moving around the frame

| Action | What it does |
|--------|--------------|
| Scroll wheel, or **+** / **-** | Zoom in and out around the cursor. |
| Drag | Pan when zoomed in. |
| Double-click, **0**, or the **Whole frame** button | Return to the whole frame. |
| Hover | At whole-frame zoom a loupe follows the cursor with a near full-resolution close-up, so you can tell close pairs apart (for example Mizar and Alioth). The green circle in the loupe shows how close a click must be to snap to a star. The loupe switches off once you zoom in past full resolution. |

### Identifying stars

1. **Click a bright star.** The click snaps to the nearest star PFR Sentinel detected. If no detected star is nearby, the click stays where you placed it, with a warning that the solve is much less accurate with unsnapped clicks.
2. **Choose which star it is.** Type in **Search for a star by name…** to filter the list. It contains bright stars (magnitude 3.5 or brighter) more than 15 degrees above the horizon at the frame's capture time, each shown with its magnitude and altitude. Stars without a proper name are listed by Bayer designation or catalogue number.
3. **Click Add this star.** The star appears under **Identified stars** with a tick, or with a warning mark if the click did not snap. Each star can only be used once. Select an entry and click **Remove selected** to take it out; selecting an entry while zoomed in also brings that star into view.
4. **After three stars, let the suggestions help.** Three identified stars are enough to work out roughly where every other bright star must be, so the 30 brightest are labelled on the frame with dashed blue circles (fainter where no detected star sits nearby). Click a labelled star and its name is filled in for you — check it, then **Add this star**. If instead you see **Check your stars**, the stars identified so far disagree with each other or with the sky in the frame: one of them is probably wrong, most likely the one you just added.
5. **Identify at least 5 stars spread across the sky.** **6 or more** lets the solver recover automatically if one turns out to be wrong. The button shows your progress, for example **Solve (3/5)**, and becomes available at five.
6. **Click Solve.** A progress bar runs for the few seconds the solve takes.

### If the solve fails

Nothing is lost. Every star you identified stays where it was, the message names the star that did not fit and how far off it is, that star is shown in red on the frame and in the list (and selected, ready for **Remove selected**), and the others show their own error. Remove or re-identify the suspect and click **Solve** again. The full detail for every star is also in the [Logs](Logs).

### Checking and saving the result

A solve that passes is **not saved yet**. The dialog shows the RMS error and draws solid blue circles where the new calibration puts the 40 brightest stars, over the same frame. Zoom in and check that they sit on real stars, then:

- **Save calibration** saves it, closes the dialog, and the overlay starts using it. The Lens Calibration status line reads "Guided calibration saved: …" and a notification is added.
- **Adjust stars** returns to identifying stars without saving, with everything you identified intact.

Closing the dialog with stars identified asks for confirmation first, so a stray Esc does not discard your work.

### Outlier rescue

If the full set of stars does not fit well and you identified more than five, the solver tries leaving out one or two of them. If a left-out click actually sits on a different bright star, it is re-identified as that star and kept. The result explains what happened, and the affected star is shown in orange — for example **Pollux → Sirius** when a star you named turned out to be a different one, or **left out** when the solve used 6 of 7 stars.

---

## Reset Calibration

**Reset Calibration…** asks "Reset calibration?" and, if you click **Reset**, deletes the saved calibration. Use it when the overlay is badly misaligned and refuses to improve — a bad saved calibration can hold back every automatic refinement.

After a reset the badge returns to **None** and the overlay stops drawing. Automatic calibration starts over using the frames already collected, or you can run Guided Calibration straight away. A reset cannot run while a calibration is in progress.

From the next release a reset also forgets the learned [equipment map](#equipment-avoidance). To forget only the map and keep the calibration, use **Reset Equipment Map…** instead.

---

## Dump calibration buffer

> **New in the next release** — not available in version 3.7.7 or earlier.

Automatic calibration works from a rolling collection of star positions measured on recent frames (up to 60 frames, no images). **Dump calibration buffer** writes that collection to a small file so it can be replayed by the developers when calibration keeps failing on your sky. The status line shows where the file was saved, or "Calibration buffer is empty" if no frames have been collected yet — frames are gathered only while capture is running with the overlay enabled and the sky is dark enough for stars. The file also gets written on its own when automatic re-calibration gives up after four rejected attempts (development builds write one at every basin-escape attempt). The five newest dumps are kept in `%LOCALAPPDATA%\PFRSentinel\allsky\`, and [Export Diagnostics](Diagnostics-Export) adds the newest one to the bundle, so leave capture running for a clear night and then export the bundle. The file holds star positions, frame times and your site coordinates rounded to about a kilometre; it never contains images.

---

## Constellations

Draws the 88 constellation stick figures with their three-letter IAU abbreviations (for example UMa, Ori, Cyg).

| Setting | Default | Description |
|---------|---------|-------------|
| Show constellations | On | Show the constellation layer. |
| Lines | On | Draw the stick-figure lines. |
| Labels | On | Show the abbreviation, placed at the centre of the visible part of the figure. |
| Color | Blue | Line and label colour. Choose from 10 preset colours: Blue, Orange, Green, Red, Cyan, Yellow, Purple, Pink, White, Teal. |

A constellation's lines are drawn only when every line endpoint in view falls in open sky. If any part of the figure is behind equipment, the whole figure is hidden rather than drawn partially. Lines fade out smoothly towards the edge of the sky circle.

---

## Bright Stars

Labels named bright stars. Labels only, with no marker — the star itself is the marker.

| Setting | Default | Range | Description |
|---------|---------|-------|-------------|
| Show named bright stars | Off | — | Show the bright star layer. |
| Max magnitude | 3 | 1–5 | Only label stars at least this bright. Lower values label fewer, brighter stars. |
| Use Bayer designation when unnamed | Off | — | Label stars that have no proper name by their Bayer designation and constellation (for example "δ Vel"). When off, only stars with proper names are labelled. |
| Color | Yellow | — | Label colour. |

Star labels are placed beside their star and never on top of it, or on top of any other labelled star. Bright stars share the **Max objects visible** budget with the other object layers.

Leave **Max magnitude** at 3 unless you have a reason to change it. Magnitude 2 leaves only about ten stars in the whole sky — too few to fill a **Max objects visible** budget of 15 on a moonlit night, when fainter objects are hidden.

---

## Messier Objects

Labels the 110 Messier objects (galaxies, nebulae, and star clusters), with the common name where there is one — for example "Andromeda Galaxy (M31)".

| Setting | Default | Description |
|---------|---------|-------------|
| Show Messier objects | On | Show Messier object labels. |
| Color | Orange | Label colour. |

---

## NGC/IC Objects

Labels objects from the NGC and IC catalogues. The catalogue holds several thousand objects, so a magnitude filter controls how dense the layer is.

| Setting | Default | Range | Description |
|---------|---------|-------|-------------|
| Show NGC objects (mag filtered) | Off | — | Show NGC/IC object labels. |
| Max magnitude | 8 | 5–12 | Only label objects at least this bright. Lower values show fewer, brighter objects. |
| Color | Green | — | Label colour. |

Objects that also have a Messier number are skipped, since the Messier layer already labels them.

---

## Planets and Moon

Labels Mercury, Venus, Mars, Jupiter, Saturn, Uranus, Neptune, and the Moon.

| Setting | Default | Description |
|---------|---------|-------------|
| Show planets & Moon | On | Show planet and Moon labels. |
| Color | Pale yellow (#FFFFCC) | Label colour, shared by all the planets and the Moon. |

Positions are computed from simplified orbital theory (Meeus, *Astronomical Algorithms*), accurate to a few arcminutes — far better than label placement needs. No internet connection or external ephemeris is used. The Moon's position is corrected for your location on the Earth's surface, which can shift it by about a degree.

---

## Label Placement

- Labels are only drawn for objects more than 10 degrees above the horizon, and only where the frame shows open sky. Open sky is worked out from where stars are detected.
- Each label is tried in several positions around its object (right, left, below, above, then the diagonals) and never covers the object it names. A label that would overlap one already placed is dropped. Placement order is constellation abbreviations, then bright stars, Messier, NGC/IC, and planets.
- **Max objects visible** picks the brightest visible objects.
- Label text scales with the image size, so labels look the same at any resolution.

### Equipment avoidance

> **New in the next release** — not available in version 3.7.7 or earlier.

Labels are only placed on open sky. On each frame, the stars that were detected mark out where the sky is, and those results are combined over the last 15 frames. On its own that knowledge is short-lived: it starts from nothing every session and fades during a cloudy or moonlit spell. In version 3.7.7 and earlier, once it faded the app fell back to judging the raw brightness of the image, which put labels on lit telescopes on a bright night and nowhere at all on a dark one.

From the next release the app also keeps an **equipment map**: a slow memory of where telescopes, mounts, the pier and other obstructions sit in the frame, built up over hundreds of frames and saved between sessions.

- **What teaches it.** Only frames taken when the overlay is allowed to draw (sun down, roof not reported closed). A frame with plenty of detected stars marks the sky it saw as sky, and the parts of the sky circle where it saw no stars at all as equipment. A frame with few stars — thin cloud, a bright Moon — can only add sky, never take it away, and the Moon's own glare is left out, so glare never reads as equipment.
- **How it heals.** The map changes slowly: a telescope moved to a new place is learned over about a night, and the place it left is forgotten over about two. It removes places from labelling; it never switches labelling off.
- **How it is used.** While the recent frames have a good view of the sky, a label needs both the recent frames and the map to agree that its spot is sky. When the recent frames lose the sky (cloud, the Moon), the map alone decides, so labels keep their places instead of disappearing. Before either exists — the first minutes of a first session — labels are placed anywhere inside the calibrated sky circle, so they may briefly sit on equipment.
- **Resetting it.** **Reset Equipment Map…** on the Lens Calibration card forgets the map after a confirmation; use it after moving the camera or rearranging the rig. **Reset Calibration…** forgets it too. Changing the image size or the output crop starts a new map automatically.

The map is saved in the app's data folder as `allsky_obstruction.npz`, at most every ten minutes and when capture stops. Like the calibration, it belongs to one installation: do not copy it to another rig.

### Stable labels from frame to frame

> **New in the next release** — not available in version 3.7.6 or earlier.

Labels hold steady between frames instead of blinking on and off or jumping around, which matters most in timelapses:

- **Open sky** is combined over recent frames, so a noisy frame does not hide or reveal labels. If a frame has too few detected stars, the last good result is kept.
- **Max objects visible** is sticky: an object already on screen keeps its place until a newcomer is clearly brighter.
- **Label position** is remembered: each label tries the same side of its object as in the previous frame first.

In version 3.7.6 and earlier, each frame is labelled from scratch, so labels near the **Max objects visible** limit or at the edge of an obstruction can flicker between frames.

#### When the exposure changes

> **New in the next release** — not available in version 3.7.7 or earlier.

In version 3.7.7, open sky is combined over only 3 frames, and "open sky" is judged against fixed brightness levels. That absorbs one odd frame but not an exposure change, which lasts many: when auto-exposure steps, dusk fades, or you change the exposure yourself, the area treated as open sky shrinks, and the constellation lines and labels over the lost area disappear until the frames settle — or for good, if the frames stay darker.

From the next release:

- Open sky is combined over the last **15 frames** (about 7 minutes at 30-second exposures), and the last good result is kept for up to 15 frames when too few stars are detected. Labels ride through an exposure change instead of following it. A lasting change, such as a telescope parked across the view, is still picked up, after about 8 frames.
- Open sky is judged against **each frame's own sky brightness**, so a darker or brighter frame of the same sky gives the same result.
- The combined result is **never thrown away** for lack of stars. After 15 frames without a usable view it is set aside and the [equipment map](#equipment-avoidance) takes over; the first good frame afterwards starts afresh rather than being outvoted by old frames. In 3.7.7, a long run of starless frames wiped the result and labels were placed from the raw image brightness instead.
- Frames with only **3 to 9 detected stars** still count: they mark the sky around their stars as open and say nothing about the rest. In 3.7.7 such frames were ignored entirely.
- The **Moon's glare** is left out of the count, so the sky the Moon washes out is not treated as an obstruction.
- Label memory is cleared when capture starts and whenever the calibration changes, and a frame that is re-processed after a settings change is not counted twice.

On a recorded sequence with a simulated exposure ramp, this cut label changes from 62 to 18 over 40 frames, most of the remainder being real changes in the scene.

---

## Compass

The All-Sky page does not draw a compass. To show N/E/S/W on your image, add a **Compass** overlay on the [Overlay Settings](Overlay-Settings) tab. Its **Mirror E/W** option swaps East and West for mirror-flipped views, which rotation alone cannot fix.

---

## Troubleshooting

| Symptom | Likely cause | What to do |
|---------|--------------|------------|
| Calibration never converges, or fails with "not enough stars detected" or "couldn't match star patterns" | Wrong time, latitude, or longitude; cloud; obstructed lens | **Check the time, latitude, and longitude first** — this is the most common cause. Make sure the Windows clock is correct and the coordinates on the Settings tab are right, including the sign (west longitude and south latitude are negative). Then retry on a clearer night, or use Guided Calibration. |
| "Warning: lat/lon not configured. Set in Output > Weather Settings." or "Set latitude/longitude in Output > Weather Settings first." | Coordinates are blank or zero | The message names an old location: the coordinates are in the **Weather API** card on the [Settings](Settings) tab. Enter **Latitude** and **Longitude** there. |
| "No image available — start capture first." | No frame has been processed yet | Start capture, or wait for the watcher to process a frame. |
| "Calibration rejected — it disagrees with the measured celestial-pole position" | The fit found the wrong orientation | Let capture run longer and retry, or use Guided Calibration. |
| Overlay used to be right but is now offset or rotated everywhere | The camera was moved | Reset the calibration and recalibrate. |
| Overlay is badly wrong and does not improve | A bad saved calibration is holding back refinement | Click **Reset Calibration…**, then run Guided Calibration. |
| Overlay does not appear at all | Sun above -6 degrees, roof reported closed, no calibration, or image cropped | Check the quality badge, the time of day, and the ML roof status. |
| Labels sit on a telescope, the mount or the pier | The equipment map has not learned that spot yet — a first session, a rig that was just rearranged, or a map that was reset | Let capture run on a clear night; the map learns the equipment within an hour or so and forgets a moved scope over about two nights. If a scope was moved, **Reset Equipment Map…** speeds this up. Not available in 3.7.7 or earlier, where labels follow the stars detected on each frame only. |
| Labels for a whole region vanish during cloud or moonlight and come back later | The recent frames lost sight of the stars there | From the next release the equipment map holds the labels in place through such spells. In 3.7.7 and earlier this is expected. |
| Few stars detected and the image looks dark | Auto-exposure is at maximum exposure and still below target | The [Logs](Logs) show a warning that auto-exposure is pinned at max exposure. Lower the target brightness or raise gain — see [Auto-Exposure](Auto-Exposure). |
| Guided solve fails with a large error on one star | That star was misidentified or mis-clicked | It is marked in red and selected in the dialog, with your other stars kept: remove or re-identify it and solve again. Identify 6 or more stars so the solver can recover automatically. |
| Guided Calibration shows **Check your stars** | The stars identified so far do not agree with each other or with the frame | Re-check the most recent star first. The warning clears as soon as the identifications agree. |
| Badge is amber and reads **unconfirmed** or **check alignment** | Automatic refinements keep being rejected and recent frames could not confirm the saved calibration | See [When the badge turns amber](#when-the-badge-turns-amber). Often just cloud; if the overlay is visibly off on a clear night, run Guided Calibration. |

---

## Tips

- **Set your coordinates and clock first.** Nearly every calibration problem comes down to time or location.
- **Let it run.** On a clear night, automatic calibration improves from Preliminary towards Good or Excellent as frames accumulate over an hour or more.
- **Use Guided Calibration** on obstructed or tilted installs, or right after moving the camera. Spread your stars across the whole sky rather than clustering them — and start with three you are sure of, so the suggestions can label the rest.
- **Use Max objects visible to control clutter.** The default of 15 works well for most setups.
- **Enable NGC cautiously.** Start with a low maximum magnitude (6 or 7) and raise it only if you want a denser overlay.
- **Burn in only where you need labels.** The burn-in options are per destination, so you can, for example, label the web image while keeping saved files and timelapses clean.
