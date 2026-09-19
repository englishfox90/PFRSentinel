"""Diagnostics controller — builds the support bundle off the GUI thread.

Flow (all on a daemon thread, results back via Qt signals):
  1. In camera mode, nudge the capture loop for a fresh frame and wait for the
     main window's raw cache to turn over (bounded by the exposure length).
  2. Export the cached frame (Bayer FITS + unprocessed PNG + metadata).
  3. Zip it with recent logs, the redacted config, the all-sky calibration
     files, the latest processed output, and a summary.json.
"""
import copy
import os
import shutil
import tempfile
import threading
import time

from PySide6.QtCore import QObject, Signal

from services import app_config
from services.diagnostics_bundle import build_bundle, default_bundle_path, environment_info
from services.logger import app_logger
from services.raw_frame_export import export_raw_frame
from services.reveal_in_file_manager import reveal_path
from version import __version__

# A forced capture still has to finish the exposure already in flight and then
# the new one, so the wait is sized from the exposure, not the interval.
FRESH_FRAME_MIN_WAIT_S = 45.0
FRESH_FRAME_MAX_WAIT_S = 600.0


class DiagnosticsController(QObject):
    progress = Signal(str)
    bundle_ready = Signal(str)
    bundle_failed = Signal(str)

    def __init__(self, main_window):
        super().__init__(main_window)
        self._mw = main_window
        self._worker = None
        # Queued onto the GUI thread (this object lives there), so Explorer is
        # launched from a thread with a COM apartment, not the worker.
        self.bundle_ready.connect(self._reveal)

    @property
    def is_running(self) -> bool:
        return self._worker is not None and self._worker.is_alive()

    def export_bundle(self, fresh_frame=True):
        """Start an export; ignored while one is already running."""
        if self.is_running:
            return
        try:
            # Snapshot the config on the GUI thread: a toggle flipped mid-export
            # would otherwise mutate the dict while the worker deep-copies it.
            config_data = copy.deepcopy(self._mw.config.data)
            self._worker = threading.Thread(
                target=self._run, args=(bool(fresh_frame), config_data),
                name="DiagnosticsExport", daemon=True,
            )
            self._worker.start()
        except Exception as e:
            app_logger.error(f"Diagnostics export could not start: {e}")
            self.bundle_failed.emit(str(e))

    # -- worker thread ------------------------------------------------------

    def _run(self, fresh_frame, config_data):
        work_dir = tempfile.mkdtemp(prefix='pfr_diag_')
        try:
            notes = []
            frame_files = self._collect_raw_frame(work_dir, fresh_frame, notes)

            extra = {f'frames/{os.path.basename(p)}': p for p in frame_files}
            extra.update(self._calibration_files(config_data))
            latest = self._latest_output_file(config_data)
            if latest:
                extra[f'frames/latest_output{os.path.splitext(latest)[1]}'] = latest

            self.progress.emit("Writing diagnostics bundle…")
            dest = default_bundle_path(os.path.join(app_config.get_app_data_dir(), 'diagnostics'))
            build_bundle(
                dest,
                config_data=config_data,
                log_dir=app_logger.get_log_dir(),
                summary=self._summary(config_data, notes),
                extra_files=extra,
            )
            self.bundle_ready.emit(str(dest))
        except Exception as e:
            app_logger.error(f"Diagnostics export failed: {e}")
            self.bundle_failed.emit(str(e))
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)

    def _collect_raw_frame(self, work_dir, fresh_frame, notes):
        zwo = self._live_camera()
        if fresh_frame and zwo is not None:
            self._wait_for_fresh_frame(zwo, notes)

        snapshot = getattr(self._mw, 'cached_raw_snapshot', None)
        image, metadata, captured_at = snapshot() if snapshot else (None, None, None)
        if image is None and not metadata:
            notes.append("No frame captured yet: the bundle has no raw frame. "
                         "Start capture and export again to include one.")
            return []

        self.progress.emit("Saving raw frame…")
        try:
            # Watch mode caches the processor's own dict, which a reprocess
            # mutates in place; a shallow copy keeps the JSON dump stable.
            result = export_raw_frame(work_dir, dict(metadata or {}), pil_image=image)
        except Exception as e:
            # The logs are the part that matters; never lose them to a frame.
            app_logger.error(f"Raw frame export failed: {e}")
            notes.append(f"Raw frame export failed: {e}")
            return []
        notes.extend(result['notes'])
        if captured_at is not None:
            notes.append(f"Raw frame captured at {captured_at.isoformat(timespec='seconds')}")
        return result['files']

    def _wait_for_fresh_frame(self, zwo, notes):
        _img, before_meta, before_time = self._mw.cached_raw_snapshot()
        timeout = self._fresh_frame_timeout(zwo)
        self.progress.emit(f"Capturing a fresh raw frame (up to {int(timeout)} s)…")
        zwo.request_immediate_capture()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            _img, meta, captured_at = self._mw.cached_raw_snapshot()
            # Both must have turned over: the time is written last, so a new
            # time with the same metadata object cannot happen, but a new
            # metadata object with the old time is the half-written window.
            if captured_at != before_time and meta is not before_meta:
                return
            if self._live_camera() is None:
                notes.append("Capture stopped while waiting for a fresh frame; "
                             "the last cached frame was used instead.")
                break
            time.sleep(0.5)
        else:
            notes.append(f"No fresh frame arrived within {int(timeout)} s; "
                         "the last cached frame was used instead.")
        # Don't leave the wake armed to shorten some later inter-frame wait.
        zwo.consume_immediate_capture()

    @staticmethod
    def _fresh_frame_timeout(zwo) -> float:
        exposure = float(getattr(zwo, 'exposure_seconds', 0) or 0)
        return min(FRESH_FRAME_MAX_WAIT_S, max(FRESH_FRAME_MIN_WAIT_S, 2 * exposure + 30))

    # -- context ------------------------------------------------------------

    def _live_camera(self):
        ctrl = getattr(self._mw, 'camera_controller', None)
        if ctrl is None or not getattr(ctrl, 'is_capturing', False):
            return None
        return getattr(ctrl, 'zwo_camera', None)

    @staticmethod
    def _calibration_files(config_data) -> dict:
        files = {
            'allsky/allsky_calibration.json': app_config.get_calibration_path(),
            'allsky/allsky_calibration.previous.json': app_config.get_calibration_backup_path(),
        }
        custom = (config_data.get('allsky_overlay') or {}).get('calibration_file')
        if custom and os.path.abspath(custom) != os.path.abspath(files['allsky/allsky_calibration.json']):
            files[f'allsky/custom_{os.path.basename(custom)}'] = custom
        return files

    def _summary(self, config_data, notes) -> dict:
        cfg = config_data
        info = environment_info(__version__)
        info.update({
            'capture_mode': cfg.get('capture_mode', 'camera'),
            'camera_capturing': bool(getattr(getattr(self._mw, 'camera_controller', None),
                                             'is_capturing', False)),
            'watch_active': bool(getattr(getattr(self._mw, 'watch_controller', None),
                                         'is_watching', False)),
            'selected_camera': cfg.get('zwo_selected_camera_name', ''),
            'live_camera': self._live_camera_settings(),
            'notes': notes,
        })
        return info

    def _live_camera_settings(self):
        zwo = getattr(getattr(self._mw, 'camera_controller', None), 'zwo_camera', None)
        if zwo is None:
            return None
        try:
            return {
                'exposure_s': round(float(zwo.exposure_seconds), 3),
                'gain': zwo.gain,
                'offset': zwo.offset,
                'auto_exposure': zwo.auto_exposure,
                'max_exposure_s': zwo.max_exposure,
                'target_brightness': zwo.target_brightness,
                'capture_interval_s': zwo.effective_capture_interval,
                'use_raw16': zwo.use_raw16,
                'bayer_pattern': zwo.bayer_pattern,
                'wb_config': dict(zwo.wb_config or {}),
            }
        except Exception as e:
            return {'error': str(e)}

    @staticmethod
    def _latest_output_file(config_data):
        from services.web_output import WebOutputServer
        path = getattr(WebOutputServer, 'latest_image_path', None)
        if path and os.path.isfile(path):
            return path
        out_dir = config_data.get('output_directory', '')
        try:
            candidates = [
                os.path.join(out_dir, f) for f in os.listdir(out_dir)
                if f.lower().endswith(('.jpg', '.jpeg', '.png'))
            ]
        except OSError:
            return None
        return max(candidates, key=os.path.getmtime) if candidates else None

    @staticmethod
    def _reveal(path):
        reveal_path(str(path))
