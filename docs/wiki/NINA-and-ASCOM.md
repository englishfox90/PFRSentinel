# NINA & ASCOM

> **New in the next release** — not available in version 3.8.0 or earlier.

The **NINA & ASCOM** tab, under **Automation** in the sidebar, gathers everything PFR Sentinel tells NINA and other ASCOM software. Earlier versions spread these over the Output and Image Processing tabs. Your settings carry over unchanged.

The tab has four cards, in the order you would set them up:

| Card | What it does | More |
|---|---|---|
| **NINA Plugin** | Installs, updates or removes the PFR Sentinel plugin in NINA. Shown on Windows only, like NINA itself. | [NINA Integration](NINA-Integration) |
| **Control API** | **Enable Capture Control API** and the **API Token** the plugin and scripts use to read imaging conditions and to start or stop capture. It runs on the web server, so the card warns "The web server is off, so NINA cannot reach Sentinel — turn it on in Output → Web Server." while **Enable Web Server** is off. **Show** reveals the token; the two icon buttons beside it copy it ("Copy the token") and make a new one ("Make a new token"). Hover a button for its name. | [Capture Control API](Capture-Control-API) |
| **Imaging Conditions** | Whether the sky is good enough to image, so a NINA sequence pauses under cloud and resumes when it clears. Holds **Judge imaging conditions**, **Image only in** (the sun limit), **Sensitivity** with its **Advanced** numbers, and **Reset baseline**. The line at the top shows the current verdict, and **Live view** opens the conditions panel on Live Monitoring. This card starts open; the others start collapsed. | [Imaging Conditions](Imaging-Conditions) |
| **Roof Safety Monitor** | The roof state for NINA's safety checks: the **Alpaca SafetyMonitor** Sentinel serves, and the **ASCOM safety file** for NINA's GenericFile safety monitor (with its **File path** and **Browse**). Both read the roof classifier — the Alpaca SafetyMonitor can follow the [Imaging Conditions](Imaging-Conditions) grade instead, with its **Safe when** choice (new in the next release) — so while [ML Models](ML-Models) are off the card warns "Needs ML Models — switch them on in Image Processing → ML Models. Until then both read unsafe." | [Alpaca Safety Monitor](ML-Models#alpaca-safety-monitor), [ASCOM Safety File](ML-Models#ascom-safety-file-nina-integration) |

The Output tab's **Web Server** card ("Capture control, the API token and the NINA plugin are on the NINA & ASCOM page.") and the Image Processing tab's **ML Models** card ("Roof safety for NINA (Alpaca SafetyMonitor and the ASCOM safety file) and Imaging Conditions are on the NINA & ASCOM page.") keep a one-line note with an **Open** button that brings you here, for anyone who looks in the old place.

The NINA plugin's messages (version 1.3.0.0, bundled with the next release) point here too, for example "Enable it on Sentinel's NINA & ASCOM page (the Output tab in Sentinel 3.8 and earlier)". The plugin bundled with 3.8.0 still says "Sentinel's Output tab"; from the next release the Output tab's note leads here.

The **Settings in NINA & ASCOM** link at the foot of the [conditions panel on Live Monitoring](Live-Monitoring#imaging-conditions-panel) also brings you here.
