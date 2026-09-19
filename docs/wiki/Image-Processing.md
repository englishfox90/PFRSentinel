# Image Processing

The Image Processing tab controls how each frame is enhanced before overlays are drawn: resizing, output framing, brightness and saturation, auto-stretch, and the ML scene models. Changes save immediately. If a frame has already been captured, it is reprocessed with the new settings about half a second after you stop adjusting, so you can see the effect without waiting for the next exposure.

A reprocessed frame is saved and sent to the web server and other outputs like a normal frame, but it is not added to the [Timelapse](Timelapse) or to [Meteor Detection](Meteor-Detection), so adjusting settings does not put duplicate frames into either. In version 3.7.6 and earlier, reprocessed frames were also passed to the timelapse and meteor detection.

---

## Processing Order

In ZWO Camera mode each frame is processed in this order:

1. **Image Resize**
2. **Auto Stretch (MTF)**
3. **Auto Brightness** (with the **Brightness** trim)
4. **Saturation**
5. **Timestamp Overlay**
6. **Output Framing** crop (if turned on)
7. Your text, image and compass overlays from the [Overlay Settings](Overlay-Settings) tab

The ML models and star detection analyse the raw captured frame, not the processed one, so stretch and brightness settings do not change their results. All-sky calibration, meteor detection and the ML models all work on the full frame, before the Output Framing crop.

In Directory Watch mode the order differs: the stretch runs before the resize, the Output Framing crop follows the resize, **Auto Brightness** and **Saturation** are applied after the overlays have been drawn, and the **Timestamp Overlay** is not applied.

---

## Image Resize

| Setting | Range | Default | Description |
|---------|-------|---------|-------------|
| **Scale** | 10–100% | 74% | Scales the output image. Reduces file size and speeds up everything downstream (overlays, web, Discord, timelapse). 100% keeps full sensor resolution. |

---

## Adjustments

| Setting | Range | Default | Description |
|---------|-------|---------|-------------|
| **Auto Brightness** | Toggle | Off | Scales brightness so the average pixel value moves towards mid-grey. The automatic factor is limited to between 0.5x and 4.0x. |
| **Brightness** | 0.5x–2.0x | 1.0x | A trim multiplied onto the Auto Brightness factor. It only has an effect while **Auto Brightness** is on; with Auto Brightness off, this slider does nothing. |
| **Saturation** | 0.0x–2.0x | 0.98x | Colour saturation multiplier. 0 produces greyscale, 1.0 is unchanged, 2.0 is heavily saturated. The label rounds to one decimal place, so the default shows as 1.0x. |

---

## Timestamp Overlay

| Setting | Default | Description |
|---------|---------|-------------|
| **Show Timestamp** | Off | Draws the current date and time (`YYYY-MM-DD HH:MM:SS`, PC local time) in white near the top-right corner. This is a fixed, simple stamp. For control over position, size and colour, use a text overlay containing the `{DATETIME}` token instead (see [Overlay Tokens](Overlay-Tokens)). ZWO Camera mode only. |

---

## Output Framing

> **New in the next release** — not available in version 3.7.6 or earlier.

Crops what PFR Sentinel sends out to a box you draw over the last frame. It is designed for all-sky cameras, where the round sky image rarely fills the sensor: you can keep the sky and drop the black corners from your saved images, web frame and timelapse.

Only the outputs are cropped. The camera still captures the full sensor, and the analysis features keep working on the whole frame, because the dark corners and the lens centre are what they measure against.

| Cropped to the box | Keeps the full frame |
|--------------------|----------------------|
| Saved image files | [Auto-Exposure](Auto-Exposure) |
| Web server image (`/latest`) | [Auto Stretch](Auto-Stretch) |
| [Image Library](Image-Library) | [ML Models](ML-Models) and the ASCOM safety file |
| [Discord](Discord-Integration) and [Hermes](Hermes-Notifications) image posts | [All-Sky Overlay](All-Sky-Overlay) calibration |
| [Timelapse](Timelapse) video | [Meteor Detection](Meteor-Detection) |
| The live preview | The crop editor thumbnail on this card |

Text, image and compass overlays are placed after the crop, so they anchor to the edges of the cropped image. The **Timestamp Overlay** is drawn before the crop, near the top-right corner of the full frame; if your box leaves that corner out, the timestamp is cut off, so use a text overlay with `{DATETIME}` instead. If you burn the [All-Sky Overlay](All-Sky-Overlay) into an output, its labels are shifted to match the crop and still line up with the sky.

The crop applies in both ZWO Camera and Directory Watch modes.

### Settings

| Setting | Default | Description |
|---------|---------|-------------|
| **Crop outputs to a region** | Off | Turns the crop on. While it is off the box is still shown (dimmed and dashed) and can be edited, but outputs use the full frame. |
| Crop editor | 80% of the frame, centred | Shows the last captured frame with the box drawn over it; the area outside the box is shaded. Drag inside the box to move it, and drag a corner handle to resize it. With **Keep square** off, handles on the edges let you change the width and height separately. The size and position are shown under the box. Before the first frame arrives it reads "Start capture to see a frame here". |
| **Offset** (**X**, **Y**) and **Size** (**W**, **H**) | | The offset is the box's left and top edge measured from the top-left corner of the frame; the size is its width and height. Values are pixels of the full camera frame, whatever the **Scale** setting, so the same numbers keep the same part of the sky. Width and height are rounded down to even numbers, and the box is at least 64 pixels on each side. |
| **Keep square** | On | Locks the box to a square, the natural shape for an all-sky circle. When you turn it on, the shorter side wins. |

