#!/usr/bin/env python3
"""
Backfill Calibration JSON Files

Adds missing fields (corner_analysis, percentiles, time_context) to existing
calibration JSON files by re-analyzing the corresponding raw/lum FITS files.

Usage:
    python backfill_calibration.py <directory> [--dry-run]
    
Examples:
    python backfill_calibration.py "H:\\raw_debug" --dry-run
    python backfill_calibration.py "H:\\raw_debug\\Roof Closed Day Time"
"""
import argparse
import json
import os
import sys
import re
from datetime import datetime
from pathlib import Path

import numpy as np

try:
    from astropy.io import fits
except ImportError:
    print("ERROR: astropy required. Install with: pip install astropy")
    sys.exit(1)

# The time context comes from the app's own service so a backfilled flag is
# the one a live capture writes (issue #86: this script used to carry its own
# copy of the algorithm, with the same faults). scripts/dev/allsky/ is three
# levels below the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from services.time_context import ASTRAL_AVAILABLE, compute_time_context as _service_time_context  # noqa: E402
from services.moon import get_configured_location as _config_location  # noqa: E402

if not ASTRAL_AVAILABLE:
    print("WARNING: astral not installed. Using simple hour-based time classification.")
    print("         Install with: pip install astral")


def parse_timestamp_from_filename(filename):
    """
    Extract timestamp from filename like calibration_20260105_165636.json
    Returns datetime object or None if pattern doesn't match.
    """
    match = re.search(r'(\d{8})_(\d{6})', filename)
    if match:
        date_str, time_str = match.groups()
        try:
            return datetime.strptime(f"{date_str}_{time_str}", "%Y%m%d_%H%M%S")
        except ValueError:
            return None
    return None


def compute_corner_analysis(lum, norm_array=None, roi_size=50, margin=5):
    """
    Compute corner-vs-center analysis for mode classification.
    """
    h, w = lum.shape
    
    # Define corner ROIs
    corners = {
        'tl': lum[margin:margin+roi_size, margin:margin+roi_size],
        'tr': lum[margin:margin+roi_size, w-margin-roi_size:w-margin],
        'bl': lum[h-margin-roi_size:h-margin, margin:margin+roi_size],
        'br': lum[h-margin-roi_size:h-margin, w-margin-roi_size:w-margin],
    }
    
    all_corners = np.concatenate([c.flatten() for c in corners.values()])
    
    # Center ROI (central 25%)
    ch, cw = h // 4, w // 4
    center = lum[ch:3*ch, cw:3*cw]
    
    # Corner stats
    corner_med = float(np.median(all_corners))
    corner_p90 = float(np.percentile(all_corners, 90))
    corner_mad = float(np.median(np.abs(all_corners - corner_med)))
    corner_stddev = float(corner_mad * 1.4826)
    
    corner_meds = {k: float(np.median(c)) for k, c in corners.items()}
    
    # Center stats
    center_flat = center.flatten()
    center_med = float(np.median(center_flat))
    center_p90 = float(np.percentile(center_flat, 90))
    
    # Ratios
    corner_to_center_ratio = corner_med / center_med if center_med > 0.001 else 1.0
    center_minus_corner = center_med - corner_med
    
    result = {
        'roi_size': roi_size,
        'margin': margin,
        'corner_med': round(corner_med, 6),
        'corner_p90': round(corner_p90, 6),
        'corner_stddev': round(corner_stddev, 6),
        'corner_meds': {k: round(v, 6) for k, v in corner_meds.items()},
        'center_med': round(center_med, 6),
        'center_p90': round(center_p90, 6),
        'corner_to_center_ratio': round(corner_to_center_ratio, 4),
        'center_minus_corner': round(center_minus_corner, 6),
    }
    
    # Per-channel RGB corner bias
    if norm_array is not None and norm_array.ndim == 3 and norm_array.shape[2] == 3:
        rgb_bias = {}
        for c, name in enumerate(['bias_r', 'bias_g', 'bias_b']):
            channel = norm_array[:,:,c]
            ch_corners = np.concatenate([
                channel[margin:margin+roi_size, margin:margin+roi_size].flatten(),
                channel[margin:margin+roi_size, w-margin-roi_size:w-margin].flatten(),
                channel[h-margin-roi_size:h-margin, margin:margin+roi_size].flatten(),
                channel[h-margin-roi_size:h-margin, w-margin-roi_size:w-margin].flatten(),
            ])
            rgb_bias[name] = round(float(np.median(ch_corners)), 6)
        result['rgb_corner_bias'] = rgb_bias
    
    return result


