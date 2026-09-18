# Logs

The Logs tab shows application messages from every part of PFR Sentinel (capture, image processing, outputs, all-sky calibration, and so on) as they happen. It is also where you export a diagnostics bundle for a bug report. The live preview stays visible on the left while this tab is open.

---

## Controls

A control bar at the top of the tab contains the following:

| Control | Default | Description |
|---------|---------|-------------|
| **Level** | Info+ | Which messages to show. **Info+** shows everything except DEBUG. **All** shows everything. **INFO**, **WARN**, **ERROR**, and **DEBUG** show only that level. Your choice is remembered between sessions. |
| Search | Empty | Shows only messages containing the text you type (not case-sensitive). Combined with the level filter, so a message must match both. |
| **Auto-scroll** | On | Keeps the view scrolled to the newest message. Turn it off to read back through older messages without the view jumping. Turning it back on jumps to the bottom. |
| **Clear** | | Empties the log view. The log files on disk are not affected. |
| **Open Folder** | | Opens the log folder in File Explorer. |
| **Export Diagnostics** | | New in the next release (not in version 3.7.6 or earlier). Builds a ZIP of recent logs, a redacted copy of your settings, the all-sky calibration, and a raw frame, ready to attach to a GitHub issue. See [Diagnostics Export](Diagnostics-Export). |

The level filter and search are applied to messages as they arrive. Messages already on screen are not re-filtered when you change them, so click **Clear** after changing a filter to start with a view that only contains matching messages.

---

## Log View

Messages appear in a read-only, monospace view in the form `[HH:MM:SS] LEVEL: message`. Long lines do not wrap; scroll sideways to read them.

The view keeps the most recent 1,000 lines. Older lines are dropped from the view as new ones arrive, but remain in the log file on disk.

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

## Notes

- If **Send Anonymous Usage Data** is on in [Settings](Settings), warning and error messages are also sent to the developer's analytics service. Turning that setting off stops this.
- Error messages can also be posted to Discord or other notification channels if you enable error posting on the [Output](Output-Settings) tab.
