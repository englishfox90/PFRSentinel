"""Diagnostics bundle — one ZIP a user can attach to a bug report.

Pure file assembly: logs from the last few days, a redacted config, the
all-sky calibration files, whatever frame files the caller hands over, and a
``summary.json`` describing the environment and what was (and was not)
included. Nothing here touches Qt or the camera; the controller decides what
goes in.
"""
import copy
import json
import os
import platform
import sys
import time
import zipfile
from datetime import datetime
from pathlib import Path

from .logger import app_logger

# Any config key containing one of these is masked. Matched case-insensitively
# on the key name at every nesting level, so a new secret under a new section
# is caught without an allow-list update. Location, private endpoint URLs and
# the camera serial are masked too: the bundle is meant for a public issue.
SENSITIVE_KEY_MARKERS = (
    'token', 'secret', 'webhook', 'api_key', 'apikey', 'password',
    'distinct_id', 'client_secrets',
    'latitude', 'longitude', 'location', 'elevation', 'url', 'serial',
)
REDACTED = '<redacted>'

BUNDLE_PREFIX = 'PFRSentinel_diagnostics'
DEFAULT_LOG_DAYS = 3
# Recorded under ``missing`` when a rig has never written a buffer dump, so
# the summary says so the same way it does for an absent calibration file.
BUFFER_DUMP_PLACEHOLDER = 'allsky/buffer_*.json'


def redact_config(data):
    """Deep copy of ``data`` with sensitive values masked.

    Empty values stay empty so the reader can tell "not configured" from
    "configured but hidden" — that distinction is often the whole diagnosis.
    """
    return _redact(copy.deepcopy(data))


def _redact(node):
    if isinstance(node, dict):
        for key, value in list(node.items()):
            if _is_sensitive(key):
                if value not in ('', None, [], {}):
                    node[key] = REDACTED
            else:
                node[key] = _redact(value)
        return node
    if isinstance(node, list):
        return [_redact(item) for item in node]
    return node


def _is_sensitive(key) -> bool:
    name = str(key).lower()
    return any(marker in name for marker in SENSITIVE_KEY_MARKERS)


def recent_log_files(log_dir, days=DEFAULT_LOG_DAYS, now=None):
    """Log files under ``log_dir`` modified within the last ``days`` days.

    Rotated files carry a date suffix (``sentinel.log.2026-09-04``), so the
    match is on the modification time rather than the name.
    """
    log_dir = Path(log_dir)
    if not log_dir.is_dir():
        return []
    cutoff = (now if now is not None else time.time()) - days * 86400
    files = [
        p for p in log_dir.iterdir()
        if p.is_file() and p.stat().st_mtime >= cutoff
    ]
    return sorted(files, key=lambda p: p.stat().st_mtime)


def environment_info(version) -> dict:
    return {
        'app_version': version,
        'python': sys.version.split()[0],
        'platform': platform.platform(),
        'machine': platform.machine(),
        'frozen': bool(getattr(sys, 'frozen', False)),
        'created_at': datetime.now().astimezone().isoformat(timespec='seconds'),
    }


def allsky_buffer_dump_files(dump_dir) -> dict:
    """``{arcname: path}`` for the newest calibration buffer dump.

    Same shape as the calibration entries the controller builds, so it drops
    straight into ``extra_files``. Only the newest dump goes in: the older
    ones are the same rig on earlier escapes, and the bundle is attached to
    a GitHub issue with a size limit.
    """
    # Function-level: this module stays a light, pure file assembler; the
    # dump's naming lives with the dump, behind the whole allsky package.
    from .allsky.buffer_dump import newest_dump
    newest = newest_dump(dump_dir)
    if newest is None:
        return {BUFFER_DUMP_PLACEHOLDER: None}
    return {f'allsky/{newest.name}': str(newest)}


def default_bundle_path(base_dir) -> Path:
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    return Path(base_dir) / f'{BUNDLE_PREFIX}_{stamp}.zip'


def build_bundle(dest_zip, *, config_data, log_dir, summary,
                 extra_files=None, log_days=DEFAULT_LOG_DAYS) -> Path:
    """Write the bundle and return its path.

    Args:
        dest_zip: Output ``.zip`` path (parent directory is created).
        config_data: The live config dict; redacted before writing.
        log_dir: Directory holding ``sentinel.log`` and its rotations.
        summary: Dict written as ``summary.json``; the list of files actually
            included is appended under ``"contents"``.
        extra_files: ``{arcname: path}`` of additional files (frames,
            calibration JSON). Missing paths are skipped and recorded in the
            summary rather than failing the whole export.
        log_days: How many days of logs to include.
    """
    dest_zip = Path(dest_zip)
    dest_zip.parent.mkdir(parents=True, exist_ok=True)

    contents = []
    missing = []
    with zipfile.ZipFile(dest_zip, 'w', zipfile.ZIP_DEFLATED) as zf:
        for log_file in recent_log_files(log_dir, log_days):
            arcname = f'logs/{log_file.name}'
            zf.write(log_file, arcname)
            contents.append(arcname)

        zf.writestr('config.redacted.json',
                    json.dumps(redact_config(config_data), indent=2, default=str))
        contents.append('config.redacted.json')

        for arcname, path in (extra_files or {}).items():
            if path and os.path.isfile(path):
                zf.write(path, arcname)
                contents.append(arcname)
            else:
                missing.append(arcname)

        summary = dict(summary)
        summary['contents'] = contents
        summary['missing'] = missing
        zf.writestr('summary.json', json.dumps(summary, indent=2, default=str))

    app_logger.info(
        f"Diagnostics bundle written: {dest_zip} "
        f"({len(contents) + 1} files, {dest_zip.stat().st_size // 1024} KB)"
    )
    return dest_zip
