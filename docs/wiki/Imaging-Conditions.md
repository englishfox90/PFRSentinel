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

The settings are on the **[NINA & ASCOM](NINA-and-ASCOM)** tab, in the **Imaging Conditions** card. The card shows the current verdict in one line, with a **Live view** button.

The status strip at the top of the window has an **IMAGING** tile with the same verdict. Before capture starts it reads **Idle**; once capture is running it reads **Warming up** until enough good frames have been seen. **Click the tile** to open the conditions panel on Live Monitoring.

### The conditions panel on Live Monitoring

On the **Live Monitoring** page a narrow panel sits beside the preview. Hide it with the arrow at its top right, and bring it back with **Conditions** in the preview's header; Sentinel remembers which you chose. It shows, from the top:

- **The verdict in one sentence**, for example "Imaging paused at 23:14 — cloud moving in. 2 of 3 good frames to resume.", "OK to image since 21:02." or "Idle — start capture to judge the sky."
- **The latest numbers**: stars against your clear-sky figure (for example "Stars 131/315"), cloud cover and FWHM. Hover for the full measurements.
- **NINA last checked**: when NINA last asked for the verdict, for example "NINA last checked 4 s ago". It reads "NINA hasn't checked in yet" until it does, and turns amber if NINA's request was refused (check the control API) or if it has not asked for over 15 minutes while you capture. Sentinel tells NINA apart from other tools by the name the plugin sends with each request.
- **A chart of the last few hours**: the star index as a line, your clear-sky figure dashed once it is learned, and amber shading wherever imaging was not OK, including gaps when no frames arrived.
- **Recent**: the last few pauses and resumes with their time, the reason in plain words and how long each lasted ("so far" while it still holds). The list is kept across a restart of Sentinel for 24 hours.
- **Clear-sky baseline**: how far learning has got for the current Moon phase, with a progress bar, or the learned figure once it is ready.

The panel is shown only on Live Monitoring, where the preview has room beside it. **Settings in NINA & ASCOM** at its foot takes you to the card.

The **star index** is not the same number as the star count on the **SEEING** tile. The star index counts stars on the sky disc after it has been scaled to a fixed size, so it can be compared from frame to frame and with your clear-sky figure. The SEEING count comes from the full-size frame, where the sharpness (FWHM) is measured, and changes with the frame size. Hover over the chart or the numbers for the same explanation.

The roof and sky readings need [ML Models](ML-Models) switched on. Without them, turn off **require roof open** in `config.json` (see below) if you have no roof.

## Learning your sky

Every installation sees a different number of stars on a clear night. The lens, the camera, the exposure, the horizon and the light pollution all change it, so PFR Sentinel ships with no star numbers at all. It learns what a clear night looks like **on your rig**, from your own frames.

A frame teaches it only when it is certain the sky is clear: the roof reads Open, the sun is more than 18° below the horizon, the sky classifier reads Clear, and the frame is not just sensor noise. A bright Moon washes out many stars, so the clear-sky count is learned separately for the Moon below the horizon and for each quarter of Moon brightness.

Until enough clear frames have been seen for the current Moon phase (30 by default), the conditions panel says, for example, **Learning your clear sky with the Moon up, 25–50% lit — 12 of 30 clear frames**. While it is learning:

- no star-count comparison is made, so a low count alone never pauses imaging
- the cloud cover is a rough figure taken from the ML sky class, and is marked as such
- the other checks (roof, sun, sky class, a sudden drop in stars, sharpness) still apply

The learned counts start over by themselves when you change the camera, the frame size or the lens (a new all-sky calibration with a noticeably different scale). Use **Reset baseline** on the card after anything else that changes how many stars the camera sees, such as a new exposure or gain regime, or moving the camera.

## When imaging is paused

A frame counts as **bad** when any of these is true:

| Reason | What it means |
|---|---|
| Roof not open | The roof is not read Open with enough confidence |
| Sun too high | The sun is above your chosen limit: 18° below the horizon (astronomical night), or 12° (nautical twilight) |
| Frame is sensor noise | Nothing in the frame can be judged |
| No sky reading | Neither a star count nor an ML sky reading is available |
| Cloud cover high | Estimated cloud cover is above your cloud limit (50 % with Balanced) |
| Too few stars | Fewer than 40 % of your learned clear-sky count with Balanced (only once the count is learned) |
| Stars dropping | The star count fell by more than 40 % (Balanced) against the last 10 frames |
| Stars soft (FWHM) | Stars are more than 50 % wider (Balanced) than tonight's median. FWHM is how wide a star looks in pixels: haze, dew or poor seeing make it grow |

One bad frame does not pause imaging. With Balanced it takes **3 bad frames in a row** to pause, and **3 good frames in a row** to resume, so a single passing cloud or a single clear gap does not stop and restart a sequence. When PFR Sentinel starts, imaging stays paused ("warming up") until it has seen 3 good frames.

