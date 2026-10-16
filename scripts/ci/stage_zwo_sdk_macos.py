"""Stage the ZWO ASI SDK for a macOS (Apple-silicon) build of PFR Sentinel.

The source is the committed universal ``libASICamera2.dylib`` in the project
root (ZWO's official V1.41 build, kept current by scripts/update_zwo_sdk.py).
It serves a source checkout as it stands, but can't be bundled as-is: ZWO's
arm64 slice links libusb at an absolute Homebrew path
(``/opt/homebrew/opt/libusb/lib/libusb-1.0.0.dylib``), which a Mac without
Homebrew doesn't have.

So this script takes the arm64 slice, copies Homebrew's libusb beside it,
rewrites the camera library's libusb reference to ``@rpath/...`` with
``@loader_path`` as its rpath (libusb is then found in the same folder,
wherever the app is), and re-signs both: arm64 macOS refuses to load a
library whose signature no longer matches its bytes. ZWO licenses the SDK
under MIT, so shipping it is allowed; the licence goes with it.

Output (default ``build/zwo-macos/``), which PFRSentinel.spec bundles:
    libASICamera2.dylib, libusb-1.0.0.dylib, ZWO-LICENSE.txt, libusb-COPYING.txt

Run on a Mac with Homebrew and ``brew install libusb``:
    python scripts/ci/stage_zwo_sdk_macos.py
"""
from __future__ import annotations

import argparse
import ctypes
import os
import shutil
import subprocess
import sys
from typing import Optional

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_OUT = os.path.join(REPO_ROOT, "build", "zwo-macos")
SOURCE_LIB = os.path.join(REPO_ROOT, "libASICamera2.dylib")
ZWO_LICENCE = os.path.join(REPO_ROOT, "assets", "licenses", "ZWO-ASI-SDK-LICENSE.txt")

CAMERA_LIB = "libASICamera2.dylib"
LIBUSB = "libusb-1.0.0.dylib"
ARCH = "arm64"


def _run(*argv: str) -> str:
    result = subprocess.run(argv, capture_output=True, text=True)
    if result.returncode != 0:
        raise SystemExit(f"FAIL: {' '.join(argv)}\n{result.stderr.strip()}")
    return result.stdout


def libusb_dependency(otool_l_output: str) -> Optional[str]:
    """The libusb load path in ``otool -L`` output, or None if it has none."""
    for line in otool_l_output.splitlines()[1:]:
        path = line.strip().split(" (compatibility", 1)[0]
        if os.path.basename(path).startswith("libusb-1.0"):
            return path
    return None


def rpaths(otool_l_output: str) -> list[str]:
    """LC_RPATH entries in ``otool -l`` output."""
    lines = otool_l_output.splitlines()
    found = []
    for i, line in enumerate(lines):
        if line.strip() == "cmd LC_RPATH":
            for follow in lines[i + 1:i + 4]:
                parts = follow.split()
                if parts and parts[0] == "path":
                    found.append(parts[1])
    return found


def homebrew_libusb() -> tuple[str, Optional[str]]:
    """Homebrew's libusb dylib and its licence file (None if absent)."""
    prefix = _run("brew", "--prefix", "libusb").strip()
    dylib = os.path.realpath(os.path.join(prefix, "lib", LIBUSB))
    if not os.path.isfile(dylib):
        raise SystemExit(f"FAIL: {dylib} not found — run `brew install libusb`")
    licence = os.path.join(prefix, "COPYING")
    return dylib, licence if os.path.isfile(licence) else None


def stage(out_dir: str) -> None:
    if not os.path.isfile(SOURCE_LIB):
        raise SystemExit(f"FAIL: {SOURCE_LIB} is missing — it is committed; is this a full checkout?")
    os.makedirs(out_dir, exist_ok=True)
    camera = os.path.join(out_dir, CAMERA_LIB)
    libusb = os.path.join(out_dir, LIBUSB)

    if ARCH not in _run("lipo", "-archs", SOURCE_LIB).split():
        raise SystemExit(f"FAIL: {SOURCE_LIB} has no {ARCH} slice")
    if os.path.isfile(camera):
        os.remove(camera)
    _run("lipo", SOURCE_LIB, "-thin", ARCH, "-output", camera)
    shutil.copyfile(ZWO_LICENCE, os.path.join(out_dir, "ZWO-LICENSE.txt"))
    print(f"[OK] {CAMERA_LIB}: {ARCH} slice of the committed library")

    libusb_src, libusb_licence = homebrew_libusb()
    shutil.copyfile(libusb_src, libusb)
    os.chmod(libusb, 0o644)
    if libusb_licence:
        shutil.copyfile(libusb_licence, os.path.join(out_dir, "libusb-COPYING.txt"))
    print(f"[OK] {LIBUSB} from {libusb_src}")

    # libusb is matched by install name, so give it the @rpath form the camera
    # library will now ask for.
    _run("install_name_tool", "-id", f"@rpath/{LIBUSB}", libusb)
    current = libusb_dependency(_run("otool", "-L", camera))
    if current is None:
        raise SystemExit(f"FAIL: {CAMERA_LIB} doesn't link libusb — has ZWO changed the build?")
    if current != f"@rpath/{LIBUSB}":
        _run("install_name_tool", "-change", current, f"@rpath/{LIBUSB}", camera)
    if "@loader_path" not in rpaths(_run("otool", "-l", camera)):
        _run("install_name_tool", "-add_rpath", "@loader_path", camera)
    for lib in (libusb, camera):
        _run("codesign", "--force", "--sign", "-", lib)

    if ARCH not in _run("lipo", "-archs", libusb).split():
        raise SystemExit(f"FAIL: Homebrew's libusb has no {ARCH} slice")

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
