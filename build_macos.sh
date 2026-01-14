#!/bin/bash
# =============================================================================
# Build script for PFR Sentinel macOS Application
# Creates a .app bundle using PyInstaller
#
# Usage:
#   ./build_macos.sh
#
# Requirements:
#   - Python 3.10+ (recommend 3.11 or 3.12)
#   - Virtual environment with dependencies installed
#   - Xcode Command Line Tools (xcode-select --install)
#
# Optional:
#   - libASICamera2.dylib in project root for ZWO camera support
#   - Apple Developer ID for code signing
# =============================================================================

set -e  # Exit on error

echo "========================================"
echo "  PFR Sentinel - macOS Build"
echo "========================================"
echo

SPEC_FILE="PFRSentinel_macOS.spec"

# Check if we're on macOS
if [[ "$(uname)" != "Darwin" ]]; then
    echo "ERROR: This script must be run on macOS"
    exit 1
fi

# Detect architecture
ARCH=$(uname -m)
echo "Building for architecture: $ARCH"
if [[ "$ARCH" == "arm64" ]]; then
    echo "  (Apple Silicon detected)"
else
    echo "  (Intel Mac detected)"
fi
echo

# Activate virtual environment if it exists
if [[ -d "venv" ]]; then
    echo "Activating virtual environment..."
    source venv/bin/activate
elif [[ -d ".venv" ]]; then
    echo "Activating virtual environment (.venv)..."
    source .venv/bin/activate
else
    echo "WARNING: Virtual environment not found"
    echo "Continuing with system Python..."
fi

# Verify Python version
PYTHON_VERSION=$(python3 --version 2>&1 | cut -d' ' -f2)
echo "Python version: $PYTHON_VERSION"
echo

# Check for required tools
echo "Checking dependencies..."

if ! command -v pyinstaller &> /dev/null; then
    echo "ERROR: PyInstaller not found. Install with: pip install pyinstaller"
    exit 1
fi
echo "  ✓ PyInstaller found"

# Check for app icon
if [[ ! -f "assets/app_icon.icns" ]]; then
    echo "  ⚠ Warning: assets/app_icon.icns not found"
    echo "    Will use default icon. See scripts/create_icns.sh to create one."
else
    echo "  ✓ App icon found"
fi

# Check for ZWO SDK
if [[ -f "libASICamera2.dylib" ]]; then
    echo "  ✓ ZWO SDK found (libASICamera2.dylib)"
else
    echo "  ⚠ ZWO SDK not found - camera support requires manual installation"
fi

# Check for ML models
if [[ -f "ml/models/roof_classifier_v1.onnx" ]]; then
    echo "  ✓ ML models found"
else
    echo "  ⚠ ML models not found - ML features will be disabled"
fi

echo

# Clean old build artifacts
echo "Cleaning old build artifacts..."
rm -rf build/PFRSentinel 2>/dev/null || true
rm -rf "dist/PFR Sentinel.app" 2>/dev/null || true
rm -rf dist/PFRSentinel 2>/dev/null || true

# Create entitlements file if it doesn't exist
if [[ ! -f "macos/entitlements.plist" ]]; then
    echo "Creating entitlements file..."
    mkdir -p macos
    cat > macos/entitlements.plist << 'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>com.apple.security.cs.allow-jit</key>
    <true/>
    <key>com.apple.security.cs.allow-unsigned-executable-memory</key>
    <true/>
    <key>com.apple.security.cs.disable-library-validation</key>
    <true/>
    <key>com.apple.security.device.camera</key>
    <true/>
    <key>com.apple.security.device.usb</key>
    <true/>
</dict>
</plist>
EOF
    echo "  ✓ Created macos/entitlements.plist"
fi

echo
echo "Building application with PyInstaller..."
echo

python3 -m PyInstaller "$SPEC_FILE" --noconfirm

if [[ $? -ne 0 ]]; then
    echo
    echo "ERROR: Build failed!"
    exit 1
fi

echo
echo "========================================"
echo "  Build completed successfully!"
echo "========================================"
echo
echo "Application location:"
echo "  dist/PFR Sentinel.app"
echo
echo "To run the application:"
echo "  open \"dist/PFR Sentinel.app\""
echo
echo "To create a DMG installer, run:"
echo "  ./build_macos_dmg.sh"
echo
