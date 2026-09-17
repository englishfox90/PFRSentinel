# Overlay Settings

The Overlay Settings tab is an editor for the text, image and compass overlays drawn onto each processed frame. Overlays are part of the saved image and of the image served to the web server, Image Library and Discord. If you crop your outputs with **Output Framing** on the [Image Processing](Image-Processing#output-framing) tab, overlays are placed after the crop, so anchors and offsets are measured from the edges of the cropped image. Timelapse videos include them only if that option is turned on in the [Timelapse](Timelapse) tab.

---

## Layout

The panel has two columns:

- **Left**: **Overlay List** and **Preview**
- **Right**: the editor for the selected overlay

---

## Overlay List

A table of all configured overlays with three columns: **Name**, **Type**, and **Summary** (the first 35 characters of the text, or the image file name).

| Button | Description |
|--------|-------------|
| **Add** | Opens a menu: **Add Text Overlay**, **Add Image Overlay** or **Add Compass Rose**. |
| **Duplicate** | Copies the selected overlay and adds " Copy" to its name. |
| **Delete** | Removes the selected overlay. |

Overlays are drawn in list order, so overlays lower in the list are drawn on top of those above them.

---

## Preview

The **Preview** card draws the selected overlay on a synthetic starry background, updating as you edit. Only the selected overlay is shown. Tokens are filled with fixed sample values (for example `{CAMERA}` shows `ASI676MC`), not live data.

The preview is scaled to fit the card, so sizes and offsets are approximate. To check the real result, click **Apply Changes**: if a frame has already been captured, it is reprocessed with the new overlays and shown in [Live Monitoring](Live-Monitoring).

---

## Editor

### Common Settings

| Setting | Range | Default | Description |
|---------|-------|---------|-------------|
| **Name** | Free text | Text *n* / Image *n* / Compass Rose | Display name shown in the list. |
| **Type** | Text / Image / Compass | Type chosen in **Add** | Switches the editor between overlay types. |
| **Anchor** | Top-Left, Top-Center, Top-Right, Bottom-Left, Bottom-Center, Bottom-Right | Bottom-Left (text), Bottom-Right (image, compass) | The corner or edge the overlay is positioned from. See [Known Limitations](#known-limitations) for the centre anchors. |
| **Offset X** | -2000 to 2000 px | 15 (compass: 20) | Horizontal distance in from the anchor edge. |
| **Offset Y** | -2000 to 2000 px | 15 (compass: 20) | Vertical distance in from the anchor edge. |

### Saving Changes

| Button | Description |
|--------|-------------|
| **Apply Changes** | Saves the overlay and reprocesses the last captured frame so you can see it. |
| **Reset** | Reloads the selected overlay into the editor. |

**Add**, **Duplicate** and **Delete** save immediately. Edits made in the editor are saved when you click **Apply Changes**.

---

## Text Overlays

Text overlays render one or more lines of text. Tokens in curly braces, such as `{EXPOSURE}`, are replaced with live values for every frame. See [Overlay Tokens](Overlay-Tokens) for the full list.

A new text overlay starts with `{CAMERA}` and `{EXPOSURE}` on two lines.

| Setting | Range | Default | Description |
|---------|-------|---------|-------------|
| **Tokens** | Dropdown + **Insert** | — | Pick a token and click **Insert** to add it at the cursor. Tokens are grouped by category. You can also type any token directly. |
| Text box | Multi-line | `{CAMERA}` / `{EXPOSURE}` | The text to render. |
| **Font Size** | 8–200 px | 24 | Text height in pixels on the output image. |
| **Color** | white, black, lightgray, darkgray, red, green, blue, cyan, magenta, yellow, orange, purple, pink, lime | white | Text colour. |
| **Style** | normal / bold / italic | normal | Text style. |
| **Alignment** | left / center / right | left | Alignment of lines within a multi-line block. |
| **Background** | Toggle | Off | Draw a rectangle behind the text. |
| **BG Color** | black, white, darkgray, lightgray | black | Colour of the background rectangle. Shown only when **Background** is on. |

The **Tokens** dropdown does not list the weather tokens: the Weather group is hidden behind a setting the app never turns on. Type weather tokens such as `{WEATHER_TEMP}` or `{WEATHER_CONDITION}` straight into the text box instead. They work whenever [Weather](Weather-Setup) is configured with an API key and a location. See [Overlay Tokens](Overlay-Tokens#weather-tokens) for the list.

---

## Image Overlays

Image overlays place an image file, such as a logo or watermark, onto each frame.

| Setting | Range | Default | Description |
|---------|-------|---------|-------------|
| **File** | — | — | The image to overlay (PNG, JPG, JPEG, GIF or BMP), chosen with **Browse**. PNG transparency is preserved. |
| **Width** | 10–2000 px | 100 | Overlay width. |
| **Height** | 10–2000 px | 100 | Overlay height. |
| **Lock Aspect** | Toggle | On | Keeps the image's proportions. Changing one dimension updates the other, and the image is fitted inside the width and height box. |
| **Opacity** | 0–100% | 100% | Transparency of the overlay. |

If you replace the image file on disk with a new version, the next frame picks up the change automatically.

---

## Compass Overlays

Compass overlays draw an 8-point compass rose with N, E, S and W labels.

| Setting | Range | Default | Description |
|---------|-------|---------|-------------|
| **Rotation** | 0–359° | 0 | Rotates the rose clockwise to line North up with your image orientation. |
| **Size** | 40–200 px | 80 | Diameter of the compass rose. |
| **Mirror E/W** | Toggle | Off | Swaps East and West. |

Use **Rotation** to point N the right way. If East and West then sit on the wrong sides, turn on **Mirror E/W**. This happens with mirror-flipped views, such as a camera looking via a mirror or software that writes a flipped frame, and rotation alone can't fix it. The two settings work together: rotation still turns clockwise when mirroring is on.

Position is set with the shared **Anchor**, **Offset X** and **Offset Y** fields.

---

## Known Limitations

The preview can differ from the saved image in a few cases:

- **Top-Center** and **Bottom-Center** anchors are centred in the preview, but on the output image the overlay is placed at the top-left corner. Use a left or right anchor with an offset instead.
- The text **Background** rectangle is shown in the preview but is not drawn on the output image.
- The text colours lightgray, darkgray, orange, purple, pink and lime render as white on the output image. white, black, red, green, blue, cyan, magenta and yellow render correctly.
- Text **Style** (bold or italic) only affects the preview; the output image always uses the regular font.

---

## Notes

- Text overlays support multiple lines, with **Alignment** applied per line.
- A token with no value available (for example a weather token before weather is configured) is shown as `?`.
- For astronomical labels (stars, constellations, planets) see [All-Sky Overlay](All-Sky-Overlay).
