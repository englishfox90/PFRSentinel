#!/bin/sh
# Build PFR Sentinel.app and a drag-to-Applications .dmg on an Apple-silicon Mac.
#
#   ./build_sentinel_macos.sh
#
# Needs Homebrew. Uses ./venv when it exists (create it with
# `python3.13 -m venv venv && . venv/bin/activate && pip install -r requirements.txt`),
# otherwise the python on PATH, which is how CI runs it.
#
# The app is ad-hoc signed, not notarized. A copy built here runs as-is; one
# downloaded from GitHub is quarantined by macOS, see docs/wiki/macOS.md.
set -eu

cd "$(dirname "$0")"

if [ -f venv/bin/activate ]; then
    . venv/bin/activate
fi

if [ "$(uname -s)" != "Darwin" ]; then
    echo "FAIL: this builds the macOS app and must run on a Mac" >&2
    exit 1
fi

if ! brew list libusb >/dev/null 2>&1; then
    brew install libusb
fi

python -m pip install --quiet -r requirements-build.txt

python scripts/ci/check_spec_parses.py
python scripts/ci/stage_zwo_sdk_macos.py
python -m PyInstaller --noconfirm PFRSentinel.spec

app="dist/PFR Sentinel.app"
if [ ! -d "$app" ]; then
    echo "FAIL: PyInstaller produced no $app" >&2
    exit 1
fi

version=$(python -c "import version; print(version.__version__)")
dmg="dist/PFRSentinel-$version-macos-arm64.dmg"

# A fresh staging folder each time: ditto merges into an existing one, which
# would leave files from an older build inside the new image.
staging="dist/dmg-staging"
rm -rf "$staging"
mkdir -p "$staging"
ditto "$app" "$staging/PFR Sentinel.app"
ln -s /Applications "$staging/Applications"
hdiutil create -volname "PFR Sentinel" -srcfolder "$staging" -ov -format UDZO "$dmg"
rm -rf "$staging"

echo "Built $app"
echo "Built $dmg"