def compute_percentiles(lum):
    """Compute extended percentile stats."""
    return {
        'p1': round(float(np.percentile(lum, 1)), 6),
        'p10': round(float(np.percentile(lum, 10)), 6),
        'p50': round(float(np.median(lum)), 6),
        'p90': round(float(np.percentile(lum, 90)), 6),
        'p99': round(float(np.percentile(lum, 99)), 6),
    }


def compute_time_context(dt):
    """Time context for a capture at naive host-local ``dt``, from the same
    service the app uses at capture time."""
    return _service_time_context(now=dt, location=get_configured_location())


def get_configured_location():
    """(latitude, longitude, name) from the weather config, else the default
    observatory the training set came from."""
    # Default location: Rockwood, Texas observatory
    DEFAULT_LAT = 31.3303162
    DEFAULT_LON = -100.4570705
    DEFAULT_NAME = "Rockwood, Texas"

    lat, lon, name = _config_location()
    if lat is not None and lon is not None:
        return lat, lon, name
    return DEFAULT_LAT, DEFAULT_LON, DEFAULT_NAME


def load_fits_normalized(fits_path, denom=None):
    """
    Load FITS file and return normalized array.
    
    Args:
        fits_path: Path to FITS file
        denom: Normalization denominator (auto-detect if None)
    
    Returns:
        Normalized array (0-1 range)
    """
    with fits.open(fits_path) as hdul:
        data = hdul[0].data.astype(np.float32)
        
        # Handle RGB FITS (C, H, W) -> (H, W, C)
        if data.ndim == 3 and data.shape[0] == 3:
            data = np.transpose(data, (1, 2, 0))
        
        # Auto-detect denominator if not provided
        if denom is None:
            raw_max = np.max(data)
            if raw_max <= 1.0:
                # Already normalized (luminance file)
                return data
            elif raw_max <= 255:
                denom = 255.0
            elif raw_max <= 4095:
                denom = 4095.0
            else:
                denom = 65535.0
        
        return data / denom


def compute_luminance(rgb_array):
    """Compute luminance from RGB array."""
    if rgb_array.ndim == 2:
        return rgb_array
    elif rgb_array.ndim == 3 and rgb_array.shape[2] == 3:
        return 0.299 * rgb_array[:,:,0] + 0.587 * rgb_array[:,:,1] + 0.114 * rgb_array[:,:,2]
    else:
        return rgb_array.mean(axis=-1) if rgb_array.ndim > 2 else rgb_array


def backfill_calibration(json_path, dry_run=False, force_time=False):
    """
    Backfill missing fields in a calibration JSON file.
    
    Args:
        json_path: Path to calibration JSON file
        dry_run: If True, don't modify files
        force_time: If True, recalculate time_context even if exists
    
    Returns:
        tuple: (success: bool, message: str, fields_added: list)
    """
    json_path = Path(json_path)
    
    # Load existing calibration
    try:
        with open(json_path, 'r') as f:
            cal = json.load(f)
    except Exception as e:
        return False, f"Failed to load JSON: {e}", []
    
    # Check what's missing (or needs updating)
    fields_to_add = []
    if 'corner_analysis' not in cal:
        fields_to_add.append('corner_analysis')
    if 'percentiles' not in cal:
        fields_to_add.append('percentiles')
    if 'time_context' not in cal or force_time:
        # --force-time recomputes every file, 'astral' ones included: the
        # flag they carry from before issue #86 was wrong too.
        fields_to_add.append('time_context')
    
    if not fields_to_add:
        return True, "Already complete", []
    
    # Extract timestamp from filename
    dt = parse_timestamp_from_filename(json_path.name)
    if not dt:
        return False, "Could not parse timestamp from filename", []
    
    timestamp_str = dt.strftime("%Y%m%d_%H%M%S")
    
    # Find matching FITS files
    parent_dir = json_path.parent
    lum_path = parent_dir / f"lum_{timestamp_str}.fits"
    raw_path = parent_dir / f"raw_{timestamp_str}.fits"
    
    # Determine if we need FITS files (only for corner_analysis or percentiles)
    needs_fits = 'corner_analysis' in fields_to_add or 'percentiles' in fields_to_add
    
    # Find matching FITS files if needed
    have_lum = lum_path.exists()
    have_raw = raw_path.exists()
    
    if needs_fits and not have_lum and not have_raw:
        return False, f"No matching FITS files found (looked for lum_{timestamp_str}.fits, raw_{timestamp_str}.fits)", []
    
    # Get normalization denominator from existing calibration
    denom = cal.get('normalization', {}).get('denom', 65535)
    
    # Load data only if needed
    lum = None
    norm_rgb = None
    
    if needs_fits:
        if have_lum:
            try:
                lum = load_fits_normalized(lum_path)
                if lum.ndim == 3:
                    lum = compute_luminance(lum)
            except Exception as e:
                return False, f"Failed to load lum FITS: {e}", []
        
        if have_raw:
            try:
                norm_rgb = load_fits_normalized(raw_path, denom)
                if lum is None:
                    lum = compute_luminance(norm_rgb)
            except Exception as e:
                if lum is None:
                    return False, f"Failed to load raw FITS: {e}", []
                # Can continue without raw if we have lum
                norm_rgb = None
    
    # Compute missing fields
    if 'corner_analysis' in fields_to_add:
        cal['corner_analysis'] = compute_corner_analysis(lum, norm_rgb)
    
    if 'percentiles' in fields_to_add:
        cal['percentiles'] = compute_percentiles(lum)
    
    if 'time_context' in fields_to_add:
        cal['time_context'] = compute_time_context(dt)
    
    # Save updated calibration
    if not dry_run:
        try:
            with open(json_path, 'w') as f:
                json.dump(cal, f, indent=2)
        except Exception as e:
            return False, f"Failed to save JSON: {e}", fields_to_add
    
    return True, "Updated", fields_to_add


