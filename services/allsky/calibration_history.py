"""
A bounded history of every calibration model that became the live one.

The calibration file holds one model and its backup one more. On 2026-09-29
(discussion #105) two automatic refinements in half an hour replaced a
Guided Calibration solve and then the backup holding it, and the next night
a twilight score hid the overlay; from the diagnostics bundle nobody could
say which model had been live when, and the user had nothing to go back to.

Every write of the calibration file (the background service's saves through
`calibration_store.save_with_backup`, a manual or guided result saved by the
controller, a restore) also writes the model here as its own small JSON,
with what put it there. The UI lists them and restores one; the diagnostics
bundle carries them, so a "my calibration is gone" report can be answered
from the bundle.

Retention keeps the folder small: the newest `MAX_GUIDED` guided solves and
the newest `MAX_OTHER` of everything else, counted separately so a busy
night of refinements can never push the user's anchored solves out. Pruning
deletes files only, never the folder.

Pure apart from file I/O: no Qt.
"""
import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from services.logger import app_logger as log

from .fisheye import FisheyeModel

HISTORY_VERSION = 1
HISTORY_DIRNAME = 'calibration_history'
ENTRY_PREFIX = 'cal_'
ENTRY_SUFFIX = '.json'
STAMP_FORMAT = '%Y%m%dT%H%M%S_%fZ'

SOURCE_GUIDED = 'guided'
SOURCE_MANUAL = 'manual'
SOURCE_REFINEMENT = 'refinement'
SOURCE_ESCAPE = 'escape'
SOURCE_COLD_START = 'cold_start'
SOURCE_RESTORED = 'restored'

SOURCE_LABELS = {
    SOURCE_GUIDED: 'Guided Calibration',
    SOURCE_MANUAL: 'Calibrate Now',
    SOURCE_REFINEMENT: 'Automatic refinement',
    SOURCE_ESCAPE: 'Automatic re-solve',
    SOURCE_COLD_START: 'First automatic calibration',
    SOURCE_RESTORED: 'Restored',
}

# A model file is ~2 KB. 10 guided solves is months of a user re-anchoring;
# 30 others is several nights of refinements at the 10-minute cadence's
# realistic save rate (a handful a night). Under 100 KB in all.
MAX_GUIDED = 10
MAX_OTHER = 30


@dataclass(frozen=True)
class HistoryEntry:
    entry_id: str          # file stem; stable handle for restore
    saved_at: str          # ISO 8601 UTC, when it became the live model
    source: str            # SOURCE_*
    provenance: str
    quality: str
    rms_residual: float
    n_matches: int
    n_images: int
    chance_ratio: float
    final_tol_px: float
    calibrated_at: str     # the model's own stamp (links `replaced`)
    replaced: str          # calibrated_at of the model it replaced, '' if none
    restored_from: str     # entry_id a restore came from, else ''

    @property
    def label(self) -> str:
        return SOURCE_LABELS.get(self.source, self.source or 'Unknown')

    @property
    def is_guided(self) -> bool:
        return self.source == SOURCE_GUIDED


def history_dir(create: bool = False) -> str:
    from services.app_config import get_allsky_buffer_dir
    path = os.path.join(get_allsky_buffer_dir(create=create), HISTORY_DIRNAME)
    if create:
        os.makedirs(path, exist_ok=True)
    return path


def source_for_model(model: FisheyeModel) -> str:
    """Guided for the user-anchored solve, else a manual Calibrate Now."""
    from .model_admission import is_user_anchored
    return SOURCE_GUIDED if is_user_anchored(model) else SOURCE_MANUAL


def record(model: FisheyeModel, source: str, replaced: Optional[FisheyeModel] = None,
           restored_from: str = '', out_dir: Optional[str] = None,
           now: Optional[datetime] = None) -> Optional[str]:
    """Write `model` as a new history entry and prune; the entry id, or None
    when the write failed (a warning — the calibration file is already
    saved, which is what matters)."""
    try:
        from .calibration_quality import model_quality
        out = out_dir or history_dir(create=True)
        os.makedirs(out, exist_ok=True)
        when = now or datetime.now(timezone.utc)
        entry_id = f"{ENTRY_PREFIX}{when.strftime(STAMP_FORMAT)}_{source}"
        entry = HistoryEntry(
            entry_id=entry_id,
            saved_at=when.isoformat(),
            source=source,
            provenance=str(getattr(model, 'provenance', '') or ''),
            quality=str(model_quality(model, model.n_images, model.span_minutes)),
            rms_residual=float(model.rms_residual),
            n_matches=int(model.n_matches),
            n_images=int(model.n_images),
            chance_ratio=float(getattr(model, 'chance_ratio', 0.0) or 0.0),
            final_tol_px=float(getattr(model, 'final_tol_px', 0.0) or 0.0),
            calibrated_at=str(model.calibrated_at or ''),
            replaced=str(getattr(replaced, 'calibrated_at', '') or '') if replaced else '',
            restored_from=restored_from,
        )
        payload = {'version': HISTORY_VERSION, 'entry': asdict(entry),
                   'model': asdict(model)}
        path = os.path.join(out, entry_id + ENTRY_SUFFIX)
        tmp = path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(payload, f, indent=1)
        os.replace(tmp, path)
        prune(out)
        log.info(f"Calibration history: kept {entry_id} ({entry.label}, "
                 f"RMS {entry.rms_residual:.2f}px, {entry.n_matches} matches)")
        return entry_id
    except Exception as e:
        log.warning(f"Could not add the calibration to its history: {e}")
        return None


