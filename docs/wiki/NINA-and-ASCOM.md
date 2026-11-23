# NINA & ASCOM

> **New in the next release** — not available in version 3.8.0 or earlier.

The **NINA & ASCOM** tab, under **Automation** in the sidebar, gathers everything PFR Sentinel tells NINA and other ASCOM software. Earlier versions spread these over the Output and Image Processing tabs. Your settings carry over unchanged.

The tab has four cards, in the order you would set them up:

| Card | What it does | More |
|---|---|---|
| **NINA Plugin** | Installs, updates or removes the PFR Sentinel plugin in NINA. Windows only, like NINA itself. | [NINA Integration](NINA-Integration) |
| **Control API** | **Enable Capture Control API** and the **API Token** the plugin and scripts use to read imaging conditions and to start or stop capture. It runs on the web server, so the card warns you when **Enable Web Server** (Output tab) is off. **Show** reveals the token; the two icon buttons beside it copy it and make a new one. | [Capture Control API](Capture-Control-API) |
| **Imaging Conditions** | Whether the sky is good enough to image, so a NINA sequence pauses under cloud and resumes when it clears. Holds the on/off switch, the sun limit, the **Sensitivity** preset and its **Advanced** numbers, and **Reset baseline**. **Live view** opens the conditions panel on Live Monitoring. | [Imaging Conditions](Imaging-Conditions) |
| **Roof Safety Monitor** | The roof state for NINA's safety checks: the **Alpaca SafetyMonitor** Sentinel serves, and the **ASCOM safety file** for NINA's GenericFile safety monitor (with its **File path**). Both read the roof classifier, so the card says so when [ML Models](ML-Models) are off. | [ML Models](ML-Models#alpaca-safety-monitor) |

The Output tab's **Web Server** card and the Image Processing tab's **ML Models** card keep a one-line note with an **Open** button that brings you here, for anyone who looks in the old place. The NINA plugin's own messages still mention the Output tab; that note is how they lead here.
