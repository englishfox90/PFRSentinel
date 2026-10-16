# PFR Sentinel on macOS

> **New in the next release** — not available in version 3.7.9 or earlier.

PFR Sentinel runs on Apple-silicon Macs (M1 and later) as a regular app, `PFR Sentinel.app`. Windows remains the main platform; on a Mac a few Windows features are missing (see [What's different](#whats-different)).

## Requirements

- An Apple-silicon Mac (M1 or later). Intel Macs are not supported by the packaged app.
- For ZWO camera mode: nothing extra. The ZWO camera library and the USB library it needs are inside the app.
- For timelapse: ffmpeg, from [Homebrew](https://brew.sh) with `brew install ffmpeg`.

## Installing

1. Download the `.dmg` (see below for where).
2. Open it and drag **PFR Sentinel** onto **Applications**.
3. The app is not notarized by Apple yet, so the first time you open a downloaded copy macOS says it "cannot be opened" or "is damaged". Clear the download flag once in Terminal, then open the app normally:

   ```bash
   xattr -dr com.apple.quarantine "/Applications/PFR Sentinel.app"
   ```

   A copy you built yourself with `build_sentinel_macos.sh` doesn't need this.

Dev builds are published with the Windows ones: the newest is always at the [dev-latest prerelease](https://github.com/englishfox90/PFRSentinel/releases/tag/dev-latest) as `PFRSentinel-dev-unsigned-macos-arm64.dmg`, and each build is announced, with its download link, in the "Dev builds" thread under [Discussions](https://github.com/englishfox90/PFRSentinel/discussions). There is no macOS release build yet.

## Where things are kept

Settings, logs and calibration live in `~/Library/Application Support/PFRSentinel`. Images are saved to `~/Library/Application Support/PFRSentinel/Images` unless you choose another folder on the [Output Settings](Output-Settings) tab.

To move a Windows setup to the Mac, copy `config.json` (and `allsky_calibration.json` if the camera and lens stay on the same mount) from `%LOCALAPPDATA%\PFRSentinel` on the PC into that folder. The camera library path in the copied settings points at a Windows file; PFR Sentinel notices and switches to the library inside the app on its own. Folder paths you chose on Windows (output, watch folder) need setting again.

## Running unattended

For an observatory Mac that should look after itself:

1. In PFR Sentinel, turn on **Start at login** and **Auto-start capture on launch** on the [Settings](Settings#start-at-login) tab. If PFR Sentinel crashes, macOS starts it again after 30 seconds.
2. In **System Settings → Users & Groups**, turn on automatic login for the account PFR Sentinel runs under, so the Mac logs in by itself after a power cut. (Automatic login isn't available while FileVault is on.)
3. In **System Settings → Energy**, turn on **Start up automatically after a power failure**, and stop the Mac from sleeping.

## What's different

- **USB camera reset**: Windows can reset a stuck USB camera; macOS has no way for an app to do that. Camera recovery still reconnects and, if needed, restarts PFR Sentinel, but a camera wedged at the USB level needs unplugging.
- **NINA**: NINA only runs on Windows, so the NINA plugin, the NINA sequencer scripts and anything that comes from NINA are not available.
- **Look**: the app uses the same Fluent design as on Windows, with the Mac's own fonts, so it doesn't match pixel for pixel.
