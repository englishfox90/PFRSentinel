#!/bin/bash
# =============================================================================
# Complete build script for PFR Sentinel macOS
# Builds executable AND creates DMG installer
#
# Usage:
#   ./build_macos_installer.sh
#
# This script:
#   1. Syncs version from version.py
#   2. Builds the .app bundle
#   3. Creates the DMG installer
# =============================================================================

set -e  # Exit on error

echo "========================================"
echo "  PFR Sentinel - Full macOS Build"
echo "========================================"
echo

# Check if we're on macOS
if [[ "$(uname)" != "Darwin" ]]; then
    echo "ERROR: This script must be run on macOS"
    exit 1
fi

# Step 0: Update version in spec file
echo "[0/2] Syncing version from version.py..."
python3 scripts/update_macos_version.py
if [[ $? -ne 0 ]]; then
    echo "WARNING: Version sync failed, continuing with existing version..."
fi
echo

# Step 1: Build application
echo "[1/2] Building application..."
./build_macos.sh
if [[ $? -ne 0 ]]; then
    echo "ERROR: Application build failed!"
    exit 1
fi

# Step 2: Create DMG
echo "[2/2] Creating DMG installer..."
./build_macos_dmg.sh
if [[ $? -ne 0 ]]; then
    echo "ERROR: DMG creation failed!"
    exit 1
fi

echo
echo "========================================"
echo "  Full Build Completed Successfully!"
echo "========================================"
echo
echo "Build artifacts:"
echo "  - App:       dist/PFR Sentinel.app"
echo "  - Installer: dist/PFRSentinel_*_macOS.dmg"
echo
echo "Ready for distribution!"
echo