| Button | What it does |
|--------|--------------|
| **Fit to sky** | Finds the sky circle in the last frame and fits a square just around it, with a small margin. It also turns the crop on and saves. The button reads **Measuring…** while it works. If the circle can't be found, the status line reads "Could not find the sky circle in the last frame. Try again on a frame with visible sky, or drag the box by hand." |
| **Centre** | Moves the box to the middle of the frame, keeping its size. |
| **Full frame** | Resets the box to the whole frame. |

The status line under the buttons summarises the result, for example "Outputs: 2880×2880 at (80, 320) of the 3552 × 3552 frame.", or "Crop off — outputs use the full 3552 × 3552 frame."

If frames change size later (for example you switch to a camera with a different sensor, or Directory Watch files arrive at a different resolution), the box is scaled in proportion so it keeps the same part of the image.

---

## Auto Stretch (MTF)

A midtone transfer function that lifts faint detail out of dark frames. The card is collapsible. For how each step works and tuning advice, see [Auto Stretch](Auto-Stretch).

| Setting | Range | Default | Description |
|---------|-------|---------|-------------|
| **Enable Auto Stretch** | Toggle | On | Turns the stretch on or off. |
| **Target Median** | 0.10–0.50 | 0.15 | Target brightness of the median pixel after the stretch. Higher is brighter. |
| **Linked Channels** | Toggle | Off | On: the same stretch is applied to R, G and B, which keeps colour balance stable between frames. Off: each channel is stretched on its own. |
| **Preserve Blacks** | Toggle | On | Keeps true blacks dark instead of lifting them to grey. |
| **Dark Scene Color Fix** | Toggle | On | Evens out the red, green and blue levels before stretching very dark frames. Fixes the purple or magenta cast common in dark images. |
| **Dark Threshold** | 0.01–0.15 | 0.12 | Frames whose median brightness is below this value get the Dark Scene Color Fix. |
| **Shadow Aggressiveness** | 1.5–4.0 | 1.8 | How hard the shadows are clipped. 1.5 = aggressive (darker background, less noise), 4.0 = gentle (more faint detail, more noise). |
| **Saturation Boost** | 1.0x–2.0x | 1.5x | Extra saturation applied after the stretch to restore colour. |
| **Green Removal (SCNR)** | 0–100% (0 shows as Off) | 46% | Removes green cast from airglow or light pollution. |

Defaults are the values a new installation starts with. Upgrading keeps whatever you had saved.

---

## ML Models (Beta)

Two on-device models classify each captured frame: a **Roof Classifier** (open or closed) and a **Sky Classifier** (sky condition, run only when the roof reads open). They run locally with no internet connection. See [ML Models](ML-Models) for accuracy and limitations.

| Setting | Default | Description |
|---------|---------|-------------|
| **Enable ML Analysis** | Off | Loads the models and runs them on every frame. The status line underneath reports what loaded: "✓ Both models loaded", "✓ Roof model only", "✓ Sky model only", or an error marked with ✗. |
| **Skip Sky Features When Roof Closed** | On | While the roof classifier reports Closed, star detection and the [All-Sky Overlay](All-Sky-Overlay) (including its background calibration) are paused. Turn this off if you have no roof, such as an open-air all-sky camera, so a mistaken "Closed" reading can't switch those features off. |
| **ASCOM Safety File** | Off | Writes the roof status to a text file for NINA's GenericFile safety monitor. See below. |
| **File Path** | `%LOCALAPPDATA%\PFRSentinel\RoofStatusFile.txt` | Where the safety file is written. Type a path or use **Browse**. |

When ML is enabled, the tokens `{ROOF_STATUS}`, `{SKY_CONDITION}`, `{STARS_VISIBLE}` and `{STAR_DENSITY}` can be used in text overlays. See [Overlay Tokens](Overlay-Tokens).

### ASCOM Safety File

Point NINA's **GenericFile** safety monitor at the file and configure it with:

- Preamble: `Roof Status:`
- Safe trigger: `OPEN`
- Unsafe trigger: `CLOSED`

The file is fail-safe. It only reports `OPEN` after two consecutive frames read the roof as open with at least 70% confidence. Everything else writes `CLOSED`: a closed reading, a low-confidence reading, or frames where the ML models are switched off or could not run. See [ML Models](ML-Models#ascom-safety-file-nina-integration) for the full behaviour and file format.

The safety file is written in ZWO Camera mode only.

---

## Community Data Contribution

An opt-in programme that collects anonymous training samples to help improve the ML models. It works independently of **Enable ML Analysis**.

| Setting | Default | Description |
|---------|---------|-------------|
| **Enable Data Contribution** | Off | Saves at most one sample every 30 minutes while capturing in ZWO Camera mode, up to 500 samples. |

### What Is Collected

- A downscaled luminance image (256x256 pixels, about 260 KB per sample, so up to about 130 MB at the 500-sample limit). The caption in the app says about 80 KB, which is out of date.
- Camera settings and image statistics
- Time and moon context (no GPS coordinates)
- Roof and weather status, if available

### Controls

| Control | Description |
|---------|-------------|
| Status line | Shows the sample count, the 500-sample limit and disk usage. Refreshes every 5 seconds. |
| **Export for Upload** | Creates a ZIP of the collected samples and opens its folder. Disabled until at least one sample exists. |
| **Open Upload Form** | Opens the Google Form for submitting the ZIP. |
| **Clear** | Deletes all collected samples after asking for confirmation. Use it after a successful upload. |

See [ML Models](ML-Models#community-data-contribution) for what each sample contains and what is removed.

---

## Developer Mode

A **Developer Mode** card (raw debug image saving and per-channel statistics logging) appears only in development builds. It is not part of the released application.
