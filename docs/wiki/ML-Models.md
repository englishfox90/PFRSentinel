# ML Models

PFR Sentinel includes two machine learning models that analyse pier camera frames as they are captured: a **Roof Classifier** that detects whether the observatory roof is open or closed, and a **Sky Classifier** that assesses sky conditions while the roof is open. Both run locally on your PC; no cloud service or internet connection is involved.

ML analysis is a Beta feature and is off by default. Turn it on with **Enable ML Analysis** in the **ML Models (Beta)** card on the [Image Processing](Image-Processing) tab. It is part of the released application, not a developer-only feature.

---

## Models

### Roof Classifier

Decides whether the roof is open or closed from the pier camera image.

| Property | Value |
|----------|-------|
| Input | Frame reduced to 128x128 greyscale, plus 4 context values |
| Output | Open or Closed, with a confidence percentage |
| Accuracy | 99.8% validation accuracy (June 27, 2026 model) |

Alongside the image, the model uses:

| Context value | Description |
|---------------|-------------|
| Corner-to-centre ratio | Brightness of the frame corners compared with the centre. A closed roof tends to be evenly dark; an open sky has a gradient. |
| Median brightness | Overall frame brightness, scaled correctly for 8-bit and 16-bit frames. |
| Night flag | Whether the PC clock is between 20:00 and 06:00. |
| Hour | Hour of day from the PC clock. |

### Sky Classifier

Assesses the sky. It only runs when the roof classifier reports Open, because with the roof closed the camera can't see the sky.

| Property | Value |
|----------|-------|
| Input | Frame reduced to 384x384 greyscale, plus 6 context values |
| Output | Sky condition, whether stars are visible, star density, whether the moon is visible |
| Accuracy (sky condition) | 90.4% overall on the held-out test set: Clear 92.7% (204 of 220), Partly Cloudy 15 of 23, Overcast 6 of 6 (June 27, 2026 model) |

| Output | Values |
|--------|--------|
| Sky condition | Clear, Partly Cloudy, Overcast |
| Stars visible | Yes / No |
| Star density | 0.0 (none) to 1.0 (dense star field) |
| Moon visible | Yes / No |

The sky classifier uses the same four context values as the roof classifier, plus:

| Context value | Description |
|---------------|-------------|
| Moon illumination | Current moon phase as a percentage |
| Moon is up | Whether the moon is above the horizon at your location |

"Moon is up" needs latitude and longitude in the **Weather API** card on the [Settings](Settings) tab (see [Weather Setup](Weather-Setup)). Without coordinates the model is always told the moon is down.

Non-square frames are centre-cropped to a square before they are reduced, so the image isn't squashed.

---

## Accuracy and Limitations

- **Trained on one observatory.** Both models were trained on frames from a single pier camera (a ZWO ASI676MC with square frames). Other cameras, lenses, fields of view and enclosures may give noticeably different results. Watch the predictions for a few nights before relying on them.
- **Overexposure hurts roof detection.** In testing, the roof classifier's remaining errors were overexposed frames, typically bright daytime frames with the roof open. Bright moonlight on its own was not a significant cause of errors.
- **Moonlit clear nights can read as Partly Cloudy.** Moon glow and twilight wash out the sky in a way that looks like thin cloud. Much of the training data carries the same ambiguity, so treat Partly Cloudy on a bright-moon night with caution.
- **Partly Cloudy is the weakest class.** It was right on 15 of 23 test frames (about 65%), limited by how few partly cloudy frames exist in the training data. Overcast has only a handful of test frames, so its score is not a reliable estimate.
- **Time features use the PC clock.** Make sure the observatory PC's time and time zone are correct.
- **The models see the raw frame.** Predictions are made before stretching, brightness and overlays, so image processing settings don't affect them. Very underexposed or overexposed raw frames do.

---

## Using the Results

### Overlay Tokens

With ML enabled, these tokens can be used in text overlays (see [Overlay Tokens](Overlay-Tokens)):

| Token | Example Value |
|-------|---------------|
| `{ROOF_STATUS}` | Open (95%) |
| `{SKY_CONDITION}` | Clear (87%) |
| `{STARS_VISIBLE}` | Yes |
| `{STAR_DENSITY}` | High (0.85) |

