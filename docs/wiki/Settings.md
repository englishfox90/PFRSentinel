# Settings

The Settings tab holds application-wide options: the accent colour, how PFR Sentinel runs in the background and at Windows logon, anonymous usage data, the OpenWeatherMap connection, and update checking. It opens full width (the live preview is hidden while it is open). Settings are saved as you change them.

---

## Appearance

| Setting | Default | Description |
|---------|---------|-------------|
| **Accent** | Iris | Six colour swatches (hover one to see its name): **Iris**, **Nebula**, **Aurora**, **Solar**, **Nova**, and **Forest**. The accent colours buttons, switches, the selected navigation item, and other highlights. The change applies immediately. The app always uses its dark theme. |

---

## System

| Setting | Default | Description |
|---------|---------|-------------|
| **Enable System Tray** | Off | Run in the background. When on, a PFR Sentinel icon appears in the Windows notification area, closing the main window hides it to the tray instead of quitting, and a launch at Windows logon starts hidden in the tray. When off, closing the window quits the app and a logon launch opens the window normally. |
| **Start with Windows** | Off | Launch PFR Sentinel automatically when you log on to Windows. |
| **Auto-start capture on launch** | On | When PFR Sentinel is launched at logon by **Start with Windows**, begin capturing with the saved camera or watch folder automatically. |
| **Automatic camera recovery** | On | How hard PFR Sentinel works to get a ZWO camera back after a fault. On: the full [camera recovery](Capture-Settings#camera-recovery) process, including USB resets and an app restart. Off: one plain reconnect, then capture stops until you start it again. New in the next release. |
| **Send Anonymous Usage Data** | On | Share anonymous usage information, error reports, and warning/error log messages with the developer. See [Anonymous Usage Data](#anonymous-usage-data). |

### System Tray

Turning on **Enable System Tray** takes effect immediately: the tray icon appears and the window stays open until you close it. Turning it off removes the tray icon.

Right-click the tray icon for these options:

| Menu item | Description |
|-----------|-------------|
| **Show Window** | Restores the main window. Shown only while the window is hidden, and the icon's default action. |
| **Hide Window** | Hides the main window to the tray. Shown only while the window is visible. |
| **Start Capture** | Starts capture. Available when capture is stopped. |
| **Stop Capture** | Stops capture. Available while capturing. |
| **Exit** | Shuts PFR Sentinel down completely. |

With the tray enabled, closing the window does not stop capture or any outputs; use **Exit** from the tray menu to quit. Launching PFR Sentinel again while it is already running brings the existing window to the front instead of starting a second copy.

### Start with Windows

**Start with Windows** registers a Windows scheduled task named "PFR Sentinel Autostart" that runs when you log on. The task runs with the highest privileges available to your account, so the app can start with Administrator rights (needed for full USB camera recovery) without a permission prompt at every logon.

- Creating or removing the task usually needs Administrator approval once, so expect a Windows permission (UAC) prompt when you change the setting. If you decline, the switch turns itself back off (or on) to match the real state.
- A message at the top of the window confirms "PFR Sentinel will start with Windows." or "PFR Sentinel will no longer start with Windows."
- The switch always reflects whether the scheduled task actually exists, even if it was changed outside the app.

How the logon launch behaves depends on the other two switches:

| Enable System Tray | Auto-start capture on launch | At logon |
|--------------------|------------------------------|----------|
| On | On | Starts hidden in the tray and begins capturing |
| On | Off | Starts hidden in the tray, capture stopped |
| Off | On | Opens the window and begins capturing |
| Off | Off | Opens the window, capture stopped |

For an unattended observatory PC, turn on **Start with Windows** and **Auto-start capture on launch** so capture resumes by itself after a reboot or power cut. Turn **Enable System Tray** off if you want to see the window after logon (for example, over remote desktop) rather than having it tucked away in the tray.

Changing **Enable System Tray** or **Auto-start capture on launch** while **Start with Windows** is on updates the scheduled task, which may show the permission prompt again. **Auto-start capture on launch** only affects launches at logon; opening the app from its shortcut does not start capture automatically.

### Anonymous Usage Data

**Send Anonymous Usage Data** is on by default. Your images and your settings file are never sent, and no account or name is attached: each installation is identified only by a random ID created on first use.

When it is on, PFR Sentinel sends:

- **Usage events**: that the app started (version, whether it is running as Administrator) and shut down, which tabs you open, when capture starts and stops (capture mode, camera model, which outputs and features are turned on, the number of overlays and which overlay tokens they use, and how many images were processed), timelapse recordings, [YouTube](YouTube-Uploads) timelapse uploads, Discord and notification posts, auto-exposure calibration results, whether weather is configured, and when an update is available.
- **Installation details**, recorded at startup against the random ID: the version first installed, the operating system, the current app version, and whether the app is running as Administrator.
- **Error reports**: the type and message of errors, with the technical stack trace showing where in the program they happened, including unexpected crashes.
- **Warning and error log messages**: a copy of each WARN and ERROR line from the application log, exactly as written. INFO and DEBUG messages are never sent.

Error messages and log lines are sent as written, so they can contain details such as folder paths (which often include your Windows user name), camera names, or web addresses.

Turning the setting off stops all of this immediately. If you turn it back on, usage events and error reports resume straight away. Log messages also resume straight away if the setting was on when PFR Sentinel started; if it was off at startup, they resume the next time PFR Sentinel starts.

---

## Discord Alerts and Storage Cleanup

These two cards are pointers only. Discord settings and storage cleanup are configured on the [Output](Output-Settings) tab. See also [Discord Integration](Discord-Integration).

---

## Weather API

Weather data is used for overlay tokens, the Weather tile in the status strip, Discord posts, and cloud cover in the [Image Library](Image-Library). PFR Sentinel uses OpenWeatherMap and caches results for 10 minutes. For a full walkthrough see [Weather Setup](Weather-Setup).

| Setting | Description |
|---------|-------------|
| **Get free API key** | Opens the OpenWeatherMap API page in your browser, where you can sign up for a free key. |
| **API Key** | Your OpenWeatherMap API key. Hidden by default; click **Show** / **Hide** to toggle. |
| **Test** | Fetches the current weather with the values entered and shows the result below the key, e.g. "✓ Clear, 12.0°C". Errors include "API key required", "Location or coordinates required", and "No data returned". |
| **Location** | City name, e.g. "London, UK". Leave blank to use coordinates instead. |
| **Coordinates** | **Latitude**, **Longitude**, and **Elevation (m)** of your observatory. An alternative to a city name, and more precise. |
| **Units** | **Metric (°C, m/s)** or **Imperial (°F, mph)**. Known issue: choosing **Imperial** has no effect in the current version. It is always saved as metric, and **Test** ignores it too. See [Weather Setup](Weather-Setup#units). |

Latitude and longitude can be typed in decimal degrees (`31.33`, `-100.46`) or degrees-minutes-seconds (`31 32 51`, `31:32:51`, `100 27 25 W`). When you leave the field, the value is converted to decimal degrees. South and West are negative. If the value can't be understood, the field gets a red border, the message "Invalid coordinate — use decimal degrees (e.g. 31.33 or -100.46)" appears, and the value is not saved.

The coordinates are also used for the sunset/sunrise recording window on the [Timelapse](Timelapse) tab and by the [All-Sky Overlay](All-Sky-Overlay), which uses the elevation for atmospheric refraction correction. Setting coordinates is recommended even if you also enter a city name.

---

## About & Updates

The card shows your current version (also shown in the window title and at the bottom-right of the window).

| Control | Description |
|---------|-------------|
| **Check for Updates** | Checks GitHub for a newer release straight away. If you are up to date, an "Up to Date" message confirms the version you are running. |
| **GitHub Releases** | Opens the releases page on GitHub. |

PFR Sentinel also checks automatically a few seconds after it starts (skipped if it already checked in the last 24 hours) and once more after it has been running for 24 hours.

When a newer version is found:

- A notification "Update available: v..." is added to the bell in the app bar, and a **!** badge appears on **Settings** in the navigation rail until you open this tab.
- If the window is hidden in the tray, it is brought back on screen so you can see the dialog.
- An **Update Available** dialog shows the release notes and the installer file name and size, with three buttons:
  - **Download Update** downloads the installer with a progress bar, then changes to **Run Installer**, which closes PFR Sentinel and starts the installer.
  - **View on GitHub** opens the release page.
  - **Skip This Version** closes the dialog.

---

## Notes

- There is no developer mode switch in Settings. Developer-only features (such as saving raw debug frames) are not available in the standard release.
- When PFR Sentinel shuts down it stops capture, finishes any timelapse video, and closes the camera cleanly. If shutdown gets stuck for more than 90 seconds (for example, a camera driver that stops responding), the app forces itself to close so the next launch or an installer upgrade isn't blocked.
