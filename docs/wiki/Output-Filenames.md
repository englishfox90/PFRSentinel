# Output Filenames

The **Filename** field in the **File Output** card on the [Output Settings](Output-Settings) tab sets the name of each saved image. By default it's `latestImage`, so every capture overwrites the same file. That suits web dashboards and other software that always reads the newest image. To build an archive of every frame instead, put one or more tokens in the name. PFR Sentinel replaces them with real values each time it saves.

---

## Tokens

| Token | Replaced with | Example |
|-------|---------------|---------|
| `{filename}` | Name of the captured frame, without its extension. In ZWO camera mode this is `capture_` followed by the capture date and time. | `capture_20260330_224530` |
| `{session}` | Today's date on this PC when the image is saved (`YYYY-MM-DD`). | `2026-03-30` |
| `{timestamp}` | Date and time on this PC when the image is saved (`YYYYMMDD_HHMMSS`). | `20260330_224530` |

- Tokens must be lowercase and typed exactly as shown. Any other text in braces stays in the filename as written.
- You can mix tokens with your own text, underscores and hyphens.
- `{timestamp}` has no spaces or colons, so it's safe in any Windows filename.

---

## Extension

Don't type an extension in the **Filename** field. PFR Sentinel always adds one to match the **Format** setting: `.jpg` for jpg and `.png` for png. If you type `latestImage.jpg`, the file is saved as `latestImage.jpg.jpg`.

**JPG Quality** (1–100, default 100) only matters when **Format** is jpg.

---

## Examples

| Filename field | Saved as | Behaviour |
|----------------|----------|-----------|
| `latestImage` | `latestImage.jpg` | The default. Overwritten on every capture. |
| `allsky_latest` | `allsky_latest.jpg` | Fixed name. Overwritten on every capture. |
| `{timestamp}` | `20260330_224530.jpg` | A new file for every capture. |
| `{session}_{timestamp}` | `2026-03-30_20260330_224530.jpg` | A new file for every capture, grouped by date when sorted. |
| `{filename}_processed` | `capture_20260330_224530_processed.jpg` | A new file for every capture. |
| `pfr_{session}` | `pfr_2026-03-30.jpg` | One file per day. Overwritten on every capture that day. |

---

## Things to Know

- **`{session}` changes at midnight.** A night that runs past midnight gets two `{session}` values. Use `{timestamp}` when you need names that keep sorting correctly across midnight.
- **Unique filenames fill the disk.** Every capture adds a file when the name includes `{timestamp}` or `{filename}`. **Storage Cleanup** doesn't currently delete these files (see [Output Settings](Output-Settings#storage-cleanup)), so plan to clear old images some other way.
- **Only use characters Windows allows in filenames.** Leave out `\ / : * ? " < > |`. Saving fails if the name can't be used as a file.
- **Other programs can catch a file mid-write.** In ZWO camera mode the image is written straight to its final name. If another program polls a fixed filename, it can occasionally catch a half-written file. The [Web Server](Web-Server)'s `/latest` endpoint always serves a complete image.
- **Directory Watch mode ignores this field.** It names each output `<folder>_<original name>`: the name of the folder holding the source image, an underscore, then the source file's name without its extension, with the extension from **Format**. Each output is designed to be written to a temporary file first and then renamed, so that other programs don't see a partial image.
- **Headless mode** replaces `{session}` and `{timestamp}` in the same way, but not `{filename}`.
