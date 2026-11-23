# PFR Sentinel

**Live Camera Monitoring & Overlay System for Observatories**

[![PFR Sentinel Discord](https://img.shields.io/badge/Discord-PFR%20Sentinel-%235865F2.svg?logo=discord&logoColor=white)](https://discord.gg/ED4KFAPevz)
[![My Discord (702660989438656592)](https://img.shields.io/badge/My-Discord-%235865F2.svg)](https://discord.com/users/702660989438656592)

A modern astrophotography application with a Fluent Design UI (PySide6 + qfluentwidgets) that watches directories for new images or captures directly from ZWO ASI cameras or ASCOM Alpaca cameras on the network, adding customizable metadata overlays with weather data and serving output through multiple channels.

**Current Version:** 3.8.0

The user guide is the [project wiki](https://github.com/englishfox90/PFRSentinel/wiki) (source in `docs/wiki/`).

---

## Key Features

### Capture Modes
- **Directory Watch Mode**: Monitor folders for new images from any camera/software
- **ZWO Camera Mode**: Direct capture from ZWO ASI cameras with auto-exposure and debayering
- **Alpaca Camera Mode** (next release): Capture from any camera served by an ASCOM Alpaca server (for example ASCOM Remote), with Discover and Test Connection

### Output Modes (can run simultaneously)
- **File Output**: Save processed images to disk
- **Web Server**: HTTP server with `/latest` (image) and `/status` (JSON) endpoints
- **Discord Integration**: Periodic image posts with weather data embeds

### NINA & ASCOM
- **NINA plugin**: live pier-camera panel and Start/Stop Sentinel Capture sequencer instructions; from the next release (plugin 1.3.0.0) also a trigger and a loop condition that pause or end imaging when the sky turns bad
- **Imaging conditions** (next release): a per-frame "good enough to image?" verdict at `GET /conditions`, learned against your own clear sky
- **Roof safety**: the ML roof state as an ASCOM Alpaca SafetyMonitor and as a file for NINA's GenericFile safety monitor
- All of these are set up on the **NINA & ASCOM** page (next release; on the Output and Image Processing pages in 3.8.0)

### Processing
- Auto-stretch (MAD-based histogram stretching)
- Customizable text overlays with metadata tokens
- Weather data integration (OpenWeatherMap)
- Resize and format options (PNG/JPEG)

### User Interface
- Modern Fluent Design UI (Windows 11 styling)
- Live monitoring panel with preview, RGB histogram, and mini-log
- System tray support with notifications
- Command-line automation support (`--headless`, `--auto-start`, `--tray`)

---

## Installation

### Windows Installer (Recommended)

Download `PFRSentinel_Setup.exe` from the `releases/` folder and run it.

**Self-contained** - includes Python runtime, all dependencies, and ZWO ASI SDK.

**Optional:**
- **ffmpeg** - Needed for timelapse recording
- **OpenWeatherMap API Key** - For weather data overlays

### Running from Source

```powershell
git clone <repository-url>
cd PFRSentinel
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
python main.py
```

---

## Quick Start

1. **Choose Capture Mode** (Capture page):
   - **Directory Watch**: Select folder to monitor for new images
   - **ZWO Camera**: Click "Detect", configure exposure/gain
   - **Alpaca Camera** (next release): Click "Discover" or type the server's host, then "Test Connection"

2. **Configure Outputs** (Output page):
   - Enable desired outputs (File, Web, Discord)
   - Configure settings for each

3. **Add Overlays** (Overlays page):
   - Add text overlays with tokens like `{CAMERA}`, `{EXPOSURE}`, `{WEATHER}`

4. **Monitor** (Live Monitoring page):
   - View live preview, histogram, and recent logs

5. **Connect NINA** (NINA & ASCOM page, next release):
   - Install the NINA plugin and turn on the Capture Control API

---

## Command Line Options

```powershell
python main.py --auto-start              # Auto-start capture
python main.py --auto-stop 3600          # Stop after N seconds
python main.py --headless                # No GUI (server mode)
python main.py --tray                    # Start minimized to tray
```

---

## Project Structure

```
PFRSentinel/
├── ui/                 # PySide6 + qfluentwidgets UI
│   ├── main_window/    # Main window, navigation, capture and output wiring
│   ├── components/     # App bar, status strip, cards
│   ├── panels/         # Pages (layout only)
│   ├── controllers/    # Business logic and threading
│   └── theme/          # Fluent Design theming
├── services/           # Core processing: config, processor, watcher, outputs
│   ├── camera/         # ZWO camera, shared capture loop, alpaca/ (ASCOM Alpaca)
│   ├── imaging_conditions/  # Per-frame imaging verdict for NINA
│   ├── alpaca_safety/  # Alpaca SafetyMonitor server
│   └── allsky/         # All-sky calibration and overlay
├── ml/                 # Scene classifiers (ONNX inference)
├── nina-plugin/        # NINA plugin (C# / .NET 8)
├── docs/wiki/          # User wiki source
├── main.py             # Application entry point
└── version.py          # Version info
```

---

## Configuration

Settings stored in `%LOCALAPPDATA%\PFRSentinel\config.json` on Windows (`~/Library/Application Support/PFRSentinel` on macOS, `~/.local/share/PFRSentinel` on Linux)

Logs in the `logs` folder beside it (7-day rotation)

---

## Overlay Tokens

| Token | Description |
|-------|-------------|
| `{CAMERA}` | Camera name |
| `{EXPOSURE}` | Exposure time |
| `{GAIN}` | Gain value |
| `{TEMP}` | Camera/weather temperature |
| `{FILENAME}` | Original filename |
| `{DATETIME}` | Current date/time |
| `{WEATHER}` | Weather description |
| `{WEATHER_ICON}` | Weather icon emoji |

---

## Community & Support

- **Discord server**: join the [PFR Sentinel Discord](https://discord.gg/ED4KFAPevz) for help, questions and to share your all-sky setup.
- **Message the author**: Paul is [`englishfox3689`](https://discord.com/users/702660989438656592) on Discord.
- **Bugs and feature requests**: open an issue on [GitHub](https://github.com/englishfox90/PFRSentinel/issues).

---

## Troubleshooting

- **No cameras detected**: Verify USB connection, check SDK path in Capture page
- **Alpaca camera not found**: Check the server is running and reachable, and type its address if Discover finds nothing
- **Weather shows N/A**: Configure API key and location in settings
- **Check logs**: Use Logs page or open `%LOCALAPPDATA%\PFRSentinel\logs`

---

## Building

```powershell
.\build_sentinel.bat           # Build executable
.\build_sentinel_installer.bat # Build installer
```

---

## License

MIT License - See [LICENSE](LICENSE) for details.

**Author:** Paul Fox-Reeks

