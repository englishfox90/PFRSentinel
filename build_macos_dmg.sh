#!/bin/bash
# =============================================================================
# Create DMG installer for PFR Sentinel macOS Application
# Creates a distributable .dmg file with drag-to-Applications layout
#
# Usage:
#   ./build_macos_dmg.sh
#
# Requirements:
#   - Built .app bundle (run build_macos.sh first)
#   - create-dmg tool: brew install create-dmg
#
# Optional:
#   - Code signing identity for signed DMG
# =============================================================================

set -e  # Exit on error

echo "========================================"
echo "  PFR Sentinel - Create DMG Installer"
echo "========================================"
echo

# Check if we're on macOS
if [[ "$(uname)" != "Darwin" ]]; then
    echo "ERROR: This script must be run on macOS"
    exit 1
fi

# Get version from version.py
VERSION=$(python3 -c "exec(open('version.py').read()); print(__version__)" 2>/dev/null || echo "0.0.0")
echo "Version: $VERSION"
echo

APP_NAME="PFR Sentinel"
APP_PATH="dist/${APP_NAME}.app"
DMG_NAME="PFRSentinel_${VERSION}_macOS"

# Check if app bundle exists
if [[ ! -d "$APP_PATH" ]]; then
    echo "ERROR: Application bundle not found at: $APP_PATH"
    echo "Run ./build_macos.sh first to create the app bundle."
    exit 1
fi

echo "Application bundle found: $APP_PATH"
echo

# Check for create-dmg tool
if ! command -v create-dmg &> /dev/null; then
    echo "create-dmg not found. Installing via Homebrew..."
    if command -v brew &> /dev/null; then
        brew install create-dmg
    else
        echo "ERROR: Homebrew not found. Please install create-dmg manually:"
        echo "  brew install create-dmg"
        echo
        echo "Or install Homebrew first:"
        echo "  /bin/bash -c \"\$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)\""
        exit 1
    fi
fi

# Clean old DMG
rm -f "dist/${DMG_NAME}.dmg" 2>/dev/null || true
rm -rf "dist/dmg_temp" 2>/dev/null || true

echo "Creating DMG installer..."
echo

# Create DMG with Applications shortcut
# This creates a nice drag-to-install DMG
create-dmg \
    --volname "${APP_NAME}" \
    --volicon "assets/app_icon.icns" \
    --window-pos 200 120 \
    --window-size 600 400 \
    --icon-size 100 \
    --icon "${APP_NAME}.app" 150 190 \
    --hide-extension "${APP_NAME}.app" \
    --app-drop-link 450 185 \
    --no-internet-enable \
    "dist/${DMG_NAME}.dmg" \
    "$APP_PATH"

if [[ $? -ne 0 ]]; then
    echo
    echo "ERROR: DMG creation failed!"
    echo
    echo "Trying simpler DMG creation method..."
    
    # Fallback: Simple DMG creation without fancy layout
    mkdir -p "dist/dmg_temp"
    cp -R "$APP_PATH" "dist/dmg_temp/"
    ln -s /Applications "dist/dmg_temp/Applications"
    
    hdiutil create -volname "${APP_NAME}" \
        -srcfolder "dist/dmg_temp" \
        -ov -format UDZO \
        "dist/${DMG_NAME}.dmg"
    
    rm -rf "dist/dmg_temp"
fi

echo
echo "========================================"
echo "  DMG Installer Created Successfully!"
echo "========================================"
echo
echo "Installer location:"
echo "  dist/${DMG_NAME}.dmg"
echo
echo "File size:"
ls -lh "dist/${DMG_NAME}.dmg" | awk '{print "  " $5}'
echo

# Optional: Code sign the DMG
if [[ -n "$CODESIGN_IDENTITY" ]]; then
    echo "Code signing DMG..."
    codesign --force --sign "$CODESIGN_IDENTITY" "dist/${DMG_NAME}.dmg"
    echo "  ✓ DMG signed with: $CODESIGN_IDENTITY"
    echo
fi

# Optional: Notarize (requires Apple Developer account)
if [[ -n "$NOTARIZE_APPLE_ID" ]] && [[ -n "$NOTARIZE_PASSWORD" ]] && [[ -n "$NOTARIZE_TEAM_ID" ]]; then
    echo "Submitting for notarization..."
    xcrun notarytool submit "dist/${DMG_NAME}.dmg" \
        --apple-id "$NOTARIZE_APPLE_ID" \
        --password "$NOTARIZE_PASSWORD" \
        --team-id "$NOTARIZE_TEAM_ID" \
        --wait
    
    echo "Stapling notarization ticket..."
    xcrun stapler staple "dist/${DMG_NAME}.dmg"
    echo "  ✓ DMG notarized and stapled"
    echo
fi

echo "Distribution checklist:"
echo "  1. Test DMG on a clean Mac"
echo "  2. Verify app launches correctly"
echo "  3. Check Gatekeeper doesn't block (if signed)"
echo "  4. Upload to GitHub releases or distribution server"
echo