If frames stop arriving, for example because the camera disconnected, the verdict goes **stale** and reads as not OK: the tile shows **Paused · No recent frames**, or **Paused · Capture stopped** if you stopped capture yourself, and the conditions panel says since when no frames have arrived. By default this happens after three frame intervals, and never sooner than 2 minutes. When frames resume, the 3 good frames are needed again.

## Sensitivity

**Sensitivity** sets every threshold that decides how readily imaging pauses, in one choice:

| | Relaxed | Balanced (default) | Strict |
|---|---|---|---|
| Bad frames in a row to pause | 5 | 3 | 2 |
| Good frames in a row to resume | 2 | 3 | 5 |
| Cloud limit | 70 % | 50 % | 30 % |
| "Too few stars" below this share of clear sky | 20 % | 40 % | 60 % |
| Stars fading (drop against the last 10 frames) | 60 % | 40 % | 25 % |
| Stars softer (FWHM rise against tonight) | 80 % | 50 % | 30 % |

- **Relaxed** rides out passing cloud: slow to pause, quick to resume. Before your clear sky is learned it keeps imaging through "Partly Cloudy".
- **Balanced** is the setting every earlier version used, so nothing changes unless you pick another.
- **Strict** protects every sub-exposure: quick to pause, slow to resume.

Open **Advanced — the exact numbers** to see or change each one. Change any of them and Sensitivity reads **Custom**; set them back and it shows the preset again. Choosing a preset rewrites all six.

The cloud limit and the "too few stars" figure are one decision. Once your clear sky is learned, Sentinel estimates cloud cover from how many of your clear-sky stars are missing, so a 50 % cloud limit already pauses when half the stars are gone. The "too few stars" figure therefore always sits 10 points below the cloud line (40 % of clear sky for a 50 % limit) and moves with it when you change the cloud limit: it is a second, plainer reason that shows when the sky is clearly worse, not a separate threshold to keep in step.

## Settings

The card holds the main settings. Everything else lives in the `imaging_conditions` block of `config.json`. The numbers are what Sentinel uses; `sensitivity` only records which preset they came from:

| Setting | Default | Meaning |
|---|---|---|
| `enabled` | `true` | Off: the verdict reads "disabled" and is never OK |
| `sensitivity` | `balanced` | `relaxed`, `balanced`, `strict` or `custom`: the preset last chosen on the card. Display only |
| `bad_frames` / `good_frames` | `3` / `3` | Frames in a row to pause / resume |
| `max_sun_alt_deg` | `-18` | `-18` for astronomical night, `-12` to allow nautical twilight |
| `require_roof_open` | `true` | Turn off for a rig with no roof |
| `min_roof_confidence` | `0.7` | How sure the roof classifier must be |
| `min_sky_confidence` | `0.6` | Below this the ML sky class is not used |
| `max_cloud_cover_pct` | `50` | Cloud cover limit |
| `min_star_ratio` | `0.4` | Star count as a fraction of your learned clear-sky count |
| `star_drop_pct` | `40` | Drop in stars, in %, against the median of the last `trend_frames` frames |
| `trend_frames` | `10` | Frames the drop is measured against |
| `fwhm_rise_pct` | `50` | Rise in FWHM, in %, against tonight's median |
| `min_stars_for_fwhm` | `10` | Fewer stars than this: no sharpness reading |
| `baseline_min_samples` | `30` | Clear frames needed per Moon phase before the clear-sky count is used |
| `stale_after_s` | `0` | Seconds without a frame before the verdict is stale; `0` picks it from the frame interval |

## Reading it from a script

The verdict is served at `GET /conditions` on the [web server](Web-Server), with the same token as the [Capture Control API](Capture-Control-API):

```
curl -H "Authorization: Bearer <token>" http://127.0.0.1:8080/conditions
```

The main fields are `imaging_ok` (true or false), `reasons` (a list of codes such as `cloud_cover_high` or `stars_dropping`), `changed_at`, `stale`, `cloud_cover_pct` with `cloud_cover_source` (`star_baseline` or `ml_class`), `stars.change_pct` (signed %, e.g. `-45.0`, over `stars.window_frames` frames), `fwhm.px`, `fwhm.change_pct` and `baseline_status` (`learning` or `ready`, with `samples` and `needed`). `fwhm.arcsec` is filled in only when an [all-sky calibration](All-Sky-Overlay) gives the plate scale. `events` lists the recent pauses and resumes, newest first: `at`, `kind` (`paused` or `resumed`), `reasons` and `duration_s` (null while it still holds). Every field, including the nested ones such as `stars.count`, is described in the `/conditions` fields table at `/docs` on the web server (shown once the control API is on).

`/status` carries a short `conditions` block (`imaging_ok`, `reasons`, `changed_at`, `stale`) without a token.

## Limits

- The star count and the sharpness come from the all-sky camera, not from your imaging camera. They describe the whole sky, not your target's part of it.
- The arcsecond figure is the plate scale at the centre of the fisheye. Towards the horizon the real figure is larger.
- While your clear-sky count is still being learned, cloud cover comes from the ML sky class and is only a rough figure.
