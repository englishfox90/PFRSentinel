# Logs

The Logs tab shows application messages from every part of PFR Sentinel (capture, image processing, outputs, all-sky calibration, and so on) as they happen. It is also where you export a diagnostics bundle for a bug report. The live preview stays visible on the left while this tab is open.

---

## Controls

A control bar at the top of the tab contains the following:

| Control | Default | Description |
|---------|---------|-------------|
| **Level** | INFO | The least severe level to show, along with everything more severe. **DEBUG** shows every message, **INFO** shows everything except DEBUG, **WARN** shows warnings and errors, and **ERROR** shows errors only. Your choice is remembered between sessions. |
| Search | Empty | Shows only messages containing the text you type (not case-sensitive). Combined with the level filter, so a message must match both. While a search is active, the number of matching lines is shown next to the box. |
| **Auto-scroll** | On | Keeps the view scrolled to the newest message. Turn it off to read back through older messages without the view jumping. Turning it back on jumps to the bottom. |
| **Clear** | | Empties the log view. The log files on disk are not affected. |
| **Open Folder** | | Opens the log folder in File Explorer. |
| **Export Diagnostics** | | New in the next release (not in version 3.7.6 or earlier). Builds a ZIP of recent logs, a redacted copy of your settings, the all-sky calibration, and a raw frame, ready to attach to a GitHub issue. See [Diagnostics Export](Diagnostics-Export). |

### Filtering past and new messages

> **New in the next release** — not available in version 3.7.8 or earlier.

Changing the level or the search text re-filters the messages already received, and new messages that arrive afterwards are filtered the same way. For example, typing `NINA` with the level at **DEBUG** shows every NINA message from the last 10,000 lines, then adds each new one as it is logged. Clearing the search box brings the full view back. The search reaches further back than the 1,000 lines the view holds, so a rare message pushed out by DEBUG lines can still be found.

In version 3.7.8 and earlier, filters only applied to messages arriving after the change, so you had to click **Clear** to start with a filtered view. The **Level** list also offered **Info+** and **All**, and **INFO**, **WARN**, **ERROR** and **DEBUG** each showed only that one level. A saved **All** now opens as **DEBUG**, and **Info+** opens as **INFO**.

---

## Log View

Messages appear in a read-only, monospace view in the form `[HH:MM:SS] LEVEL: message`. Long lines do not wrap; scroll sideways to read them.

The view keeps the most recent 1,000 lines. Older lines are dropped from the view as new ones arrive, but remain in the log file on disk. **Clear** empties the view and the 10,000 lines the filters search, so cleared messages don't come back when you change a filter.

### Colour Coding

| Level | Colour |
|-------|--------|
| ERROR | Red |
| WARN | Amber |
| DEBUG | Muted grey |
| INFO | Grey |

The **Recent Activity** card on the [Live Monitoring](Live-Monitoring) panel shows a shorter feed of the same messages (the last 100, without DEBUG).

---

## Log Files on Disk

Log files are stored in:

```
%LOCALAPPDATA%\PFRSentinel\logs
```

In version 3.7.6 and earlier, logs are in `%APPDATA%\PFRSentinel\logs`. The first time the new version starts, it writes one line to the log saying where log files are now stored and naming the old folder. Logs already in the old folder are left where they are and are not deleted, so you can remove that folder yourself once you no longer need them.

- The current log is `sentinel.log`. Every message is written to it, including DEBUG messages, whatever level filter is selected on the tab.
- The log rolls over at midnight. The previous day's file is renamed with its date (for example `sentinel.log.2026-09-04`).
- Old log files are deleted after 7 days. PFR Sentinel also removes any dated log files older than 7 days each time it starts. In version 3.7.6 and earlier, this start-up clean-up did not find the log files, so old logs could build up.
- Each line in the file has a full date and time, e.g. `[2026-09-05 22:14:03] INFO     - Capture started`.

The footer at the bottom of the log view shows the full path of the current log file (`...\logs\sentinel.log`). In version 3.7.6 and earlier, the footer named a `watchdog.log` file that is never written; the real file is `sentinel.log`. Use **Open Folder** to go straight to the log folder.

When asking for help, attach a [diagnostics bundle](Diagnostics-Export) rather than copying lines from the view; it includes the last 3 days of log files automatically. In version 3.7.6 and earlier, which has no diagnostics bundle, click **Open Folder** and attach `sentinel.log` instead.

---

## Resource Lines

> **New in the next release** — not available in version 3.7.8 or earlier.

PFR Sentinel writes a `Resources:` line to the log at start-up, when capture starts or stops, and whenever the app's memory use moves noticeably (about every 30 minutes otherwise). It shows two memory figures that mean different things:

- **working set** is the number Task Manager's Memory column shows. Windows leaves pages resident after a frame is processed even though the app has finished with them, so this figure sits near the peak of the last frame and drops when the app asks Windows to trim it (after capture stops). It is not a measure of what the app is holding.
- **private** is the memory the app has actually claimed. If this figure rises line after line while capture runs, something is holding on to frames; if it stays flat while the working set is high, nothing is being retained.

The rest of the line gives the CPU share since the previous line and how many frames the all-sky calibration, meteor detection and timelapse queues are holding. A **Memory growth** warning appears if the private figure keeps rising for most of an hour. When reporting a memory problem, attach a [diagnostics bundle](Diagnostics-Export): the log lines and the latest figures are both included.

---

## Notes

- If **Send Anonymous Usage Data** is on in [Settings](Settings), warning and error messages are also sent to the developer's analytics service. Turning that setting off stops this.
- Error messages can also be posted to Discord or other notification channels if you enable error posting on the [Output](Output-Settings) tab.