def _entry_paths(out_dir) -> List[Path]:
    d = Path(out_dir)
    if not d.is_dir():
        return []
    return sorted(p for p in d.glob(f'{ENTRY_PREFIX}*{ENTRY_SUFFIX}') if p.is_file())


def _read(path: Path) -> Optional[dict]:
    try:
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
        if not isinstance(data, dict) or not isinstance(data.get('entry'), dict):
            return None
        return data
    except Exception:
        return None


def _entry_from(data: dict, stem: str) -> Optional[HistoryEntry]:
    raw = data['entry']
    try:
        fields = {k: raw.get(k) for k in HistoryEntry.__dataclass_fields__}
        fields['entry_id'] = stem
        defaults = {'saved_at': '', 'source': '', 'provenance': '', 'quality': '',
                    'rms_residual': 0.0, 'n_matches': 0, 'n_images': 0,
                    'chance_ratio': 0.0, 'final_tol_px': 0.0, 'calibrated_at': '',
                    'replaced': '', 'restored_from': ''}
        for k, v in defaults.items():
            if fields.get(k) is None:
                fields[k] = v
        return HistoryEntry(**fields)
    except Exception:
        return None


def list_entries(out_dir: Optional[str] = None) -> List[HistoryEntry]:
    """Every readable entry, newest first. Unreadable files are skipped."""
    out = out_dir or history_dir()
    entries = []
    for path in _entry_paths(out):
        data = _read(path)
        entry = _entry_from(data, path.stem) if data else None
        if entry is not None:
            entries.append(entry)
    entries.sort(key=lambda e: e.entry_id, reverse=True)
    return entries


def newest_guided(entries: List[HistoryEntry]) -> Optional[HistoryEntry]:
    return next((e for e in entries if e.is_guided), None)


def load_model(entry_id: str, out_dir: Optional[str] = None) -> Optional[FisheyeModel]:
    """The model stored under `entry_id`, or None when missing or invalid."""
    if not entry_id or os.sep in entry_id or '/' in entry_id:
        return None
    path = Path(out_dir or history_dir()) / (entry_id + ENTRY_SUFFIX)
    if not path.is_file():
        return None
    data = _read(path)
    if not data or not isinstance(data.get('model'), dict):
        return None
    try:
        raw = data['model']
        model = FisheyeModel(**{k: v for k, v in raw.items()
                                if k in FisheyeModel.__dataclass_fields__})
    except Exception:
        return None
    return model if model.is_valid() else None


def prune(out_dir, max_guided: int = MAX_GUIDED, max_other: int = MAX_OTHER) -> List[Path]:
    """Keep the newest `max_guided` guided entries and `max_other` others;
    delete the rest plus stale `.tmp` files. Files only, never folders."""
    guided, other = [], []
    for path in _entry_paths(out_dir):
        (guided if path.stem.endswith('_' + SOURCE_GUIDED) else other).append(path)
    doomed = guided[:max(0, len(guided) - max_guided)] + other[:max(0, len(other) - max_other)]
    d = Path(out_dir)
    if d.is_dir():
        doomed += [p for p in d.glob(f'{ENTRY_PREFIX}*{ENTRY_SUFFIX}.tmp') if p.is_file()]
    removed = []
    for path in doomed:
        if not os.path.isfile(path):
            continue
        try:
            os.remove(path)
            removed.append(path)
        except OSError as e:
            log.warning(f"Could not remove old calibration history entry {path}: {e}")
    return removed


def bundle_files(out_dir: Optional[str] = None, max_bytes: int = 256 * 1024) -> dict:
    """``{arcname: path}`` of every entry for the diagnostics ZIP. Retention
    already bounds the count; a file over `max_bytes` (never written by
    `record`) is left out rather than bloating the bundle."""
    out = out_dir or history_dir()
    files = {}
    for path in _entry_paths(out):
        try:
            if path.stat().st_size > max_bytes:
                continue
        except OSError:
            continue
        files[f'allsky/{HISTORY_DIRNAME}/{path.name}'] = str(path)
    return files
