"""
Turn one image-library night into a calibration buffer dump.

The library (`services/library/`) keeps one finished JPEG per capture — 750 px
longest edge, stretched, overlay text burned in — in per-night folders next
to `library.db`. This script runs the service's own frame step
(`measure_sky_circle` + `detect_stars` + the catalogue above the horizon) on
every open-roof frame of a night and writes the same JSON
`services.allsky.buffer_dump` writes, so `replay_buffer.py` has real skies
to run on before any dev build ships (ALLSKY_HOSTING_SITE_PLAN §7.2).

Timestamps come from `library.db` (`captured_at`, epoch seconds); without a
database they are parsed from the `YYYYMMDD_HHMMSS_<hash>.jpg` names, which
are the PC's local time, so `--tz` names that zone. Frames whose `roof` is
not Open are skipped (a closed roof has no stars to fit). `--ignore-rect`
blanks the burned-in text boxes before detection; their positions are in the
rig's overlay settings.

Dev-only, never imported by the app. Run from the repo root:
    python scripts/dev/allsky/library_to_buffer.py <night folder> \\
        --lat 51.5 --lon -0.1 --tz Europe/London [--db library.db] \\
        [--out buffer_night.json] [--model allsky_calibration.json] \\
        [--ignore-rect x,y,w,h ...] [--limit N]
"""
import argparse
import os
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
from PIL import Image

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))))

from services.allsky.buffer_dump import write_dump  # noqa: E402
from services.allsky.calibration_validate import model_in_frame  # noqa: E402
from services.allsky.fisheye import FisheyeModel  # noqa: E402
from services.allsky.frame_catalog import above_horizon_stars  # noqa: E402
from services.allsky.star_centroid import detect_stars, measure_sky_circle  # noqa: E402
from services.library.sessions import roof_state  # noqa: E402

# The service keeps a frame only when it has at least this many detections
# (CalibrationService._detect_frame); the same floor here keeps the dump
# faithful to what a live buffer would have held.
MIN_DETECTIONS = 5
MAX_STARS = 200

FILENAME_STAMP = re.compile(r'^(\d{8})_(\d{6})_')


def parse_exposure_s(text):
    """'20.66s' / '20.66' / '500ms' -> seconds, or None."""
    if text is None:
        return None
    s = str(text).strip().lower().replace(' ', '')
    try:
        if s.endswith('ms'):
            return float(s[:-2]) / 1000.0
        return float(s.rstrip('s'))
    except ValueError:
        return None


def parse_rect(text: str):
    parts = [int(p) for p in text.split(',')]
    if len(parts) != 4 or parts[2] <= 0 or parts[3] <= 0:
        raise argparse.ArgumentTypeError("--ignore-rect takes x,y,w,h with w,h > 0")
    return tuple(parts)


def filename_time(name: str, tz: ZoneInfo):
    """UTC datetime from a library file name, or None when it has no stamp."""
    m = FILENAME_STAMP.match(name)
    if not m:
        return None
    local = datetime.strptime(m.group(1) + m.group(2), '%Y%m%d%H%M%S').replace(tzinfo=tz)
    return local.astimezone(timezone.utc)


