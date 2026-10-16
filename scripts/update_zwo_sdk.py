"""
Update the macOS ZWO SDK library
================================

Builds the universal (arm64 + x86_64) ``libASICamera2.dylib`` in the project
root from whatever ZWO ships: a universal file, or separate Intel and Apple
Silicon builds. Slices for other architectures (ZWO still ships i386) are
dropped, since current macOS cannot load them.

Before replacing the project file it loads the result once per architecture
and checks both slices report the same SDK version.

Usage (macOS only):
    python scripts/update_zwo_sdk.py <dylib-or-folder> [<dylib-or-folder> ...]

A folder is searched recursively for libASICamera2.dylib, so the unpacked SDK
can be passed as-is.

Where ZWO ships the Mac builds: zwoastro.com -> Software -> Product SDK ->
"ASI Camera SDK", listed as Windows only. Its ASI_Camera_SDK.zip carries every
platform; ASI_linux_mac_SDK_V<x.yy>.tar.bz2 inside it holds lib/mac (Intel)
and lib/mac_arm64 (Apple Silicon). Unpack that and pass its lib folder.
"""

import argparse
import os
import shutil
import subprocess
import sys
import tempfile

LIB_NAME = "libASICamera2.dylib"
REQUIRED_ARCHS = ("arm64", "x86_64")
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Run in a fresh interpreter per architecture: one process can only load its own slice.
VERSION_PROBE = (
    "import ctypes, sys\n"
    "lib = ctypes.CDLL(sys.argv[1])\n"
    "lib.ASIGetSDKVersion.restype = ctypes.c_char_p\n"
    "print(lib.ASIGetSDKVersion().decode())\n"
)


def run(*cmd):
    return subprocess.run(cmd, check=True, capture_output=True, text=True).stdout.strip()


def find_libraries(paths):
    found = []
    for path in paths:
        if os.path.isdir(path):
            for root, _dirs, files in os.walk(path):
                if LIB_NAME in files:
                    found.append(os.path.join(root, LIB_NAME))
        elif os.path.isfile(path):
            found.append(path)
        else:
            sys.exit(f"Not found: {path}")
    if not found:
        sys.exit(f"No {LIB_NAME} found in: {', '.join(paths)}")
    return found


def extract_slices(libraries, workdir):
    """Return {arch: thin dylib path}, taking each required arch from the first file that has it."""
    slices = {}
    for lib in libraries:
        archs = run("lipo", "-archs", lib).split()
        print(f"  {lib}: {' '.join(archs)}")
        for arch in archs:
            if arch not in REQUIRED_ARCHS:
                continue
            if arch in slices:
                print(f"    {arch} already taken from {slices[arch][1]}, ignoring this one")
                continue
            thin = os.path.join(workdir, f"{arch}.dylib")
            if len(archs) == 1:
                shutil.copyfile(lib, thin)
            else:
                run("lipo", lib, "-thin", arch, "-output", thin)
            slices[arch] = (thin, lib)
    missing = [arch for arch in REQUIRED_ARCHS if arch not in slices]
    if missing:
        sys.exit(f"No {', '.join(missing)} build found; pass the file that has it too.")
    return {arch: thin for arch, (thin, _src) in slices.items()}


def sdk_version(lib, arch, workdir):
    # macOS attaches one slice's code signature per file; once a slice has been loaded,
    # loading the other from the same file fails with errno 37. Probe a fresh copy.
    probe_copy = os.path.join(workdir, f"probe-{arch}.dylib")
    shutil.copyfile(lib, probe_copy)
    result = subprocess.run(
        ["arch", f"-{arch}", "/usr/bin/python3", "-I", "-c", VERSION_PROBE, probe_copy],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        detail = result.stderr.strip().splitlines()
        sys.exit(f"The {arch} slice will not load: {detail[-1] if detail else 'unknown error'}")
    return result.stdout.strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[1])
    parser.add_argument("sources", nargs="+", help=f"{LIB_NAME} files or folders containing them")
    parser.add_argument("--output", default=os.path.join(PROJECT_ROOT, LIB_NAME),
                        help="where to write the universal library (default: project root)")
    args = parser.parse_args()

    if sys.platform != "darwin":
        sys.exit("This script needs macOS (lipo and arch).")

    print("Source libraries:")
    libraries = find_libraries(args.sources)

    with tempfile.TemporaryDirectory() as workdir:
        slices = extract_slices(libraries, workdir)
        universal = os.path.join(workdir, LIB_NAME)
        run("lipo", "-create", *(slices[arch] for arch in REQUIRED_ARCHS), "-output", universal)

        versions = {arch: sdk_version(universal, arch, workdir) for arch in REQUIRED_ARCHS}
        for arch, version in versions.items():
            print(f"  {arch} loads, SDK {version}")
        if len(set(versions.values())) != 1:
            sys.exit("The slices report different SDK versions; use builds from the same release.")

        shutil.move(universal, args.output)

    print(f"Wrote {args.output} ({run('lipo', '-archs', args.output)}), SDK {versions['arm64']}")


if __name__ == "__main__":
    main()
