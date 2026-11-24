# Imaging Conditions

> **New in the next release** — not available in version 3.8.0 or earlier.

PFR Sentinel judges every frame it processes and decides whether the sky is good enough to image through. The NINA plugin reads that verdict so a sequence can pause when cloud rolls in and carry on when it clears. Sentinel does the judging. In NINA you only choose what to do with the answer.

The verdict is worked out from readings Sentinel already takes on each frame, so it costs no extra image processing:

- the **roof** reading from the [ML roof classifier](ML-Models)
- the **sky** class from the ML sky classifier (Clear, Partly Cloudy, Overcast)
- the **star count**: how many stars Sentinel finds in the frame
- how that count compares with **your own clear sky** (see below)
- how fast the count is **changing** compared with the last few frames
- **star sharpness** (FWHM), compared with the rest of tonight
- the **height of the sun**

It works in camera mode (ZWO and [Alpaca](Alpaca-Camera) cameras) and in Directory Watch mode. Headless mode serves the address but does not judge frames, so there it reports "warming up" and then "stale".

## Where to find it

The settings are on the **[NINA & ASCOM](NINA-and-ASCOM)** tab, in the **Imaging Conditions** card. The card shows the current verdict in one line ("Now: …"), with what else is wrong or how many good frames are still needed underneath, and a **Live view** button that opens the conditions panel. Its rows:

