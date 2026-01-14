#!/bin/bash
# =============================================================================
# Create macOS .icns icon from PNG source
#
# Usage:
#   ./scripts/create_icns.sh [input.png]
#
# If no input is specified, uses assets/app_icon.png
#
# Requirements:
#   - macOS (uses iconutil)
#   - sips (built into macOS)
#   - Source PNG should be 1024x1024 or larger
# =============================================================================

set -e

# Input PNG
INPUT_PNG="${1:-assets/app_icon.png}"
OUTPUT_ICNS="assets/app_icon.icns"

if [[ ! -f "$INPUT_PNG" ]]; then
    echo "ERROR: Input PNG not found: $INPUT_PNG"
    echo "Usage: $0 [input.png]"
    exit 1
fi

echo "Creating macOS icon from: $INPUT_PNG"

# Create iconset directory
ICONSET="assets/app_icon.iconset"
rm -rf "$ICONSET"
mkdir -p "$ICONSET"

# Generate all required sizes
# macOS requires specific sizes for the iconset
echo "Generating icon sizes..."

sips -z 16 16     "$INPUT_PNG" --out "$ICONSET/icon_16x16.png"      2>/dev/null
sips -z 32 32     "$INPUT_PNG" --out "$ICONSET/icon_16x16@2x.png"   2>/dev/null
sips -z 32 32     "$INPUT_PNG" --out "$ICONSET/icon_32x32.png"      2>/dev/null
sips -z 64 64     "$INPUT_PNG" --out "$ICONSET/icon_32x32@2x.png"   2>/dev/null
sips -z 128 128   "$INPUT_PNG" --out "$ICONSET/icon_128x128.png"    2>/dev/null
sips -z 256 256   "$INPUT_PNG" --out "$ICONSET/icon_128x128@2x.png" 2>/dev/null
sips -z 256 256   "$INPUT_PNG" --out "$ICONSET/icon_256x256.png"    2>/dev/null
sips -z 512 512   "$INPUT_PNG" --out "$ICONSET/icon_256x256@2x.png" 2>/dev/null
sips -z 512 512   "$INPUT_PNG" --out "$ICONSET/icon_512x512.png"    2>/dev/null
sips -z 1024 1024 "$INPUT_PNG" --out "$ICONSET/icon_512x512@2x.png" 2>/dev/null

echo "  ✓ Generated all icon sizes"

# Convert iconset to icns
echo "Converting to .icns format..."
iconutil -c icns "$ICONSET" -o "$OUTPUT_ICNS"

# Cleanup
rm -rf "$ICONSET"

echo "  ✓ Created: $OUTPUT_ICNS"
echo
echo "Done! Icon ready for macOS build."
