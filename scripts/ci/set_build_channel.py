"""Stamp the build channel into services/dev_mode_config.py before freezing.

``DEV_MODE_AVAILABLE`` is what this project already means by a dev versus a
production build: it gates raw FITS/TIFF debug exports, calibration JSON
dumps and the ML prediction integration. Until now, flipping it was item 1 on
a printed checklist at the end of ``build_sentinel.bat`` — which is to say, a
thing a human remembers or doesn't.

The ``PFRSENTINEL_DEV_MODE`` environment override cannot do this job. It is
read when ``dev_mode_config`` is imported, which in a frozen app happens on
the user's machine at launch, not on the runner at build time. Setting it in
a CI job would change nothing about the artifact. The flag has to be written
into the source before PyInstaller reads it.

Rewrites the assignment in place and re-reads the file to confirm the value
took. Not idempotent-by-accident: it fails loudly if the assignment is gone,
rather than silently producing a build with the wrong features compiled in.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
TARGET = REPO_ROOT / "services" / "dev_mode_config.py"

CHANNELS = {"dev": True, "production": False}

# Only the top-level assignment, not the two reassignments inside the
# environment-override branch below it (those are indented).
_ASSIGNMENT = re.compile(r"^DEV_MODE_AVAILABLE\s*=\s*(True|False)[^\n]*$", re.MULTILINE)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("channel", choices=sorted(CHANNELS))
    args = parser.parse_args()

    wanted = CHANNELS[args.channel]
    source = TARGET.read_text(encoding="utf-8")

    matches = _ASSIGNMENT.findall(source)
    if len(matches) != 1:
        print(f"FAIL: expected exactly one top-level DEV_MODE_AVAILABLE assignment "
              f"in {TARGET}, found {len(matches)}. The build channel can no longer "
              f"be set reliably — fix this script and the file together.",
              file=sys.stderr)
        return 1

    previous = matches[0]
    replacement = (f"DEV_MODE_AVAILABLE = {wanted}  "
                   f"# set by scripts/ci/set_build_channel.py ({args.channel} build)")
    TARGET.write_text(_ASSIGNMENT.sub(replacement, source, count=1), encoding="utf-8")

    confirmed = _ASSIGNMENT.findall(TARGET.read_text(encoding="utf-8"))
    if confirmed != [str(wanted)]:
        print(f"FAIL: wrote the {args.channel} channel but the file reads back as "
              f"{confirmed}.", file=sys.stderr)
        return 1

    print(f"Build channel: {args.channel} "
          f"(DEV_MODE_AVAILABLE {previous} -> {wanted})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
