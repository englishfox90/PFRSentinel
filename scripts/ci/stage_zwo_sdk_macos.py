"""Stage the ZWO ASI SDK for a macOS (Apple-silicon) build of PFR Sentinel.

ZWO's arm64 library comes from INDI's mirror of the official SDK, pinned to a
commit and checked against its git blob hash, so a build can never pick up a
different binary silently. ZWO licenses it under MIT, so redistributing it in
the app is allowed; the licence ships beside it.

As shipped, the library links ``@rpath/libusb-1.0.0.dylib`` but carries no
rpath of its own. INDI's drivers supply one; Python doesn't, so loading it
fails with "Library not loaded: @rpath/libusb-1.0.0.dylib". This script copies
Homebrew's libusb next to it, adds ``@loader_path`` as an rpath (so libusb is
found in the same folder, wherever the app is), and re-signs both: arm64 macOS
refuses to load a library whose signature no longer matches its bytes.

Output (default ``build/zwo-macos/``), which PFRSentinel.spec bundles:
    libASICamera2.dylib, libusb-1.0.0.dylib, ZWO-LICENSE.txt, libusb-COPYING.txt

Run on a Mac with Homebrew and ``brew install libusb``:
    python scripts/ci/stage_zwo_sdk_macos.py
"""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import os
import shutil
import subprocess
import sys
import urllib.request

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_OUT = os.path.join(REPO_ROOT, "build", "zwo-macos")

# indilib/indi-3rdparty, "fix_macos_libs: refresh ad-hoc code signatures after
# rewriting load commands" (2026-10-11), ZWO Camera SDK v1.41.
INDI_COMMIT = "f5e7166564d1afea311faf622d368afbe14bd841"
_RAW = f"https://raw.githubusercontent.com/indilib/indi-3rdparty/{INDI_COMMIT}/libasi"
SDK_SOURCES = {
    "libASICamera2.dylib": (f"{_RAW}/mac_arm64/libASICamera2.bin",
                            "edacbe21d28c23414cda6bfc1e9d00deb01afea6"),
    "ZWO-LICENSE.txt": (f"{_RAW}/license.txt",
                        "d1c94ef47e1dd1d7278d5baf7876d38eb7e55e36"),
}

CAMERA_LIB = "libASICamera2.dylib"
LIBUSB = "libusb-1.0.0.dylib"


def git_blob_sha1(data: bytes) -> str:
    """The hash git (and GitHub's contents API) gives a file with these bytes."""
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def fetch_verified(url: str, expected_blob_sha: str) -> bytes:
    with urllib.request.urlopen(url, timeout=60) as response:
        data = response.read()
    actual = git_blob_sha1(data)
    if actual != expected_blob_sha:
        raise SystemExit(f"FAIL: {url} has blob hash {actual}, expected {expected_blob_sha}")
    return data


def _run(*argv: str) -> str:
    result = subprocess.run(argv, capture_output=True, text=True)
    if result.returncode != 0:
        raise SystemExit(f"FAIL: {' '.join(argv)}\n{result.stderr.strip()}")
    return result.stdout


def homebrew_libusb() -> tuple[str, str | None]:
    """Homebrew's libusb dylib and its licence file (None if absent)."""
    prefix = _run("brew", "--prefix", "libusb").strip()
    dylib = os.path.realpath(os.path.join(prefix, "lib", LIBUSB))
    if not os.path.isfile(dylib):
        raise SystemExit(f"FAIL: {dylib} not found — run `brew install libusb`")
    licence = os.path.join(prefix, "COPYING")
    return dylib, licence if os.path.isfile(licence) else None


def rpaths(path: str) -> list[str]:
    lines = _run("otool", "-l", path).splitlines()
    found = []
    for i, line in enumerate(lines):
        if line.strip() == "cmd LC_RPATH":
            for follow in lines[i + 1:i + 4]:
                parts = follow.split()
                if parts and parts[0] == "path":
                    found.append(parts[1])
    return found


def stage(out_dir: str) -> None:
    os.makedirs(out_dir, exist_ok=True)
    for name, (url, blob) in SDK_SOURCES.items():
        with open(os.path.join(out_dir, name), "wb") as f:
            f.write(fetch_verified(url, blob))
        print(f"[OK] {name} (verified against INDI {INDI_COMMIT[:7]})")

    libusb_src, libusb_licence = homebrew_libusb()
    camera = os.path.join(out_dir, CAMERA_LIB)
    libusb = os.path.join(out_dir, LIBUSB)
    shutil.copyfile(libusb_src, libusb)
    os.chmod(libusb, 0o644)
    if libusb_licence:
        shutil.copyfile(libusb_licence, os.path.join(out_dir, "libusb-COPYING.txt"))
    print(f"[OK] {LIBUSB} from {libusb_src}")

    # libusb is found by its install name, so make that the @rpath form the
    # camera library asks for.
    _run("install_name_tool", "-id", f"@rpath/{LIBUSB}", libusb)
    if "@loader_path" not in rpaths(camera):
        _run("install_name_tool", "-add_rpath", "@loader_path", camera)
    for lib in (libusb, camera):
        _run("codesign", "--force", "--sign", "-", lib)

    for lib in (camera, libusb):
        archs = _run("lipo", "-archs", lib).split()
        if "arm64" not in archs:
            raise SystemExit(f"FAIL: {lib} has no arm64 slice ({archs})")

    # The real test: the camera library loads with libusb resolved from its
    # own folder.
    ctypes.CDLL(camera)
    print(f"[OK] {CAMERA_LIB} loads with {LIBUSB} beside it -> {out_dir}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", default=DEFAULT_OUT)
    args = parser.parse_args()
    if sys.platform != "darwin":
        print("FAIL: this stages macOS libraries and must run on a Mac", file=sys.stderr)
        return 1
    stage(args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