| Row | What it sets |
|---|---|
| **Judge imaging conditions** | On by default. Off: NINA is always told not to image (reason: disabled) |
| **Image only in** | **Astronomical night (sun below -18°)** (the default) or **Nautical twilight (sun below -12°)** |
| **Sensitivity** | **Relaxed**, **Balanced** (the default), **Strict** or **Custom**, with a line saying what the choice does. See [Sensitivity](#sensitivity) |
| **Advanced — the exact numbers** | Opens **Cloud limit**, **Stars fading**, **Stars softer**, **Pause after** and **Resume after** |
| **Clear-sky baseline** | **Reset baseline**. See [Learning your sky](#learning-your-sky) |

### The IMAGING tile

The status strip at the top of the window has an **IMAGING** tile with the same verdict. **Click the tile**, from any page, to open the conditions panel on Live Monitoring.

| Tile reads | Meaning |
|---|---|
| **Idle** | Capture is stopped and no frame has been judged yet |
| **Warming up** | Capture is running, but no frame has been judged yet. NINA is not held back by this |
| **OK · 12% cloud** | Imaging is OK. The cloud figure is left out when there is none |
| **Paused · Cloud cover high** (or another reason) | Imaging is not OK; the first reason is named. See [When imaging is paused](#when-imaging-is-paused) |
| **Recovering** | The sky looks good again, and Sentinel is counting good frames before it says OK |
| **Paused · No recent frames** | Frames stopped arriving while capture runs |
| **Paused · Capture stopped** | You stopped capture after frames had been judged |
| **Off** | **Judge imaging conditions** is off |

On a narrow window this tile is the last to be shortened, and the whole text is in its tooltip; see [Live Monitoring](Live-Monitoring#on-a-narrow-window).

### The conditions panel on Live Monitoring

On the **Live Monitoring** page a narrow panel sits beside the preview. Hide it with the arrow at its top right, and bring it back with **Conditions** in the preview's header; Sentinel remembers which you chose. It shows, from the top:

- **The verdict in one sentence**, for example "Imaging paused at 23:14 — cloud moving in. 2 of 3 good frames to resume.", "OK to image since 21:02.", "Waiting for dark — imaging starts once the sun is low enough." or "Idle — start capture to judge the sky."
- **The latest numbers**: stars against your clear-sky figure (for example "Stars 131/315"), cloud cover and FWHM. Hover for the full measurements.
- **The target and the Moon**, when NINA is imaging a target and the Moon is up: for example "Target M 15 is 18° from an 87% Moon". See [Your target and the Moon](#your-target-and-the-moon).
- **NINA last checked**: when NINA last asked for the verdict, for example "NINA last checked 4 s ago". It reads "NINA hasn't checked in yet" until it does, and turns amber if NINA's request was refused (check the control API) or if it has not asked for over 15 minutes while you capture. Sentinel tells NINA apart from other tools by the name the plugin sends with each request.
- **A chart of the last few hours**: the star count as a line, your clear-sky figure dashed once it is learned, and amber shading wherever imaging was not OK, including gaps when no frames arrived.
- **Recent**: the last few pauses and resumes with their time, the reason in plain words and how long each lasted ("so far" while it still holds). The list is kept across a restart of Sentinel for 24 hours.
- **Clear-sky baseline**: how far learning has got for the current Moon phase, with a progress bar, or the learned figure once it is ready.

The panel is shown only on Live Monitoring, where the preview has room beside it. **Settings in NINA & ASCOM** at its foot takes you to the card.

There is one star count in Sentinel. The **Stars** figure here is the same number as the star count on the **SEEING** tile, the `{STAR_COUNT}` overlay token and the image library: the stars found on the sky disc of the full-size frame, with a detection threshold that follows the frame's own noise. The sharpness (FWHM) is measured on those same stars. Hover over the chart or the numbers for the same explanation.

While star detection is skipped (roof closed, daylight, an exposure too short, or an open roof with no stars in the frame) there is no star count, and the panel shows none. An open roof with no stars in the frame reads **Too few stars**.

The roof and sky readings need [ML Models](ML-Models) switched on. Without them, turn off **require roof open** in `config.json` (see below) if you have no roof.

## Learning your sky

Every installation sees a different number of stars on a clear night. The lens, the camera, the exposure, the horizon and the light pollution all change it, so PFR Sentinel ships with no star numbers at all. It learns what a clear night looks like **on your rig**, from your own frames.

A frame teaches it only when it is certain the sky is clear: the roof reads Open, the sun is more than 18° below the horizon, the sky classifier reads Clear, and the frame is not just sensor noise. A bright Moon washes out many stars, so the clear-sky count is learned separately for the Moon below the horizon and for each quarter of Moon brightness. A Moon that is at least half lit is also split by where it is: **low** (under about 30° above the horizon) or **high**. A bright Moon high up lights the whole sky and hides far more stars than the same Moon near the horizon, so the two keep their own figures. A thinner Moon is not split, because it changes the star count little wherever it is and every group has to collect its own clear nights. The Moon has to move about 3° past the 30° line before the group changes, so it does not flip back and forth on the line. Without a site (latitude and longitude in the weather settings) the Moon's position is not known and one figure is used for every Moon.

> **New in the next release** — the split by Moon height is not available in version 3.8.0 or earlier.

What it keeps is a history of **clear nights**, not of single frames. Each night with enough clear frames (30 by default) adds one entry per Moon phase: that night's typical star count. Sentinel keeps the newest 60 such nights per Moon phase. Only a newer clear night pushes an old one out, never the calendar, so a month of bad weather or a closed roof loses nothing. A night runs from local noon to the next local noon, like the nights in the image library, so it is never split at midnight.

Your clear-sky figure is taken from the **better** of those nights (the 75th percentile of the nights' counts, not their average). A hazy night that the sky classifier still called Clear therefore barely moves it, and the figure gets steadier as nights add up. Tonight counts too, as soon as it has enough clear frames, so on the very first night the figure is simply tonight's typical count. Once it is ready, the conditions panel shows how many nights it rests on, for example **star count 315 from 12 clear nights**.

The same clear nights also record how sharp the stars were. From them Sentinel works out the star width (FWHM) of your sharper nights, and shows it next to tonight's reading ("your sharp nights 2.6 px"). This figure is for your information only: it never pauses imaging. The **Stars soft** check still compares each frame with the rest of tonight.

Until one night has enough clear frames for the current Moon phase, the conditions panel says, for example, **Learning your clear sky with the Moon high, 75–100% lit — 12 of 30 clear frames**. While it is learning:

- no star-count comparison is made, so a low count alone never pauses imaging
- the cloud cover is a rough figure taken from the ML sky class, and is marked as such: Clear reads 5 %, Partly Cloudy 60 % and Overcast 95 % (the sky model has only these three classes). So with the Balanced 50 % cloud limit, Partly Cloudy pauses imaging; with Relaxed (70 %) it does not
- the other checks (roof, sun, sky class, a sudden drop in stars, sharpness) still apply

The learned nights start over by themselves when you change the camera, the frame size or the lens (a new all-sky calibration with a noticeably different scale). Use **Reset baseline** on the card after anything else that changes how many stars the camera sees, such as a new exposure or gain regime, or moving the camera.

## When imaging is paused

A frame counts as **bad** when any of these is true:

| Reason | What it means |
|---|---|
| Roof not open | The roof is not read Open with enough confidence |
| Sun too high | The sun is above your chosen limit: 18° below the horizon (astronomical night), or 12° (nautical twilight) |
| Frame is sensor noise | Nothing in the frame can be judged |
| No sky reading | Neither a star count nor an ML sky reading is available |
| Cloud cover high | Estimated cloud cover is above your cloud limit (50 % with Balanced) |
| Too few stars | Fewer than 40 % of your learned clear-sky count with Balanced (only once the count is learned), or the roof is open and the frame shows no stars at all |
| Stars dropping | The star count fell by more than 40 % (Balanced) against the last 10 frames |
| Stars soft (FWHM) | Stars are more than 50 % wider (Balanced) than tonight's median. FWHM is how wide a star looks in pixels: haze, dew or poor seeing make it grow |

One bad frame does not pause imaging. With Balanced it takes **3 bad frames in a row** to pause, and **3 good frames in a row** to resume, so a single passing cloud or a single clear gap does not stop and restart a sequence. A start is the exception: when PFR Sentinel starts, the **first good frame** is enough, because nothing has said the sky is bad. If the first frames are bad (roof closed, cloud), the usual 3 good frames in a row are needed. A restart in the middle of a night carries on from where it stopped, as long as the last frame is no older than the stale limit below.

If frames stop arriving, for example because the camera disconnected, the verdict goes **stale** and reads as not OK: the tile shows **Paused · No recent frames**, or **Paused · Capture stopped** if you stopped capture yourself, and the conditions panel says since when no frames have arrived. By default this happens after three frame intervals, and never sooner than 2 minutes. When frames resume, it starts over as it does at a start: the first good frame is enough.

## Sensitivity

**Sensitivity** sets every threshold that decides how readily imaging pauses, in one choice:

| Row under **Advanced** | Relaxed | Balanced (default) | Strict |
|---|---|---|---|
| **Pause after** (bad frames in a row) | 5 | 3 | 2 |
| **Resume after** (good frames in a row) | 2 | 3 | 5 |
| **Cloud limit** | 70 % | 50 % | 30 % |
| "Too few stars" below this share of clear sky (no row of its own; shown under **Cloud limit**) | 20 % | 40 % | 60 % |
| **Stars fading** (drop against the last 10 frames) | 60 % | 40 % | 25 % |
| **Stars softer** (FWHM rise against tonight's median) | 80 % | 50 % | 30 % |

- **Relaxed** rides out passing cloud: slow to pause, quick to resume. Before your clear sky is learned it keeps imaging through "Partly Cloudy".
- **Balanced** is the setting every earlier version used, so nothing changes unless you pick another.
- **Strict** protects every sub-exposure: quick to pause, slow to resume.

Open **Advanced — the exact numbers** to see or change each one. Change any of them and Sensitivity reads **Custom**; set them back and it shows the preset again. Choosing a preset rewrites all six. Choosing **Custom** opens **Advanced** and changes nothing.

The cloud limit and the "too few stars" figure are one decision. Once your clear sky is learned, Sentinel estimates cloud cover from how many of your clear-sky stars are missing, so a 50 % cloud limit already pauses when half the stars are gone. The "too few stars" figure therefore always sits 10 points below the cloud line (40 % of clear sky for a 50 % limit, never under 5 %) and moves with it when you change the cloud limit, as the line under **Cloud limit** shows ("“Too few stars” is also reported below 40% of the clear-sky star count (follows the cloud limit)"): it is a second, plainer reason that shows when the sky is clearly worse, not a separate threshold to keep in step.

## Settings

The card holds the main settings (the card row for each key is given below). Everything else lives in the `imaging_conditions` block of `config.json`. The numbers are what Sentinel uses; `sensitivity` only records which preset they came from:

| Setting | Default | Meaning |
|---|---|---|
| `enabled` | `true` | **Judge imaging conditions**. Off: the verdict reads "disabled" and is never OK |
| `sensitivity` | `balanced` | `relaxed`, `balanced`, `strict` or `custom`: the preset last chosen on the card. Display only |
| `bad_frames` / `good_frames` | `3` / `3` | **Pause after** / **Resume after**: frames in a row to pause / resume |
| `max_sun_alt_deg` | `-18` | **Image only in**: `-18` for astronomical night, `-12` to allow nautical twilight |
| `require_roof_open` | `true` | Turn off for a rig with no roof |
| `min_roof_confidence` | `0.7` | How sure the roof classifier must be |
| `min_sky_confidence` | `0.6` | Below this the ML sky class is not used |
| `max_cloud_cover_pct` | `50` | **Cloud limit** |
| `min_star_ratio` | `0.4` | "Too few stars": star count as a fraction of your learned clear-sky count. Set with the cloud limit on the card |
| `star_drop_pct` | `40` | **Stars fading**: drop in stars, in %, against the median of the last `trend_frames` frames |
| `trend_frames` | `10` | Frames the drop is measured against |
| `fwhm_rise_pct` | `50` | **Stars softer**: rise in FWHM, in %, against tonight's median |
| `min_stars_for_fwhm` | `10` | Fewer stars than this: no sharpness reading |
| `baseline_min_samples` | `30` | Clear frames a night needs, per Moon phase, before it counts towards the clear-sky figure |
| `stale_after_s` | `0` | Seconds without a frame before the verdict is stale; `0` picks it from the frame interval |

## Your target and the Moon

> **New in the next release** — not available in version 3.8.0 or earlier.

When the [NINA plugin](NINA-Integration) reports the target your sequence is imaging, Sentinel works out how far that target is from the Moon and shows it while the Moon is up: on the conditions panel, on the Imaging Conditions card, in the IMAGING tile's tooltip and, for past frames, in the [Image Library](Image-Library). The line turns amber when the target is close to a bright Moon. "Close" grows with the Moon's brightness: under about 20° for a thin Moon, up to 60° at full Moon.

**Sentinel never pauses imaging for this.** The verdict stays a statement about the sky. Imaging close to the Moon is your choice (narrowband imagers do it on purpose), and a pause for it would last until the Moon set. If you do want NINA to wait, set **Min Moon separation (°)** on the plugin's trigger or loop condition.

The target is shown for as long as the all-sky overlay would draw it (two minutes after NINA last reported it, by default). Nothing is shown when NINA reports no target or the Moon is down.

## Reading it from a script

The verdict is served at `GET /conditions` on the [web server](Web-Server), with the same token as the [Capture Control API](Capture-Control-API):

```
curl -H "Authorization: Bearer <token>" http://127.0.0.1:8080/conditions
```

The main fields are `imaging_ok` (true or false), `reasons` (a list of codes such as `cloud_cover_high` or `stars_dropping`), `changed_at`, `stale`, `cloud_cover_pct` with `cloud_cover_source` (`star_baseline` or `ml_class`), `stars.change_pct` (signed %, e.g. `-45.0`, over `stars.window_frames` frames), `fwhm.px`, `fwhm.change_pct` and `baseline_status` (`learning` or `ready`, with `nights`, the clear nights behind the figure, `samples` and `needed`). `fwhm.reference_px` and `fwhm.reference_nights` give the star width of your sharper nights and how many nights it comes from; no check uses them. `fwhm.arcsec` is filled in only when an [all-sky calibration](All-Sky-Overlay) gives the plate scale. `events` lists the recent pauses and resumes, newest first: `at`, `kind` (`paused` or `resumed`), `reasons` and `duration_s` (null while it still holds). Every field, including the nested ones such as `stars.count`, is described in the `/conditions` fields table at `/docs` on the web server (shown once the control API is on).

`moon.altitude_deg` is the Moon's height above the horizon, and `target` (null without a fresh NINA target) carries `name`, `moon_separation_deg` and `altitude_deg`. `baseline_bin` names the Moon group in use, for example `moon_down`, `moon_up_25_50` or `moon_up_75_100_high`.

`/status` carries a short `conditions` block (`imaging_ok`, `reasons`, `changed_at`, `stale`) without a token.

## Limits

- The star count and the sharpness come from the all-sky camera, not from your imaging camera. They describe the whole sky, not your target's part of it.
- The arcsecond figure is the plate scale at the centre of the fisheye. Towards the horizon the real figure is larger.
- While your clear-sky count is still being learned, cloud cover comes from the ML sky class and is only a rough figure.
