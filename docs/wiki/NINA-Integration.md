# NINA Integration

PFR Sentinel can work hand in hand with N.I.N.A. A small plugin, bundled with Sentinel and installed from the **[NINA & ASCOM](NINA-and-ASCOM)** tab (the **Output** tab in 3.8.0 and earlier), adds a **PFR Sentinel** panel to NINA showing the live pier camera frame and Sentinel's health, plus two Advanced Sequencer instructions — **Start Sentinel Capture** and **Stop Sentinel Capture** — so capture follows your observing session automatically, and a trigger and a loop condition that pause imaging when the pier camera says the sky has turned bad. Everything runs through Sentinel's [Capture Control API](Capture-Control-API), and the plugin pairs itself with Sentinel: there is no address or token to type into NINA.

---

## What You Get

### The PFR Sentinel panel

A panel in NINA's **Imaging** tab, titled **PFR Sentinel**:

| Element | What it shows |
|---------|---------------|
| Live frame | The latest image from Sentinel's web server, refreshed every few seconds while the panel is visible. When Sentinel reports the frame as stale (no new frame for 5 minutes), the image is dimmed and marked **STALE**. |
| Frame line | How old the last frame is and its file name. |
| **Health** | Sentinel's overall capture health — **OK**, **IDLE**, **DEGRADED**, **RECOVERING** or **ERROR** — with Sentinel's own explanation underneath when there is one. See [Capture Control API](Capture-Control-API#health-block) for what each means. |
| Statistics | **Capture** (state and mode), **Interval**, **Last frame**, **Next frame**. A **Recovery** row appears only while the camera is recovering or has needed recovery attempts. |
| **All-sky target** | What the plugin last sent for the [all-sky overlay](#target-on-the-all-sky-overlay): whether it is sending, idle (no target running), off, or not reaching Sentinel, and while sending, the target's name, RA / Dec, field of view, rotation and how long ago Sentinel last accepted it. **Field: none (reticle)** means no field of view could be worked out, so Sentinel draws a reticle instead of a box. When a send fails, the reason is shown underneath in amber. Beside the statistics when the panel is wide enough, underneath them when it is not. *Added in version v3.7.9.* |
| **Conditions** | Sentinel's [imaging conditions](Imaging-Conditions) verdict — **OK TO IMAGE**, **NOT OK** or **NO VERDICT** — with the reasons in plain words, then **Roof**, **Sky**, **Stars** (count and ratio to this rig's clear-sky count), **Star change** (percent against recent frames), **Cloud cover**, **FWHM** (with its change against the night median), **Sun**, **Baseline** (learning or ready) and how long ago Sentinel judged a frame. While Sentinel is still learning this sky an amber line says so. This is Sentinel's verdict alone; the limits you set on a trigger or condition are shown on that item. *New in the next release.* |
| **Start** / **Stop** | Start or stop Sentinel capture. Both buttons are disabled while a command is waiting for Sentinel to confirm, and whenever capture control is unavailable — a line underneath always says why. |
| **Reconnect** | Re-reads Sentinel's settings and checks in immediately. Use it after changing a setting in Sentinel, such as turning capture control on: the panel's regular checks don't re-read Sentinel's settings. |
| Endpoint line | The Sentinel address the panel is talking to. It turns amber when an address override is in force. |

The panel stops polling while it is hidden, so it costs nothing when you are not looking at it. A camera fault does not disable Start/Stop — that is exactly when you might want to reconnect the camera and press Start again.

### Sequencer instructions

Both instructions appear in the Advanced Sequencer under the **PFR Sentinel** category.

| Instruction | What it does |
|-------------|--------------|
| **Start Sentinel Capture** | Starts capture in whatever mode Sentinel is configured for (camera or directory watch) and waits until capture is actually running before the sequence continues. |
| **Stop Sentinel Capture** | Stops capture and waits until it has actually stopped before the sequence continues. |

Each instruction has one setting:

| Setting | Default | Range | Description |
|---------|---------|-------|-------------|
| **Wait up to (s)** | 30 | 1–300 | How long Sentinel waits for capture to reach the target state before reporting a timeout. Values outside the range are clamped. Raise it if your camera is slow to start. |

Both are safe to re-run: starting a capture that is already running, or stopping one that is already stopped, succeeds without changing anything. A sequence that fires Stop twice while aborting will not fail.

A scheduled capture that is enabled but waiting for its capture window counts as started, so **Start Sentinel Capture** will not hold your sequence until the window opens.

### Pausing on bad conditions

> **New in the next release** — not available in plugin version 1.2.1.0 or Sentinel 3.8.0 or earlier.

Two more entries under the **PFR Sentinel** category let a sequence react to what the pier camera sees. Both read Sentinel's [imaging conditions](Imaging-Conditions) verdict (`GET /conditions`), which judges each pier-camera frame for roof, daylight, cloud, star count and star size.

| Entry | Kind | What it does |
|-------|------|--------------|
| **Sentinel: pause on bad conditions** | Trigger | Before each exposure, asks Sentinel whether the sky is good enough to image. If not, it waits — the sequence stays where it is — until the sky has stayed good for the **Settle (s)** time, then imaging carries on. |
| **Sentinel: loop while conditions OK** | Loop condition | Keeps the container looping while the sky is good enough, and ends the loop once it is not, so the sequence moves on to whatever comes next. |

**It only waits.** The trigger does not stop guiding or tracking, park the mount, close the roof or warm the camera. Use NINA's own instructions and your safety monitor for those. The mount keeps tracking during a pause. If a pause can last long, put NINA's **Restore Guiding** and **Center After Drift** triggers on the same container so guiding and framing are recovered when imaging resumes, and let PHD2's own star-lost handling deal with guiding through cloud.

Both follow Sentinel's verdict, and both accept optional limits of your own. Leave a box empty (or enter 0) to not use it. A limit can only make the check stricter, never overrule Sentinel's "not OK".

| Setting | Default | What counts as bad |
|---------|---------|--------------------|
| **Min star ratio** | empty | Stars on the frame divided by this rig's clear-sky count is below the number, e.g. 0.5. |
| **Max cloud %** | empty | Estimated cloud cover is above the percentage. |
| **Star drop %** | empty | The star count has dropped by this percentage or more against the recent frames, e.g. 40 is crossed at −40 %. |
| **FWHM rise %** | empty | Star size (FWHM) is this percentage or more above the night's median. |
| **Max FWHM (″)** | empty | Pier-camera FWHM in arcseconds is above the number. Needs Sentinel to know the pier camera's plate scale. |
| **Max FWHM (px)** | empty | Pier-camera FWHM in pixels is above the number. |
| **Require roof Open** | off | The roof does not read Open, whatever Sentinel's own roof setting. An unknown roof is not Open. |
| **No verdict = bad** | on | See below. |

A limit whose measurement Sentinel does not have yet (no stars detected, no plate scale) is skipped, not treated as bad, and the item's status line says so.

**While Sentinel is learning this sky.** Every rig learns its own clear-sky star count on its own installation, so a new install starts out learning. Until it has enough clear frames, **Min star ratio** and **Max cloud %** have nothing to compare against and are skipped; the status line says, for example, "Sentinel is learning this sky (12/30 clear frames); star ratio and cloud cover not used yet". **Star drop %** and **FWHM rise %** work from the first frames, because they compare the sky with itself.

**No verdict.** When Sentinel cannot give a verdict — it is not running or not reachable, it rejected the token, capture stopped so its verdict is stale, it is still gathering its first frames, imaging conditions are switched off in Sentinel, or this Sentinel is too old to have them — **No verdict = bad** decides. On (the default), the trigger pauses and the loop ends: no answer is not a yes. Off, imaging carries on. The status line always names the reason, so a pause caused by a rejected token is easy to tell from a cloudy sky.

The trigger has a few settings of its own:

| Setting | Default | Description |
|---------|---------|-------------|
| **Settle (s)** | 60 | How long the sky must stay good, without a single bad check, before imaging resumes. 0–3600. |
| **Max wait (min)** | 0 | The longest pause. 0 waits as long as it takes. A long pause also holds back everything after it in the sequence, so set a limit if your sequence ends with a park or warm-up. Up to 1440. |
| **Fail on timeout** | off | When **Max wait** runs out: ticked, the trigger fails (NINA logs it and handles it like any other failed trigger); unticked, the sequence carries on with the next exposure. It is checked again before the exposure after that. |
| **Only before exposures** | on | Check only before an exposure, so a park, warm-up or **Stop Sentinel Capture** after the imaging is never held back by a pause. |

Each entry shows Sentinel's current verdict in the sequencer, for example "Sentinel: Not OK: cloud cover is high (72%)", so you can see what it would do before you start. NINA's validation checks only the settings (for example a settle time as long as the maximum wait); Sentinel's own state is shown on that line instead, because NINA fails a trigger outright on a validation issue, and that would turn "pause when there is no verdict" into "carry on imaging".

How often it asks: all the entries in a sequence and the panel share one copy of Sentinel's answer, refreshed at most every 5 seconds, so adding the trigger to many containers does not add load. Between exposures the check uses an answer at most 15 seconds old, waiting up to 10 seconds for a fresh one when an exposure has taken longer than that. While paused it checks every 5 seconds. The loop condition, like NINA's own loop conditions, is checked between exposures and does not cut one short.

#### Example sequence

Put the trigger on the target's container, so it guards every exposure of that target:

```text
Sequence
├─ Start                           (cool camera, unpark, Start Sentinel Capture)
├─ Target: M31  (Deep Sky Object container)
│   ├─ Triggers
│   │   ├─ Sentinel: pause on bad conditions   Star drop % 40, Settle 120 s, Max wait 60 min
│   │   ├─ Restore Guiding
│   │   └─ Center After Drift
│   ├─ Slew and center, Start Guiding, Run Autofocus
│   └─ Smart Exposure  L 120 s x 60
└─ End                             (Stop Sentinel Capture, park, warm camera)
```

Thin cloud passing over: the trigger pauses before the next exposure, waits until Sentinel has seen two minutes of good sky, and imaging resumes; if it is still cloudy after an hour, the sequence carries on and checks again before the next exposure. **Only before exposures** keeps the End block free to park and warm the camera whatever the sky.

To move on instead of waiting — for example to a second target or to the end of the night — put **Sentinel: loop while conditions OK** on a loop container around the imaging instead (together with NINA's **Loop Until Time** if you also want a time limit).

### Target on the all-sky overlay

While a sequence is imaging a target, the plugin tells Sentinel which target it is, and Sentinel marks it on the [All-Sky Overlay](All-Sky-Overlay#nina-target): its name and its field of view as a box, or a reticle (a ring with four ticks) when no box can be drawn. You can see at a glance where the main scope is pointed against the clouds, the Moon and the trees.

What the plugin sends:

| Item | Where it comes from |
|------|---------------------|
| Target name | The running target in the sequencer (Advanced Sequencer first, then the simple sequence). Cut to 64 characters. |
| Position | The target's right ascension and declination, sent as J2000. |
| Field of view | Worked out from your imaging camera: pixel size × sensor width and height, divided by the telescope focal length in NINA's profile. When the camera is not connected, the pixel size from the profile and the camera size from the framing assistant settings are used. Left out when any of these is missing, in which case the reticle is drawn instead of a box. |
| Rotation | The target's position angle from the sequencer, in degrees east of north. |

When it is sent:

- When the target changes, and again every 30 seconds while it is running. Sentinel hides the marker if it hears nothing for about 2 minutes (the **Hide after (s)** setting on the All-Sky page), so a closed or crashed NINA does not leave a marker on the sky.
- When no target is running any more — the sequence finished, was stopped, or moved on to something that is not a target — the plugin tells Sentinel once to clear the marker.

Turn it on or off with **Send the current sequencer target to Sentinel** on the plugin's options page in NINA. It is on by default; turning it off clears the marker. Like Start/Stop, it needs the **Enable Capture Control API** switch and uses the same token, so if Start/Stop work, this works too — there is nothing extra to set up. It also follows the **Sentinel base URL override**.

The marker is only drawn when the all-sky overlay is turned on and the lens is calibrated; see [All-Sky Overlay](All-Sky-Overlay#nina-target).

---

## Requirements

| Requirement | Detail |
|-------------|--------|
| PFR Sentinel | Version 3.7.0 or later, installed from the official installer (the plugin is bundled with it). |
| NINA | NINA 3.x. The plugin is built against and tested with NINA 3.2. |
| Same machine | NINA and Sentinel must run on the same PC, under the same Windows user account. See [Sentinel on another machine](#sentinel-on-another-machine). |
| Web server | **Enable Web Server** turned on in the **Web Server** card of the [Output Settings](Output-Settings) tab. |
| Capture control | **Enable Capture Control API** turned on in the **Control API** card of the [NINA & ASCOM](NINA-and-ASCOM) tab (the Output tab's **Web Server** card in 3.8.0 and earlier). The live frame and health work without it, but Start/Stop and the sequencer instructions need it. |
| Pausing on bad conditions | A Sentinel newer than 3.8.0 (with [imaging conditions](Imaging-Conditions)) and plugin version 1.3.0.0 or later. Also needs capture control, as above: `/conditions` uses the same token. An older Sentinel gives no verdict, and the status line says to update it. |
| Target on the all-sky overlay | Sentinel 3.7.9 or newer with plugin version 1.2.0.0 or later — the plugin bundled with that Sentinel. Also needs capture control, as above, and a calibrated [All-Sky Overlay](All-Sky-Overlay). An older Sentinel ignores the target and the plugin notes it once in NINA's log. |

---

## Setup

> **New in the next release** — not available in version 3.8.0 or earlier. Capture control and the NINA plugin card move to the **NINA & ASCOM** tab. In 3.8.0 and earlier both are on the **Output** tab, under and below the **Web Server** card.

1. In Sentinel, open the **Output** tab. In the **Web Server** card, turn on **Enable Web Server**.
2. Open the **NINA & ASCOM** tab (under **Automation** in the sidebar).
3. In the **Control API** card, turn on **Enable Capture Control API**. Sentinel generates an API token the first time. You do not need to copy it — the plugin reads it for you. If you enable control while the web server is off, Sentinel warns you: "Capture control needs the web server — turn it on in Output → Web Server."
4. Close NINA.
5. In the **NINA Plugin** card, click **Install Plugin**.
6. Start NINA. Open the **PFR Sentinel** panel from the Imaging tab, and look for the **PFR Sentinel** category in the Advanced Sequencer's instruction list.

---

## Installing, Updating and Removing

The **NINA Plugin** card on the [NINA & ASCOM](NINA-and-ASCOM) tab manages the plugin. Its **Status** line reports what Sentinel found:

| Status | Meaning | Button |
|--------|---------|--------|
| NINA was not found on this machine. | Sentinel could not find NINA's data folder for this Windows user. | Disabled |
| Not installed. | NINA was found; the plugin is not installed. | **Install Plugin** |
| Installed in NINA 3.0.0 (version …). | The plugin is installed and matches the copy bundled with Sentinel. The number is NINA's plugin folder (normally `3.0.0`), not your NINA version. | **Reinstall Plugin** |
| Update available: … | The copy bundled with this Sentinel is newer than, or different from, the installed one. Usually reads "Update available: … installed, … bundled." If both copies report the same version but the files differ, it reads "Update available: the bundled plugin differs from the installed copy (both report …)." | **Update Plugin** |
| Installed for an older NINA plugin version. Reinstall to put it in 3.0.0. | A copy exists only in a plugin folder NINA no longer reads, so NINA is not loading it. | **Reinstall Plugin** |
| The NINA plugin is not included in this build. | This copy of Sentinel was built without the plugin. Reinstall Sentinel from the official installer. | Disabled |

**Remove** deletes the installed plugin, including any stranded copy in an older plugin folder.

Things to know:

- **Close NINA before updating, reinstalling or removing.** NINA keeps a loaded plugin file locked, so Sentinel cannot replace or delete it while NINA is running. You will see: "The plugin file could not be written. Close NINA and try again — if NINA is already closed, check the file is not read-only or held by antivirus."
- After installing or updating, restart NINA to load the new plugin. After removing, restart NINA to unload it.
- If the plugin is only in an out-of-date folder when Sentinel starts, Sentinel shows a notification (once per session) suggesting a reinstall.
- A newer Sentinel may bundle a newer plugin. After upgrading Sentinel, check the card for **Update Plugin**.
- Uninstalling Sentinel also removes the plugin. Close NINA first, or the plugin file may be left behind.

---

## How Pairing Works

The plugin finds Sentinel by reading Sentinel's own settings file for the current Windows user. From it the plugin picks up:

- the web server's host and port (a host of `0.0.0.0` is treated as this machine),
- whether the capture control API is enabled,
- the API token.

So there is nothing to set up in NINA and no token to copy. The plugin never displays or logs the token.

If you click **Regenerate** next to **API Token** in Sentinel, the old token stops working immediately. The plugin notices the rejection, re-reads Sentinel's settings, and carries on with the new token — no NINA restart needed.

Turning capture control on is different. The panel keeps the settings it last read, and only re-reads them when you click **Reconnect** or after the token is rejected. After turning capture control on, click **Reconnect** in the panel (or restart NINA). The sequencer instructions pick up the change on their own within about ten seconds.

### Plugin option: base URL override

NINA's options page for the PFR Sentinel plugin has a **Sentinel base URL override** setting (for example `http://192.168.1.20:8080`). Leave it empty on the observatory PC. It changes only the address the plugin talks to — the token still comes from the local Sentinel settings — and it applies within a few seconds without a restart. The address must start with `http://` or `https://`.

The same page has the **Send the current sequencer target to Sentinel** switch — see [Target on the all-sky overlay](#target-on-the-all-sky-overlay).

---

## Using the Instructions in a Sequence

A typical night:

| Where in the sequence | Instruction |
|-----------------------|-------------|
| After the roof opens, before the first target | **Start Sentinel Capture** |
| In the end-of-sequence instructions, before parking | **Stop Sentinel Capture** |

**Validation before the night starts.** NINA checks both instructions while you build the sequence, so problems such as Sentinel not running or control switched off show up as validation issues straight away rather than failing at 22:15. The check is repeated in the background, so an issue clears on its own once you fix it.

**When a step fails**, the step fails in NINA with Sentinel's own explanation — for example the camera error Sentinel recorded ("No ZWO cameras detected…") — and the same text appears in NINA's log.

**Aborting a sequence while Start is waiting** abandons the wait, but it does not undo the start: Sentinel has already been told to start capture by then. The plugin deliberately does not issue a Stop on your behalf and writes a warning to NINA's log instead. If you want capture stopped, press **Stop** in the panel or in Sentinel.

**Pausing for cloud.** Add **Sentinel: pause on bad conditions** to each target's container — see [Pausing on bad conditions](#pausing-on-bad-conditions) for the settings and an example.

**Headless mode.** If Sentinel runs without its window (headless mode), the instructions work the same way. Stop pauses capture rather than closing Sentinel, so a later Start resumes it.

---

## Alternative: External Script Helpers

If you prefer not to install the plugin, Sentinel also ships script helpers that do the same job from NINA's **External Script** instruction. They read Sentinel's settings the same way, so there is still nothing to paste into NINA.

| File | Use |
|------|-----|
| `sentinel-capture-start.bat` | Point an External Script instruction at this to start capture. |
| `sentinel-capture-stop.bat` | Point an External Script instruction at this to stop capture. |
| `Invoke-SentinelCapture.ps1` | The PowerShell script both call. Can be run by hand to test. |

They are in the `scripts\nina` folder inside Sentinel's program files — for a default (per-user) install, `%LOCALAPPDATA%\Programs\PFRSentinel\_internal\scripts\nina\`. If you installed for all users, it is `C:\Program Files\PFRSentinel\_internal\scripts\nina\`. Enable the web server and the capture control API first, as in [Setup](#setup).

Each call waits up to 30 seconds for capture to reach the requested state, is safe to repeat, and exits with a non-zero code so NINA marks the step as failed:

| Exit code | Meaning | Fix |
|-----------|---------|-----|
| 0 | Success, including "already running" / "already stopped" | — |
| 2 | Sentinel's settings file not found or unreadable | Sentinel is not installed for this Windows user |
| 3 | Control API switched off or no token, or control not wired up | Enable **Enable Capture Control API** on the NINA & ASCOM tab; if it is already on, restart Sentinel |
| 4 | Sentinel not reachable | Check Sentinel is running with the web server enabled |
| 5 | Token rejected, or request refused by the Host check | Regenerate the token on the NINA & ASCOM tab; run the script on the same machine as Sentinel |
| 6 | Timed out waiting for the state | Camera slow to start — raise the timeout (see below) |
| 7 | Capture reported a failure | Read the message in NINA's log; usually a camera fault |

To test by hand, or to use a longer wait, run the script from PowerShell in that folder:

```powershell
.\Invoke-SentinelCapture.ps1 -Command start
.\Invoke-SentinelCapture.ps1 -Command stop -TimeoutSeconds 60
```

The script also accepts `-BaseUrl`, `-Token` and `-ConfigPath` overrides. Messages are written to NINA's log prefixed with `[Sentinel]`; the token is never printed.

What the plugin adds over the scripts: validation while you build the sequence (a script can only fail when it runs), the live panel, and native drag-and-drop instructions.

---

## Troubleshooting

### Plugin card problems

| Symptom | Fix |
|---------|-----|
| "NINA was not found on this machine." | Start NINA once under this Windows account so it creates its data folder, then restart Sentinel. NINA and Sentinel must run as the same Windows user. |
| Install, update or remove fails with "The plugin file could not be written…" | Close NINA completely and try again. If NINA is closed, check antivirus or a read-only file. |
| "The NINA plugin is not included in this build." | Reinstall Sentinel from the official installer. |
| "Installed for an older NINA plugin version…" | Close NINA and click **Reinstall Plugin**. |

### The panel or instructions do not appear in NINA

1. Restart NINA — a plugin installed while NINA was open is not loaded until the next start.
2. Check the plugin is listed in NINA's installed plugins as **PFR Sentinel**. NINA 3.x loads plugins from a numbered folder under `%LOCALAPPDATA%\NINA\Plugins\` (normally `3.0.0`; the folder is named after NINA's plugin compatibility level, not the NINA version you run); the plugin lives in a `PFRSentinel.NINA` folder there. Reinstall from Sentinel if it is missing.
3. If the plugin is listed but the panel or instructions are missing, part of it failed to load. NINA does not show an error for this; look in NINA's log files in `%LOCALAPPDATA%\NINA\Logs\` — the failing component is named there.

### Start/Stop unavailable (panel)

The panel always explains why the buttons are disabled:

| Message | Fix |
|---------|-----|
| …the capture control API is switched off in Sentinel. | Turn on **Enable Capture Control API** on Sentinel's NINA & ASCOM tab, then click **Reconnect**. |
| …Sentinel is running but capture control is not wired up. | Restart Sentinel. |
| …Sentinel rejected the control token. | Click **Regenerate** next to **API Token** in Sentinel, then **Reconnect**. |
| …no capture control token is configured. | Turn on **Enable Capture Control API** to generate one. |
| …capture control could not be reached. / …Sentinel has not been reached yet. | Check Sentinel is running with **Enable Web Server** on, and that nothing is blocking the port. |
| Waiting for Sentinel to confirm the last command… | A Start or Stop is in progress; it can take up to about 45 seconds with the default wait. |

### Validation issues and failed steps (sequencer)

| Issue | Fix |
|-------|-----|
| PFR Sentinel's capture control API is switched off. Enable it on Sentinel's Output tab. | Turn on **Enable Capture Control API**. It is now on the NINA & ASCOM tab; the Output tab's **Web Server** card has an **Open** button that takes you there. |
| PFR Sentinel is running but capture control is not wired up. Restart Sentinel. | Restart Sentinel. |
| PFR Sentinel rejected the control token. Regenerate it on Sentinel's Output tab. | Click the regenerate button next to **API Token** (now on the NINA & ASCOM tab). |
| …not reachable… Check Sentinel is running with its web server enabled, and that the host and port match. | Start Sentinel and turn on **Enable Web Server**. |
| PFR Sentinel refused the request's Host header… | Capture control accepts calls from the same machine only. See [Capture Control API](Capture-Control-API#host-allow-list). |
| PFR Sentinel did not answer in time. | Check Sentinel is running and not blocked by a firewall. |
| Sentinel configuration not found… | Sentinel is not installed for this Windows user on this PC. |
| Timed out waiting for capture to reach 'running'… | The camera took longer than **Wait up to (s)**. Raise it. |

### The pause trigger pauses, or never pauses

The trigger's status line in the sequencer, and NINA's log (`PFR Sentinel: paused …`, `PFR Sentinel: conditions OK again …`), say why. *New in the next release.*

| Status line | Fix |
|-------------|-----|
| "No verdict: … not reachable …", "… rejected the control token …", "… capture control API is switched off …" | Same fixes as the validation issues above. The trigger pauses on these while **No verdict = bad** is on. |
| "No verdict: … has no /conditions endpoint (HTTP 404) …" | Sentinel is older than the plugin. Update Sentinel. |
| "No verdict: imaging conditions are switched off in Sentinel" | Turn imaging conditions on in Sentinel — see [Imaging Conditions](Imaging-Conditions). |
| "No verdict: Sentinel has not judged a frame recently …" | Sentinel's capture stopped or stalled. Check the panel's **Health** line and start capture. |
| "No verdict: Sentinel is still gathering its first good frames" | Normal after Sentinel starts or after a gap in frames. A Sentinel running headless, with no frames reaching its imaging-conditions check, stays here and then turns stale — with **No verdict = bad** on, the trigger pauses rather than treating that as OK. |
| "Not OK: the sky looks good again; Sentinel is waiting for a few good frames in a row" | Normal: Sentinel only says OK again after several good frames in a row. The trigger's **Settle (s)** time starts after that. |
| "OK to image. latest frame: …" | The latest frame was bad, but Sentinel waits for a few bad frames in a row before saying "not OK", so imaging continues. Your own limits still apply to that frame's figures. |
| "Not OK: …" | The sky is bad by Sentinel's rules or yours; the rest of the line names which. Loosen your limits on the item, or Sentinel's own on its [Imaging Conditions](Imaging-Conditions) settings. |
| Pauses more or less often than you'd like | Pick a different **Sensitivity** (Relaxed / Balanced / Strict) on Sentinel's [Imaging Conditions](Imaging-Conditions#sensitivity) card, or adjust the numbers under **Advanced**. |
| Not sure NINA is asking at all | On Sentinel's Live Monitoring page, the conditions panel (click the **IMAGING** tile) shows "NINA last checked … ago", or "NINA hasn't checked in yet". It turns amber when NINA's request was refused. *New in the next release.* |
| Never pauses although the sky is bad | Check the trigger is on a container that holds the exposures. With **Only before exposures** on it checks only before NINA's own exposure instructions (Take Exposure, Take Many Exposures, Smart Exposure); an imaging instruction from another plugin may not count, so turn the setting off if you use one. A limit is skipped while its measurement is missing or while Sentinel is learning this sky — the line ends with a note saying so. |

### No target marker on the all-sky view

Work down the list:

| Check | Fix |
|-------|-----|
| The all-sky overlay is off, or the **NINA Target** card's **Show NINA target** is off. | Turn them on on the All-Sky page. See [All-Sky Overlay](All-Sky-Overlay#nina-target). |
| The lens is not calibrated. | The target is placed through the lens calibration like every other label. Calibrate first. |
| **Enable Capture Control API** is off in Sentinel. | Turn it on on the NINA & ASCOM tab. The plugin uses the same token as Start/Stop. |
| No target is running. | The marker follows the *running* target. A sequence that is built but not started, or is between targets, has none. The plugin also waits about 15 seconds after NINA starts before its first report. |
| NINA stopped reporting. | Sentinel hides the marker about 2 minutes after the last report (**Hide after (s)** on the All-Sky page). Check NINA is still running the sequence. |
| **Send the current sequencer target to Sentinel** is off. | Turn it on on the plugin's options page in NINA. |
| The target is below the horizon. | Nothing is drawn until it rises. |
| NINA's log says "this Sentinel does not accept targets from NINA (HTTP 404)". | Sentinel is older than the plugin. Update Sentinel; the plugin tries again every 10 minutes, or at once when you change an option on its options page. |
| NINA's log has another "PFR Sentinel: could not send the NINA target" warning. | The rest of the message is the same advice as the Start/Stop messages in the tables above. A rejected token, a refused Host or switched-off control is retried less and less often, up to every 10 minutes; after fixing it, change any option on the plugin's options page (or restart NINA) to retry at once. |

A reticle instead of a field-of-view box means one of four things: **Show field of view** is off on the NINA Target card; the plugin could not work out the field (check the focal length in NINA's telescope settings and that the camera is connected); part of the field is below the horizon; or the box would be under 4 pixels across on your output image. The overlay is drawn once, on the output image after [Image Resize](Image-Processing#image-resize), and the preview is that same image, so a reticle in the preview means a reticle in the saved, web and timelapse images too. To get a box, raise the **Scale** under Image Resize towards 100% — see [All-Sky Overlay](All-Sky-Overlay#nina-target).

#### What the logs say

Both logs record each step at their default level, so you can follow a target from NINA to the overlay. Each line is written when something changes, not every frame or every 30-second update.

| Log | Line | Meaning |
|-----|------|---------|
| NINA | `PFR Sentinel: target reporting to Sentinel is on.` | The plugin loaded and the option is on. |
| NINA | `PFR Sentinel: no deep-sky target is running in the sequencer …` | Nothing to send yet, for example while Target Scheduler waits for darkness. |
| NINA | `PFR Sentinel: sent target 'M31' to Sentinel.` | The target reached Sentinel. "without a field of view" at the end means Sentinel will draw a reticle. |
| NINA | `PFR Sentinel: the target is sent without a field of view …` | Names the missing setting: telescope focal length, camera pixel size or sensor size. |
| Sentinel | `NINA target set via HTTP API: 'M31' RA … Dec …, FOV …, PA …` | Sentinel received the target, with the field of view and rotation it will draw. |
| Sentinel | `NINA target refused (HTTP 400): …` | Sentinel rejected what the plugin sent; the rest of the line says why. |
| Sentinel | `NINA target 'M31' drawn on the all-sky overlay with its field-of-view box` | Drawn. A reticle line says why there is no box. |
| Sentinel | `NINA target 'M31' not drawn on the all-sky overlay: …` | Received but not drawn, for example below the horizon. |
| Sentinel | `NINA target 'M31' hidden: NINA has not reported it for over 120 s` | The updates stopped, so the marker was taken down. |

Sentinel only draws the target while the all-sky overlay itself is shown, so outside the observing window (twilight, a closed roof, no stars) there is no "drawn" line; the overlay's own "Sky features suppressed" line says why.

### "Switched off" versus "not wired up"

Both show up as the same HTTP error (503), but they need opposite fixes, and Sentinel labels them separately:

| Code | Meaning | What to do |
|------|---------|------------|
| `control_disabled` | The capture control API is turned off, so there is no token. | Turn on **Enable Capture Control API** on the NINA & ASCOM tab. |
| `control_unavailable` | Control is on and the token is fine, but Sentinel's capture controls aren't connected to the server (this shouldn't happen). | Restart Sentinel. If it persists, send a [Diagnostics Export](Diagnostics-Export). |

The plugin and the script helpers read this code and show the right advice for each.

### Sentinel on another machine

Capture control is designed for NINA and Sentinel on the same PC. Control calls are accepted only from the same machine by default, and the plugin reads its token from the local Sentinel settings, so Start/Stop and the sequencer instructions will not work against a Sentinel on another PC. You can still view the frame and health remotely: set Sentinel's web server **Host** so it is reachable on your network (see [Web Server](Web-Server)) and enter that address in the plugin's **Sentinel base URL override**.
