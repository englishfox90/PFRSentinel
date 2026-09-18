# NINA Integration

PFR Sentinel can work hand in hand with N.I.N.A. A small plugin, bundled with Sentinel and installed from the **Output** tab, adds a **PFR Sentinel** panel to NINA showing the live pier camera frame and Sentinel's health, plus two Advanced Sequencer instructions — **Start Sentinel Capture** and **Stop Sentinel Capture** — so capture follows your observing session automatically. Everything runs through Sentinel's [Capture Control API](Capture-Control-API), and the plugin pairs itself with Sentinel: there is no address or token to type into NINA.

---

## What You Get

### The PFR Sentinel panel

A panel in NINA's **Imaging** tab, titled **PFR Sentinel**:

| Element | What it shows |
|---------|---------------|
| Live frame | The latest image from Sentinel's web server, refreshed every few seconds while the panel is visible. When Sentinel reports the frame as stale (no new frame for 5 minutes), the image is dimmed and marked **STALE**. |
| Frame line | How old the last frame is and its file name. |
| **Health** | Sentinel's overall capture health — **OK**, **IDLE**, **DEGRADED**, **RECOVERING** or **ERROR** — with Sentinel's own explanation underneath when there is one. See [Capture Control API](Capture-Control-API#health-block) for what each means. |
| Statistics | **Capture** (state and mode), **Interval**, **Last frame**, **Next frame**, **Frame age**, **Images served**, **Uptime**. A **Recovery** row appears only while the camera is recovering or has needed recovery attempts. |
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

---

## Requirements

| Requirement | Detail |
|-------------|--------|
| PFR Sentinel | Version 3.7.0 or later, installed from the official installer (the plugin is bundled with it). |
| NINA | NINA 3.x. The plugin is built against and tested with NINA 3.2. |
| Same machine | NINA and Sentinel must run on the same PC, under the same Windows user account. See [Sentinel on another machine](#sentinel-on-another-machine). |
| Web server | **Enable Web Server** turned on in the **Web Server** card of the [Output Settings](Output-Settings) tab. |
| Capture control | **Enable Capture Control API** turned on in the same card. The live frame and health work without it, but Start/Stop and the sequencer instructions need it. |

---

## Setup

1. In Sentinel, open the **Output** tab.
2. In the **Web Server** card, turn on **Enable Web Server**.
3. In the same card, turn on **Enable Capture Control API**. Sentinel generates an API token the first time. You do not need to copy it — the plugin reads it for you. If you enable control while the web server is off, Sentinel warns you: "Capture control needs the web server — enable it above."
4. Close NINA.
5. In the **NINA Plugin** card (just below **Web Server**), click **Install Plugin**.
6. Start NINA. Open the **PFR Sentinel** panel from the Imaging tab, and look for the **PFR Sentinel** category in the Advanced Sequencer's instruction list.

---

## Installing, Updating and Removing

The **NINA Plugin** card on the Output tab manages the plugin. Its **Status** line reports what Sentinel found:

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

NINA's options page for the PFR Sentinel plugin has one setting, **Sentinel base URL override** (for example `http://192.168.1.20:8080`). Leave it empty on the observatory PC. It changes only the address the plugin talks to — the token still comes from the local Sentinel settings — and it applies within a few seconds without a restart. The address must start with `http://` or `https://`.

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
| 3 | Control API switched off or no token, or control not wired up | Enable **Enable Capture Control API** on the Output tab; if it is already on, restart Sentinel |
| 4 | Sentinel not reachable | Check Sentinel is running with the web server enabled |
| 5 | Token rejected, or request refused by the Host check | Regenerate the token on the Output tab; run the script on the same machine as Sentinel |
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
| …the capture control API is switched off in Sentinel. | Turn on **Enable Capture Control API** on Sentinel's Output tab, then click **Reconnect**. |
| …Sentinel is running but capture control is not wired up. | Restart Sentinel. |
| …Sentinel rejected the control token. | Click **Regenerate** next to **API Token** in Sentinel, then **Reconnect**. |
| …no capture control token is configured. | Turn on **Enable Capture Control API** to generate one. |
| …capture control could not be reached. / …Sentinel has not been reached yet. | Check Sentinel is running with **Enable Web Server** on, and that nothing is blocking the port. |
| Waiting for Sentinel to confirm the last command… | A Start or Stop is in progress; it can take up to about 45 seconds with the default wait. |

### Validation issues and failed steps (sequencer)

| Issue | Fix |
|-------|-----|
| PFR Sentinel's capture control API is switched off. Enable it on Sentinel's Output tab. | Turn on **Enable Capture Control API**. |
| PFR Sentinel is running but capture control is not wired up. Restart Sentinel. | Restart Sentinel. |
| PFR Sentinel rejected the control token. Regenerate it on Sentinel's Output tab. | Click **Regenerate**. |
| …not reachable… Check Sentinel is running with its web server enabled, and that the host and port match. | Start Sentinel and turn on **Enable Web Server**. |
| PFR Sentinel refused the request's Host header… | Capture control accepts calls from the same machine only. See [Capture Control API](Capture-Control-API#host-allow-list). |
| PFR Sentinel did not answer in time. | Check Sentinel is running and not blocked by a firewall. |
| Sentinel configuration not found… | Sentinel is not installed for this Windows user on this PC. |
| Timed out waiting for capture to reach 'running'… | The camera took longer than **Wait up to (s)**. Raise it. |

### "Switched off" versus "not wired up"

Both show up as the same HTTP error (503), but they need opposite fixes, and Sentinel labels them separately:

| Code | Meaning | What to do |
|------|---------|------------|
| `control_disabled` | The capture control API is turned off, so there is no token. | Turn on **Enable Capture Control API** on the Output tab. |
| `control_unavailable` | Control is on and the token is fine, but Sentinel's capture controls aren't connected to the server (this shouldn't happen). | Restart Sentinel. If it persists, send a [Diagnostics Export](Diagnostics-Export). |

The plugin and the script helpers read this code and show the right advice for each.

### Sentinel on another machine

Capture control is designed for NINA and Sentinel on the same PC. Control calls are accepted only from the same machine by default, and the plugin reads its token from the local Sentinel settings, so Start/Stop and the sequencer instructions will not work against a Sentinel on another PC. You can still view the frame and health remotely: set Sentinel's web server **Host** so it is reachable on your network (see [Web Server](Web-Server)) and enter that address in the plugin's **Sentinel base URL override**.
