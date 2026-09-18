# Auto Exposure

Auto Exposure adjusts the camera's exposure time to hold a target brightness as the sky changes through the night. It has three parts: a quick calibration when capture starts, small adjustments on every frame after that, and a fresh calibration if the scene changes suddenly.

Auto Exposure only works in **ZWO Camera** capture mode, and it only changes exposure time. Gain always stays at the value you set on the [Capture Settings](Capture-Settings) tab. Auto Exposure is on by default for new installs.

---

## Settings

These settings are in the **Auto Exposure** card on the [Capture Settings](Capture-Settings) tab.

| Setting | Range | Default | Description |
|---------|-------|---------|-------------|
| Enable Auto Exposure | Toggle | On | Turns Auto Exposure on or off. |
| Target Brightness | 20–200 | 100 | Target brightness on a 0–255 scale. The slider hint reads "20=dark, 200=bright". |
| Max Exposure | 0.1–3600 s | 30 s | Longest exposure Auto Exposure may use. |

Target Brightness and Max Exposure are saved per camera. The **Exposure** value in the Exposure Settings card is where calibration starts.

---

## How Brightness Is Measured

Brightness is the **75th percentile** of the frame's pixel values: the level that three-quarters of pixels are at or below. On a star field that is mostly dark sky with a few bright stars, this is steadier than a plain average, which a handful of hot pixels or bright stars can pull around.

In RAW16 mode, values are scaled to the same 0–255 range before measuring, so Target Brightness means the same thing in both modes.

---

## Calibration When Capture Starts

When capture starts with Auto Exposure on, a calibration runs before normal frames are published. The status indicator shows **Calibrating camera** while it runs.

1. A test frame is taken at the current exposure and its brightness measured.
2. If brightness is within **±20%** of the target, calibration is done.
3. Otherwise a new exposure is calculated and the next test frame is taken.

Calibration takes at most **15** test frames. If it hasn't converged by then, capture continues with the last exposure and per-frame adjustment takes over.

**Choosing the next exposure:**

- Once calibration has test frames on both sides of the target (one too dark, one too bright), it interpolates between them to estimate the exposure that hits the target.
- Until then, it scales the exposure in proportion to how far off the frame is, capped per step so it can't overshoot badly. A very dark frame can raise exposure by up to 5x in one step; a very bright one can cut it by up to half.
- If brightness barely moves between test frames (for example, the scene is too dark to register), steps get bigger: 2.5x after two stalled frames, 4x after three.

**Reaching Max Exposure:** if the target needs more exposure than **Max Exposure** allows, calibration stops at Max Exposure and accepts a darker image. There is one exception: if the frames are completely black (a closed roof, or a lens cap), it settles on **5 s** instead (or Max Exposure, if that's shorter). This avoids long, useless exposures, and the per-frame adjustment brings exposure up once the scene brightens.

---

## Adjustment on Every Frame

After calibration, each captured frame is measured and exposure is nudged if needed:

| Frame brightness | What happens |
|------------------|--------------|
| Within ±20% of target (80–120 at the default target of 100) | No change. |
| Outside ±20% but within ±50% of target | Gentle step: exposure ×1.3 if too dark, ×0.7 if too bright. |
| More than 50% away from target | Larger step in proportion to the error, up to 5x longer or down to 0.2x shorter. |

Exposure never goes above **Max Exposure**, and never below 32 microseconds.

### Clipping Prevention

If a frame is too dark but more than 5% of its pixels are above **245** (nearly saturated — for example, the Moon or a bright light in view), exposure is **not** increased. This stops a dark sky from pushing bright areas into white-out. It doesn't force exposure down on its own; a frame that is too bright overall is handled by the normal adjustment above.

### Stuck at Max Exposure

If exposure is already at Max Exposure and frames are still more than 20% below target, no further exposure increase is possible. PFR Sentinel writes a warning to the [Logs](Logs) tab suggesting you lower **Target Brightness** or raise **Gain**. The warning repeats at most every 15 minutes while the condition lasts.

---

## Scene Changes and Recalibration

Auto Exposure keeps a baseline brightness: the first frame after calibration, then slowly updated so it follows gradual changes such as dusk and dawn. A full calibration runs again when any of these happens:

- Brightness more than doubles, or falls below half, of the baseline (for example, lights switched on or off, or thick cloud).
- More than 50% of pixels are clipped.
- The scene has brightened by 50% or more from a dark baseline while exposure is at or near Max Exposure.

The frame that triggered the recalibration is discarded rather than published, so a badly exposed frame doesn't reach your outputs or timelapse.

To stop repeated light changes causing constant recalibration, recalibrations are at least **60 seconds** apart and limited to **3 in any 10 minutes**. Outside those limits, the normal per-frame adjustment handles the change instead, and the log says so.

---

## Fixed Internal Values

These values are built in and can't be changed in the app.

| Parameter | Value |
|-----------|-------|
| Brightness measurement | 75th percentile |
| Acceptable range | ±20% of target |
| Calibration test frames | Up to 15 |
| Dark-scene fallback exposure | 5 s (or Max Exposure if shorter) |
| Clipping threshold | 245 (on a 0–255 scale) |
| Clipping prevention trigger | More than 5% of pixels clipped |
| Recalibration triggers | 2x brighter, 0.5x darker, or more than 50% clipped |
| Recalibration limits | 60 s apart, at most 3 per 10 minutes |

---

## Interaction With Other Features

- **Live Monitoring histogram** — while Auto Exposure is on, the histogram shows a yellow dashed **Target** line at your target brightness and a red dashed **Clip** line at 245. See [Live Monitoring](Live-Monitoring).
- **Changing settings while capturing** — new Target Brightness and Max Exposure values apply from the next frame without restarting capture or recalibrating. If you lower Max Exposure below the exposure currently in use, the very next frame uses the new limit.
- **Scheduled Capture** — calibration runs when you start capture. When **Only within time window** mode reconnects the camera at the start of a window, no calibration runs. The camera resumes at the exposure it was using when capture paused, which may be hours out of date. The baseline resets to the first new frame, and the per-frame adjustment (up to 5x per step) pulls exposure back to the target over the next few frames. A later sudden change in the scene still triggers a full recalibration. See [Capture Settings](Capture-Settings).
- **Timelapse** — exposure changes show up in the video as gradual brightness shifts. Frames discarded during a recalibration never reach the video. See [Timelapse](Timelapse).
- **Image processing** — Auto Exposure controls how much light the sensor collects; [Auto Stretch](Auto-Stretch) then controls how bright the processed image looks. If saved images look wrong but the raw exposure is fine, adjust the stretch rather than the target.

---

## Tips

- The default **Target Brightness** is 100. Lower values keep star colour and limit sky-glow washout.
- **Interval** is the wait after each frame finishes, not a fixed cadence. A long exposure doesn't have to fit inside the interval, but the time between frames will be roughly the exposure, plus readout, plus the interval. Image processing runs separately and doesn't add to it.
- If the log keeps warning that exposure is pinned at max, the sky is simply too dark for the target at your current gain. Raise **Gain**, allow a longer **Max Exposure**, or accept a lower **Target Brightness**.
- If exposure keeps jumping at the same time every night, check for lights or reflections entering the frame. Sudden changes trigger recalibration.
