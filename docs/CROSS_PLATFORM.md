# Cross-Platform Support

PFR Sentinel supports Windows, macOS, and Linux platforms. This document covers platform-specific details and installation instructions.

## Supported Platforms

| Feature | Windows | macOS | Linux |
|---------|---------|-------|-------|
| Core Application | ✅ Full | ✅ Full | ✅ Full |
| ZWO ASI Cameras | ✅ Full | ✅ Full | ✅ Full |
| ASCOM Alpaca | ✅ Full | ✅ Full | ✅ Full |
| ASCOM COM | ✅ Only | ❌ N/A | ❌ N/A |
| RTSP Streaming | ✅ Full | ✅ Full | ✅ Full |
| System Tray | ✅ Full | ✅ Full | ✅ Full |

## Configuration Locations

### Windows
- Config: `%LOCALAPPDATA%\PFRSentinel\config.json`
- Logs: `%APPDATA%\PFRSentinel\logs\`
- Output: `%LOCALAPPDATA%\PFRSentinel\Images\`

### macOS
- Config: `~/Library/Application Support/PFRSentinel/config.json`
- Logs: `~/Library/Logs/PFRSentinel/`
- Output: `~/Library/Application Support/PFRSentinel/Images/`

### Linux
- Config: `~/.local/share/PFRSentinel/config.json` (or `$XDG_DATA_HOME/PFRSentinel/`)
- Logs: `~/.local/share/PFRSentinel/logs/`
- Output: `~/.local/share/PFRSentinel/Images/`

## ZWO ASI SDK Installation

### Windows
The SDK (`ASICamera2.dll`) is typically bundled with the application. If not:
1. Download from [ZWO Downloads](https://astronomy-imaging-camera.com/software-drivers)
2. Place `ASICamera2.dll` in the application directory or configure path in Capture settings

### macOS
Install the ZWO ASI SDK library:

```bash
# Option 1: Homebrew (if available)
brew install zwo-sdk

# Option 2: Manual installation
# Download from ZWO website and copy to:
# - /usr/local/lib/libASICamera2.dylib
# - or /opt/homebrew/lib/libASICamera2.dylib (Apple Silicon)
```

**Note**: On macOS, you may need to grant camera permissions in System Preferences > Privacy & Security > Camera.

### Linux
Install the ZWO ASI SDK library:

```bash
# Option 1: Install to system location
sudo cp libASICamera2.so /usr/local/lib/
sudo ldconfig

# Option 2: Configure udev rules for USB access
sudo cp 99-asi.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules

# Ensure user is in appropriate groups
sudo usermod -aG plugdev $USER
```

**udev rules (99-asi.rules)**:
```
SUBSYSTEM=="usb", ATTR{idVendor}=="03c3", MODE="0666"
```

## ffmpeg Installation (for RTSP)

### Windows
1. Download from https://www.gyan.dev/ffmpeg/builds/
2. Extract to `C:\ffmpeg`
3. Add `C:\ffmpeg\bin` to PATH

### macOS
```bash
brew install ffmpeg
```

### Linux
```bash
# Debian/Ubuntu
sudo apt install ffmpeg

# Fedora
sudo dnf install ffmpeg

# Arch
sudo pacman -S ffmpeg
```

## ASCOM Support

### ASCOM Alpaca (Cross-Platform)
The recommended approach for cross-platform astronomy equipment control:

1. Install `alpyca` Python package: `pip install alpyca`
2. Ensure your ASCOM device has Alpaca support enabled
3. Configure the Alpaca host/port in PFR Sentinel settings

**Popular Alpaca-enabled software:**
- NINA (N.I.N.A.)
- PHD2
- Device Hub
- ASCOM Remote Server

### ASCOM COM (Windows Only)
Traditional ASCOM COM interface for local drivers:
1. Install ASCOM Platform 6.6+ from https://ascom-standards.org
2. Install `comtypes` package: `pip install comtypes`

## Python Environment Setup

### All Platforms
```bash
# Create virtual environment
python -m venv venv

# Activate
# Windows:
venv\Scripts\activate
# macOS/Linux:
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### macOS-Specific Notes
- On Apple Silicon (M1/M2/M3), ensure you're using an ARM64 Python build
- Some packages may require Rosetta for Intel compatibility

### Linux-Specific Notes
- Ensure `python3-dev` and `python3-pip` are installed
- Qt6 requires appropriate system libraries:
  ```bash
  sudo apt install libxcb-xinerama0 libxcb-cursor0
  ```

## Building Installers

### Windows

Windows uses PyInstaller for the executable and Inno Setup for the installer:

```powershell
# Build executable only
.\build_sentinel.bat

# Build installer (includes executable)
.\build_sentinel_installer.bat
```

**Output:**
- Executable: `dist\PFRSentinel.exe`
- Installer: `releases\PFRSentinel_vX.X.X_Setup.exe`

### macOS

macOS uses PyInstaller for the .app bundle and create-dmg for the disk image:

```bash
# First time setup
chmod +x build_macos*.sh scripts/create_icns.sh

# Option 1: Build .app only
./build_macos.sh

# Option 2: Build DMG only (requires .app)
./build_macos_dmg.sh

# Option 3: Full build (version sync + app + DMG)
./build_macos_installer.sh
```

**Requirements:**
- Xcode Command Line Tools: `xcode-select --install`
- Homebrew: `/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"`
- create-dmg: `brew install create-dmg`
- PyInstaller: `pip install pyinstaller`

**Icon Creation:**
```bash
# Convert PNG to .icns (run on macOS only)
./scripts/create_icns.sh assets/app_icon.png
```

**Output:**
- App Bundle: `dist/PFR Sentinel.app`
- Disk Image: `releases/PFRSentinel_vX.X.X.dmg`

**Code Signing (Optional):**
```bash
# Sign with Developer ID
export CODESIGN_IDENTITY="Developer ID Application: Your Name (TEAMID)"
./build_macos_dmg.sh
```

### Linux

Linux uses PyInstaller for a single executable:

```bash
source venv/bin/activate
pyinstaller PFRSentinel.spec
```

**Output:**
- Executable: `dist/PFRSentinel`

For distribution, consider creating a `.deb` or `.rpm` package, or an AppImage.

## Troubleshooting

### Camera Not Detected (All Platforms)
1. Check USB connection
2. Verify SDK library is installed and path configured
3. Check user permissions (Linux: udev rules, macOS: camera permissions)

### SDK Library Not Found
- Windows: Place `ASICamera2.dll` in app directory
- macOS: Install to `/usr/local/lib/libASICamera2.dylib`
- Linux: Install to `/usr/local/lib/libASICamera2.so` and run `ldconfig`

### Permission Denied (Linux)
```bash
# Add user to plugdev group
sudo usermod -aG plugdev $USER
# Log out and back in for changes to take effect
```

### Qt Platform Plugin Error (Linux)
```bash
# Install required Qt plugins
sudo apt install qt6-base-dev libqt6-svg6
```