While the roof is closed, the three sky tokens show `N/A`. **Star density** is labelled High above 0.6, Medium above 0.3, and Low otherwise.

### Live Monitoring

The status strip in [Live Monitoring](Live-Monitoring) has **Roof** and **Sky** tiles. Roof shows Open or Closed, and Sky shows the sky condition, each colour-coded. While the roof is closed, the Sky and Seeing tiles show "Roof closed". Both tiles show "Not configured" when ML is off.

#### Too Much Static

> **New in the next release** — not available in version 3.7.7 or earlier.

A frame that is almost entirely sensor noise gives the models nothing to judge. This is typical of a closed roof at night with the exposure at its maximum. On such a frame the roof model can report a low-confidence **Open**, and the sky model a confident **Clear**, when neither is true.

PFR Sentinel measures each frame for this. When a frame is only noise, the Roof tile turns amber and reads **Open · unreliable** or **Closed · unreliable**, the Sky tile reads **Too much static**, and hovering either tile explains why. The log records when it starts and stops.

This is a warning only. The roof reading, the overlay tokens, roof change notifications and the ASCOM safety file all still use whatever the models reported.

### Skipping Sky Features While the Roof Is Closed

With **Skip Sky Features When Roof Closed** on (the default), star detection and the [All-Sky Overlay](All-Sky-Overlay), including its background calibration, pause while the roof reads Closed. If your camera has no roof, for example an open-air all-sky camera, turn this off so a mistaken Closed reading can't switch those features off.