def find_calibration_files(directory, recursive=True):
    """Find all calibration_*.json files in directory."""
    directory = Path(directory)
    pattern = "calibration_*.json"
    
    if recursive:
        return list(directory.rglob(pattern))
    else:
        return list(directory.glob(pattern))


def main():
    parser = argparse.ArgumentParser(
        description="Backfill missing fields in calibration JSON files",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    python backfill_calibration.py "H:\\raw_debug" --dry-run
    python backfill_calibration.py "H:\\raw_debug\\Roof Closed Day Time"
    python backfill_calibration.py "H:\\raw_debug" --no-recursive
    python backfill_calibration.py "H:\\raw_debug" --force-time
        """
    )
    parser.add_argument('directory', help='Directory containing calibration files')
    parser.add_argument('--dry-run', action='store_true', 
                        help='Show what would be done without making changes')
    parser.add_argument('--no-recursive', action='store_true',
                        help='Do not search subdirectories')
    parser.add_argument('--force-time', action='store_true',
                        help='Recompute time_context for every file, including ones that already '
                             'have it (needed once to re-label data written before issue #86)')
    
    args = parser.parse_args()
    
    directory = Path(args.directory)
    if not directory.exists():
        print(f"ERROR: Directory not found: {directory}")
        sys.exit(1)
    
    # Check astral availability when --force-time is used
    if args.force_time:
        if ASTRAL_AVAILABLE:
            lat, lon, loc_name = get_configured_location()
            print(f"Using astral calculations for location: {loc_name} ({lat}, {lon})")
        else:
            print("WARNING: astral not installed, --force-time will use simple classification")
    
    # Find all calibration files
    print(f"Searching for calibration files in: {directory}")
    cal_files = find_calibration_files(directory, recursive=not args.no_recursive)
    
    if not cal_files:
        print("No calibration_*.json files found")
        sys.exit(0)
    
    print(f"Found {len(cal_files)} calibration file(s)")
    if args.dry_run:
        print("=== DRY RUN - No changes will be made ===\n")
    
    # Process each file
    stats = {'success': 0, 'skipped': 0, 'failed': 0, 'complete': 0}
    
    for cal_path in sorted(cal_files):
        rel_path = cal_path.relative_to(directory) if cal_path.is_relative_to(directory) else cal_path
        
        success, message, fields_added = backfill_calibration(
            cal_path, 
            dry_run=args.dry_run,
            force_time=args.force_time
        )
        
        if success:
            if fields_added:
                action = "Would update" if args.dry_run else "Updated"
                print(f"+ {rel_path}: {action} {', '.join(fields_added)}")
                stats['success'] += 1
            else:
                print(f"  {rel_path}: {message}")
                stats['complete'] += 1
        else:
            print(f"X {rel_path}: {message}")
            stats['failed'] += 1
    
    # Summary
    print(f"\n{'='*50}")
    print(f"Summary:")
    print(f"  Already complete: {stats['complete']}")
    print(f"  {'Would update' if args.dry_run else 'Updated'}: {stats['success']}")
    print(f"  Failed: {stats['failed']}")
    
    if args.dry_run and stats['success'] > 0:
        print(f"\nRun without --dry-run to apply changes")


if __name__ == '__main__':
    main()