def load_rows(db_path) -> dict:
    """``{file name: row dict}`` for every image row in ``library.db``."""
    conn = sqlite3.connect(Path(db_path).resolve().as_uri() + '?mode=ro', uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT captured_at, path, exposure, roof FROM images").fetchall()
    finally:
        conn.close()
    return {os.path.basename(r['path']): dict(r) for r in rows}


def blank_rects(arr: np.ndarray, rects) -> np.ndarray:
    """Paint each rect with the frame's median so neither the circle scan
    nor the detector sees an edge where the text box was."""
    if not rects:
        return arr
    arr = arr.copy()
    fill = np.median(arr.reshape(-1, arr.shape[-1]), axis=0) if arr.ndim == 3 \
        else np.median(arr)
    for x, y, w, h in rects:
        arr[max(0, y):y + h, max(0, x):x + w] = fill
    return arr


def detect_frame(image: Image.Image, dt: datetime, lat: float, lon: float,
                 rects=(), exposure_s=None):
    """The service's `_detect_frame` on a library JPEG, or None to skip."""
    arr = blank_rects(np.asarray(image.convert('RGB')), rects)
    circle = measure_sky_circle(arr)
    if circle is None:
        return None
    sky_cx, sky_cy, sky_r = circle
    detected = detect_stars(arr, max_stars=MAX_STARS,
                            sky_cx=sky_cx, sky_cy=sky_cy, sky_radius=sky_r)
    if len(detected) < MIN_DETECTIONS:
        return None
    frame = {
        'dt': dt,
        'detected': detected,
        'above_horizon': above_horizon_stars(dt, lat, lon),
        'sky_cx': sky_cx, 'sky_cy': sky_cy, 'sky_r': sky_r,
        'image_width': image.width, 'image_height': image.height,
    }
    if exposure_s is not None:
        frame['exposure_s'] = exposure_s
    return frame


def build_frames(night_dir, lat, lon, tz: ZoneInfo, rows=None, rects=(),
                 limit=None, report=print):
    """Frames for every usable JPEG in ``night_dir``; ``rows`` from `load_rows`
    (None = no database). Returns ``(frames, skipped_roof, skipped_detect)``."""
    files = sorted(p for p in Path(night_dir).iterdir()
                   if p.suffix.lower() in ('.jpg', '.jpeg') and p.is_file())
    frames, skipped_roof, skipped_detect = [], 0, 0
    for path in files:
        row = (rows or {}).get(path.name)
        if row is not None:
            if roof_state(row.get('roof')) != 'open':
                skipped_roof += 1
                continue
            dt = datetime.fromtimestamp(int(row['captured_at']), tz=timezone.utc)
            exposure_s = parse_exposure_s(row.get('exposure'))
        else:
            if rows is not None:
                report(f"{path.name}: not in library.db, using the file name's time")
            dt = filename_time(path.name, tz)
            exposure_s = None
            if dt is None:
                report(f"{path.name}: no timestamp in the name, skipped")
                skipped_detect += 1
                continue
        with Image.open(path) as image:
            frame = detect_frame(image, dt, lat, lon, rects, exposure_s)
        if frame is None:
            skipped_detect += 1
            continue
        frames.append(frame)
        if limit and len(frames) >= limit:
            break
    return frames, skipped_roof, skipped_detect


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("night_dir", help="library night folder (e.g. Library/2026-09-17)")
    ap.add_argument("--lat", type=float, required=True)
    ap.add_argument("--lon", type=float, required=True)
    ap.add_argument("--tz", default="UTC",
                    help="IANA zone of the file-name stamps (the rig's local time)")
    ap.add_argument("--db", help="library.db (default: next to the night folder)")
    ap.add_argument("--out", help="dump path (default: buffer_<night>.json in the "
                                  "current directory)")
    ap.add_argument("--model", help="allsky_calibration.json to record in the dump, "
                                    "rescaled to the library frame size")
    ap.add_argument("--ignore-rect", type=parse_rect, action="append", default=[],
                    metavar="x,y,w,h", help="blank a burned-in text box (repeatable)")
    ap.add_argument("--limit", type=int, help="stop after N usable frames")
    args = ap.parse_args(argv)

    night_dir = Path(args.night_dir)
    if not night_dir.is_dir():
        print(f"{night_dir}: not a folder")
        return 2
    db_path = Path(args.db) if args.db else night_dir.parent / 'library.db'
    rows = None
    if db_path.is_file():
        rows = load_rows(db_path)
        print(f"library.db: {len(rows)} rows")
    else:
        print(f"no library.db at {db_path}: timestamps from file names ({args.tz})")

    frames, skipped_roof, skipped_detect = build_frames(
        night_dir, args.lat, args.lon, ZoneInfo(args.tz), rows,
        args.ignore_rect, args.limit)
    print(f"{night_dir.name}: {len(frames)} frame(s) kept, {skipped_roof} roof not "
          f"open, {skipped_detect} unusable (no sky circle / <{MIN_DETECTIONS} stars)")
    if not frames:
        return 1

    model = None
    if args.model:
        model = FisheyeModel.load(args.model)
        model = model_in_frame(model, frames[0]['image_width'], frames[0]['image_height'])
    out = Path(args.out) if args.out else Path.cwd() / f'buffer_{night_dir.name}.json'
    write_dump(frames, model, args.lat, args.lon, out)
    dts = sorted(f['dt'] for f in frames)
    span_min = (dts[-1] - dts[0]).total_seconds() / 60.0
    print(f"wrote {out} ({span_min:.0f} min span, "
          f"{'with' if model else 'no'} model)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