The [Meteor Detection](Meteor-Detection) roof check is separate and does not follow this setting. With ML analysis on, meteor detection pauses whenever the roof is not reported Open, which includes Closed and uncertain (`N/A`) readings, and also when only the sky model loaded. See [Meteor Detection](Meteor-Detection#roof-gate) for details.

### Roof Change Notifications

[Discord](Discord-Integration) and [Hermes](Hermes-Notifications) can post a message when the roof opens or closes. A change is only reported after two consecutive frames agree, so a single misread frame doesn't trigger a notification.

### Timelapse Roof-Open Mode

The [Timelapse](Timelapse) tab has a **Roof Open (Beta)** recording window that records while the roof classifier reports the roof as open. ML analysis must be enabled for it to work.

### Image Library

The [Image Library](Image-Library) stores the roof and sky results with each frame and uses them to colour its night-by-night condition band.

### ASCOM Safety File (NINA Integration)

PFR Sentinel can write the roof status to a text file watched by NINA's **GenericFile** safety monitor, so NINA can react when the roof closes. The file is written in ZWO Camera mode only.

| Setting | Default | Description |
|---------|---------|-------------|
| **ASCOM Safety File** | Off | Write roof status to the file. |
| **File Path** | `%LOCALAPPDATA%\PFRSentinel\RoofStatusFile.txt` | The file NINA monitors. Type a path or use **Browse**. |

File format:

```
Roof Status: OPEN
Confidence: 95%
Sky Condition: Clear (87%)
Updated: 2026-09-17 22:45:30
```

The **Confidence** line is left out when there is no roof reading, and the **Sky Condition** line when there is no sky result.

NINA GenericFile monitor configuration:
- Preamble: `Roof Status:`
- Safe trigger: `OPEN`
- Unsafe trigger: `CLOSED`

The file is designed to fail safe:

- **Only a confident Open is safe.** `OPEN` is written only when the roof reads Open with at least 70% confidence. A Closed reading, a low-confidence reading, or a frame where ML is switched off, the models aren't loaded or the prediction failed all count as unsafe. Losing the ML result therefore moves the file to `CLOSED` rather than leaving it stuck on a stale `OPEN`.
- **Two frames to change state.** The file only switches between `OPEN` and `CLOSED` after two consecutive frames agree, so one noisy frame can't flip it.
- **Starts unsafe.** After PFR Sentinel starts, the first write is always `CLOSED`, which also clears any `OPEN` left over from a previous run. It then needs two consecutive confident Open frames to switch to `OPEN`; if the very first frame is a confident Open, it counts as the first of the two.
- **Kept fresh.** While the status is steady, the file is rewritten every 5 minutes so NINA's maximum-file-age check doesn't treat a healthy session as stale. Set NINA's maximum age comfortably above both 5 minutes and your capture interval.
- **Write failures are reported.** If the file can't be written (bad path, disk problem), an error is logged and an "ASCOM Safety Write Failed" error notification is raised, which reaches Discord or Hermes if you have error notifications turned on there.

Because the file only changes when frames are processed, it can only be as current as your capture interval.

---

## Runtime

The models ship with the application and run with ONNX Runtime. They load when you turn on **Enable ML Analysis**, or at startup if it is already on. The status line in the ML card ("Status: ...") shows the result:

| Status | Meaning |
|--------|---------|
| ✓ Both models loaded | Roof and sky models ready |
| ✓ Roof model only | Sky model failed to load; roof results still available |
| ✓ Sky model only | Roof model failed to load. The sky model only runs when the roof reads Open, so no sky results are produced either |
| ✗ No models available | Neither model loaded; the reason is shown in brackets |
| ✗ Error: ... | Something went wrong while checking the models; the start of the error is shown |
| Disabled | ML analysis is off |

In ZWO Camera mode, ML results drive everything on this page. In Directory Watch mode the models only provide the [overlay tokens](#overlay-tokens): the Live Monitoring tiles, roof change notifications, the ASCOM safety file, the timelapse and meteor detection roof checks, and data contribution are ZWO Camera mode features.

Development builds can additionally save per-frame calibration data (FITS and JSON files with the model predictions) for retraining. That is a developer tool and is not part of the released application.

---

## Training Data

As of the June 27, 2026 models, the roof classifier was trained on about 4,000 labelled pier camera frames. The sky classifier was trained on the roof-open subset of those frames, which is dominated by clear nights. Labels were reviewed to remove moon-glow and closed-roof mislabels before retraining.

Ground truth came from:

- The safety monitor state reported by NINA's Advanced API plugin
- OpenWeatherMap cloud cover and conditions
- Moon phase and position
- Manual labelling of sky condition, star visibility and moon visibility

---

## Community Data Contribution

An opt-in programme lets you contribute anonymous training data so future models work better across different cameras and observatories.

### How It Works

1. Turn on **Enable Data Contribution** in the **Community Data Contribution** card on the [Image Processing](Image-Processing) tab.
2. While capturing in ZWO Camera mode, PFR Sentinel saves at most one sample every 30 minutes.
3. Samples are stored locally in `%LOCALAPPDATA%\PFRSentinel\ml_contribution\`.
4. When you're ready, click **Export for Upload** to create a ZIP, then **Open Upload Form** and submit it through the Google Form.
5. After a successful upload, click **Clear** to delete the local samples.

Collection stops automatically at 500 samples (about 130 MB). This works whether or not **Enable ML Analysis** is on.

### What Each Sample Contains (about 265 KB)

- **FITS image**: 256x256 luminance-only, 32-bit float (about 260 KB). The caption in the app says about 80 KB, which is out of date.
- **Calibration JSON**: metadata and statistics (about 5 KB)

### Calibration Data Collected

| Field | Description |
|-------|-------------|
| Camera name | Model identifier, for example ZWO ASI676MC |
| Exposure, gain, bit depth, Bayer pattern | Capture parameters |
| Brightness statistics | Median, mean, percentiles and dynamic range |
| Spatial analysis | Corner-to-centre brightness ratio |
| Time context | Time of day, and that day's sun and moon rise and set times |
| Moon context | Phase, illumination and whether it is up |
| Roof state | The safety monitor state from NINA's Advanced API plugin, if NINA is running with the plugin available |
| Weather conditions | From OpenWeatherMap, if configured |

### What Is Removed

Before a sample is saved, these are stripped out:

- Coordinates and location names
- Weather city and location
- File paths
- All-sky camera addresses and file names

The sun and moon rise and set times are kept. Because they depend on where you are, they give a rough indication of your location.
