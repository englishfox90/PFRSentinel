# Auto Stretch (MTF)

Auto Stretch applies a Midtone Transfer Function to each captured frame, lifting faint detail out of dark images with a narrow dynamic range, which is typical of night-sky frames. The settings live in the **Auto Stretch (MTF)** card on the [Image Processing](Image-Processing) tab.

In ZWO Camera mode the stretch runs after **Image Resize** and before **Auto Brightness**, **Saturation** and overlays. In Directory Watch mode it runs before the resize. In both modes it runs before the **Output Framing** crop, so the stretch is always measured on the full frame, dark corners included, even when your outputs are cropped (see [Image Processing](Image-Processing#output-framing)).

---

## Pipeline Steps

The stretch runs in a fixed order.

### 1. Input Normalisation

The image is converted to floating point in the 0.0–1.0 range, and all later steps work in that space. When the camera is running in RAW16 mode (**Use RAW16 Mode** on the [Capture Settings](Capture-Settings) tab), the stretch works on the full 16-bit data for finer tonal steps; otherwise it uses the 8-bit image. The result is always saved as 8-bit.

### 2. Brightness Check

The median brightness of the frame is measured using luminance:

```
luminance = 0.299 * R + 0.587 * G + 0.114 * B
```

If that median is already more than 0.1 above the **Target Median**, the stretch is skipped entirely and the frame passes through unchanged. This stops daytime or already-bright frames from being over-processed.

### 3. Dark Scene Channel Normalisation

For very dark frames (median below the **Dark Threshold**), the red, green and blue channel medians are pulled towards the overall luminance. This corrects the purple or magenta cast that often appears in dark exposures.

The correction is applied at 50% strength, because full equalisation over-corrects and introduces new colour casts. This step only runs when **Dark Scene Color Fix** is on and the frame is below the threshold.

### 4. Shadow Clipping (MAD)

Shadow clipping sets the black point automatically. It uses the Median Absolute Deviation (MAD), which is less affected by hot pixels and bright stars than a standard deviation:

```
shadow_clip = median - shadow_aggressiveness * MAD
```

The clip point is never allowed above 80% of the median, and the MAD has a small floor so perfectly flat frames don't break the calculation.

| Shadow Aggressiveness | Behaviour |
|-----------------------|-----------|
| 1.5 | Aggressive: clips more shadow noise, darker background, may lose faint detail |
| 1.8 (default) | Fairly firm clipping |
| 2.8 | Balanced |
| 4.0 | Gentle: keeps more shadow detail, but also more noise |

With **Linked Channels** on, one clip point (from luminance) is applied to all three channels. With it off, each channel is clipped using its own median and MAD. Per-channel clipping can make colour shift from frame to frame, which is visible in timelapses.

There is no manual black point control; the black point always comes from this calculation.

### 5. Black Preservation

When **Preserve Blacks** is on, clipping uses three zones instead of a hard cutoff:

| Zone | Range | Behaviour |
|------|-------|-----------|
| True black | Darkest 1% of pixels | Set to pure black |
| Transition | Between true black and the clip point | Faded in smoothly (smoothstep curve) |
| Normal | Above the clip point | Clipped and rescaled as normal |

This avoids the washed-out blacks of a simple clip while still lifting faint detail in the transition zone. With **Preserve Blacks** off, everything below the clip point becomes black and everything above is rescaled linearly.

### 6. Midtone Transfer Function

The MTF is the core of the stretch. It remaps pixel values with a curve that lifts midtones while protecting highlights:

```
MTF(x, m) = (m - 1) * x / ((2 * m - 1) * x - m)
```

`x` is the pixel value (0.0–1.0) and `m` is the midtone balance, calculated so that the median after clipping lands on the **Target Median**. If the clipped median is already within 0.01 of the target, this step is skipped.

| Target Median | Result |
|---------------|--------|
| 0.10 | Dark: only the brightest features stand out |
| 0.15 (default) | Restrained lift with a dark background |
| 0.25 | Brighter, more visible detail |
| 0.50 | Very bright: strong lift, can wash out colour and faint structure |

### 7. SCNR (Green Removal)

Subtractive Chromatic Noise Reduction removes green cast caused by airglow or light pollution. For each pixel:

```
corrected_G = min(G, (R + B) / 2)
```

The original and corrected green are blended by the **Green Removal (SCNR)** percentage: 0% (Off) leaves the image untouched, 100% applies the full correction. SCNR runs after the MTF, where colour casts are most visible.

### 8. Saturation Boost

Stretching dilutes colour. **Saturation Boost** applies a saturation multiplier afterwards to compensate:

| Value | Effect |
|-------|--------|
| 1.0x | No boost (colours may look muted) |
| 1.5x (default) | Moderate: restores a natural look |
| 2.0x | Strong: vivid, with a risk of oversaturation |

---

## Settings Reference

| Setting | Range | Default | Description |
|---------|-------|---------|-------------|
| **Enable Auto Stretch** | Toggle | On | Master switch for the stretch. |
| **Target Median** | 0.10–0.50 | 0.15 | Desired median brightness after the stretch. |
| **Linked Channels** | Toggle | Off | Apply the same stretch to all RGB channels. Off stretches each channel independently. |
| **Preserve Blacks** | Toggle | On | Smooth transition into black instead of a hard clip. |
| **Dark Scene Color Fix** | Toggle | On | Even out channel levels in dark frames to prevent colour casts. |
| **Dark Threshold** | 0.01–0.15 | 0.12 | Median brightness below which the dark scene fix runs. |
| **Shadow Aggressiveness** | 1.5–4.0 | 1.8 | MAD multiplier for the shadow clip point. Lower clips harder. |
| **Saturation Boost** | 1.0x–2.0x | 1.5x | Post-stretch saturation multiplier. |
| **Green Removal (SCNR)** | 0–100% | 46% | Strength of green cast removal. 0 shows as Off. |

Defaults are for new installations; an upgraded installation keeps its saved values.

---

## Tips

- Turn **Linked Channels** on for timelapses. Per-channel mode can shift colour between frames as sky conditions change.
- If dark frames look purple or magenta, make sure **Dark Scene Color Fix** is on and that **Dark Threshold** is above the frames' median brightness.
- **Shadow Aggressiveness** has the biggest effect on background noise. Lower values give a darker, cleaner background; higher values keep more faint detail.
- **SCNR** helps most under light-polluted skies where sodium or LED lighting adds a green tint. It does nothing to images with no green excess.
- A **Target Median** above 0.4 pushes most of the frame into the upper half of the histogram, making faint structure visible at the cost of contrast and colour.

### The image is too bright and won't go darker

The **Target Median** is measured across the whole frame. If most of the frame is dark foreground (the pier, telescope or enclosure), the median pixel belongs to that foreground rather than the sky. The stretch then brightens the foreground to the target and the sky ends up considerably brighter, so lowering **Target Median** has less effect than you'd expect.

Things to try:

- Fix the exposure first. If the raw frame is badly underexposed (for example auto-exposure is stuck at its maximum exposure), the stretch has to lift it a long way. Raise gain or the maximum exposure on the [Capture Settings](Capture-Settings) tab; see [Auto Exposure](Auto-Exposure).
- Lower **Shadow Aggressiveness** to clip more of the background to black.
- Turn **Enable Auto Stretch** off to compare against the unstretched frame.
