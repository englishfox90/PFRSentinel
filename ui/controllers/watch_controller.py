"""
Watch Controller for Qt UI
Adapter between PySide6 UI and existing FileWatcher service
"""
from PySide6.QtCore import QObject, Signal, QThread

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from services.logger import app_logger
from services.watcher import FileWatcher
from services.allsky.overlay_renderer import render_allsky_for_preview
from services.output_crop import METADATA_KEY as CROP_METADATA_KEY


class WatchControllerQt(QObject):
    """
    Qt-compatible watch controller
    Wraps existing FileWatcher service for use with PySide6 UI
    """

    started = Signal()
    stopped = Signal()
    file_detected = Signal(str)  # File path
    # (preview PIL Image, output PIL Image, output_path, extras from process_image)
    image_processed = Signal(object, object, str, dict)
    error = Signal(str)
    
    def __init__(self, main_window, parent=None):
        super().__init__(parent)
        self.main_window = main_window
        self.config = main_window.config
        
        self.watcher = None
        self.is_watching = False
    
    def start_watching(self, directory: str):
        """Start watching directory for new files"""
        if self.is_watching:
            return
        
        if not os.path.isdir(directory):
            self.error.emit(f"Invalid directory: {directory}")
            return
        
        try:
            weather_service = getattr(self.main_window, 'weather_service', None)
            self.watcher = FileWatcher(self.config, on_image_processed=self._on_file_processed, weather_service=weather_service)
            self.watcher.start()
            self.is_watching = True
            self.started.emit()
            app_logger.info(f"Started watching: {directory}")

        except Exception as e:
            self.error.emit(str(e))
            app_logger.error(f"Failed to start watching: {e}")
    
    def stop_watching(self):
        """Stop watching"""
        if not self.is_watching:
            return
        
        try:
            if self.watcher:
                self.watcher.stop()
                self.watcher = None
            
            self.is_watching = False
            self.stopped.emit()
            
            app_logger.info("Stopped watching")
            
        except Exception as e:
            app_logger.error(f"Error stopping watcher: {e}")
    
    def _on_file_processed(self, output_path: str, processed_img, extras=None):
        """Called by FileWatcher after a file has been processed and saved"""
        self.file_detected.emit(output_path)
        allsky_cfg = self.config.get('allsky_overlay', {})
        extras = dict(extras or {})
        # The renderer gets the processor's own metadata (issue #93): the
        # roof verdict, exposure and star evidence the observable-sky gate
        # already judged for this frame, with that verdict cached on it, so
        # the overlay follows the same decision instead of asking again from
        # a blind dict. The output crop (issue #12) rides on the image's info
        # dict; the renderer needs it to translate the calibration model into
        # output px.
        metadata = extras.get('metadata')
        metadata = metadata if isinstance(metadata, dict) else {}
        crop = processed_img.info.get(CROP_METADATA_KEY) if processed_img is not None else None
        if crop:
            metadata[CROP_METADATA_KEY] = crop
        preview_img = render_allsky_for_preview(processed_img, allsky_cfg, self.config, metadata)
        self.image_processed.emit(preview_img, processed_img, output_path, extras)
