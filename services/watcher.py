"""
File system watcher using watchdog
"""
import os
import time
import threading
from concurrent.futures import ThreadPoolExecutor
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from .processor import process_image
from .cleanup import run_cleanup
from .logger import app_logger


class ImageFileHandler(FileSystemEventHandler):
    """Handler for image file events"""

    # Cooldown period: ignore repeated events for the same file within this window
    RECENTLY_PROCESSED_COOLDOWN = 30  # seconds

    # Max entries before pruning the processed-files dict
    _MAX_PROCESSED_ENTRIES = 500

    def __init__(self, config, on_image_processed=None, weather_service=None):
        super().__init__()  # Initialize parent class
        self.config = config
        self.on_image_processed = on_image_processed
        self.weather_service = weather_service
        self.processing = set()  # Track files being processed
        self.recently_processed = {}  # filepath -> timestamp of last processing
        # Tracks (mtime, size) at time of processing so on_modified only
        # re-processes files whose content has genuinely changed — prevents
        # spurious re-processing from indexers, antivirus, backup tools, etc.
        self._processed_signatures = {}  # filepath -> (mtime, size)
        self.lock = threading.Lock()
        # Thread pool for concurrent file processing (REL-002 fix)
        self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="file_processor")
    
    def update_status(self, message):
        """Update status via callback"""
        app_logger.info(message)
    
    def wait_for_file_stable(self, filepath, timeout=10, check_interval=0.5):
        """
        Wait until file size is stable (file finished writing).
        Returns True if stable, False if timeout.
        """
        if not os.path.exists(filepath):
            return False
        
        last_size = -1
        stable_checks = 0
        elapsed = 0
        
        while elapsed < timeout:
            try:
                current_size = os.path.getsize(filepath)
                
                if current_size == last_size:
                    stable_checks += 1
                    if stable_checks >= 2:  # Stable for 2 checks
                        return True
                else:
                    stable_checks = 0
                    last_size = current_size
                
                time.sleep(check_interval)
                elapsed += check_interval
            
            except Exception as e:
                app_logger.error(f"Error checking file stability: {e}")
                return False
        
        return False
    
    def _is_recently_processed(self, filepath):
        """Check if file was processed within the cooldown window. Must be called under self.lock."""
        last_time = self.recently_processed.get(filepath)
        if last_time is None:
            return False
        return (time.time() - last_time) < self.RECENTLY_PROCESSED_COOLDOWN

    def _prune_recently_processed(self):
        """Remove stale entries from recently_processed. Must be called under self.lock."""
        now = time.time()
        stale = [fp for fp, ts in self.recently_processed.items()
                 if (now - ts) >= self.RECENTLY_PROCESSED_COOLDOWN]
        for fp in stale:
            del self.recently_processed[fp]

    def _prune_processed_signatures(self):
        """Evict oldest half of signature entries. Must be called under self.lock."""
        if len(self._processed_signatures) <= self._MAX_PROCESSED_ENTRIES // 2:
            return
        # Keep the most-recently-processed entries (by mtime)
        sorted_items = sorted(self._processed_signatures.items(), key=lambda kv: kv[1][0])
        to_remove = len(sorted_items) // 2
        for fp, _ in sorted_items[:to_remove]:
            del self._processed_signatures[fp]

    # on_modified fires for a never-seen file only if its mtime is this recent
    _MODIFIED_RECENCY_THRESHOLD = 30  # seconds

    def _has_file_changed(self, filepath):
        """Check if a file's content has changed since we last processed it.

        Returns True if the file should be (re-)processed — either because
        we've never seen it or because its mtime/size differ from last time.
        Must be called under self.lock.
        """
        prev = self._processed_signatures.get(filepath)
        if prev is None:
            return True
            # Never processed — only treat as changed if the file was
            # genuinely modified recently.  This prevents spurious
            # on_modified events (e.g. Windows Preview opening a file
            # updates access time, triggering watchdog) from processing
            # pre-existing, untouched files.
            try:
                stat = os.stat(filepath)
                return (time.time() - stat.st_mtime) < self._MODIFIED_RECENCY_THRESHOLD
            except OSError:
                return True
        try:
            stat = os.stat(filepath)
            return (stat.st_mtime, stat.st_size) != prev
        except OSError:
            return True

    def process_file(self, filepath):
        """Process a single image file"""
        with self.lock:
            if filepath in self.processing:
                return  # Already processing
            if self._is_recently_processed(filepath):
                app_logger.debug(f"Skipping recently processed file: {os.path.basename(filepath)}")
                return
            self.processing.add(filepath)
            # Periodically evict stale cooldown entries to prevent unbounded growth
            if len(self.recently_processed) > 100:
                self._prune_recently_processed()
        
        try:
            filename = os.path.basename(filepath)
            self.update_status(f"Detected: {filename}")
            
            # Wait for file to be fully written
            self.update_status(f"Waiting for {filename} to stabilize...")
            if not self.wait_for_file_stable(filepath):
                self.update_status(f"Timeout waiting for {filename}")
                return
            
            # Process the image
            self.update_status(f"Processing: {filename}")
            extras = {}
            success, output_path, error, processed_img = process_image(
                filepath, self.config, weather_service=self.weather_service, extras=extras)
            
            if success:
                self.update_status(f"✓ Saved: {os.path.basename(output_path)}")

                # Record successful processing for cooldown dedup
                with self.lock:
                    self.recently_processed[filepath] = time.time()
                    # Store file signature so on_modified skips unchanged files
                    try:
                        stat = os.stat(filepath)
                        self._processed_signatures[filepath] = (stat.st_mtime, stat.st_size)
                    except OSError:
                        pass
                    if len(self._processed_signatures) > self._MAX_PROCESSED_ENTRIES:
                        self._prune_processed_signatures()

                # Notify callback with both path and image
                if self.on_image_processed:
                    self.on_image_processed(output_path, processed_img, extras)

                # Run cleanup if enabled
                if self.config.get('cleanup_enabled', False):
                    cleanup_success, cleanup_msg = run_cleanup(self.config)
                    if cleanup_success:
                        self.update_status(f"Cleanup: {cleanup_msg}")
                    else:
                        self.update_status(f"Cleanup error: {cleanup_msg}")
            else:
                self.update_status(f"✗ Error processing {filename}: {error}")

        except Exception as e:
            self.update_status(f"✗ Exception processing {filepath}: {e}")
            from .posthog_service import capture_error
            capture_error(e, context='file_watcher')

        finally:
            with self.lock:
                self.processing.discard(filepath)
    
    SUPPORTED_EXTENSIONS = ('.png', '.jpg', '.jpeg')

    def _is_supported(self, filepath):
        return filepath.lower().endswith(self.SUPPORTED_EXTENSIONS)

    def on_created(self, event):
        """Called when a file is created"""
        if event.is_directory:
            return
        filepath = event.src_path
        if self._is_supported(filepath):
            self.executor.submit(self.process_file, filepath)

    def on_modified(self, event):
        """Called when a file is modified.

        Catches systems that trigger modified instead of created, but skips
        files whose content (mtime + size) hasn't actually changed since we
        last processed them — prevents spurious re-processing caused by
        antivirus, Windows Search indexing, backup tools, etc.
        """
        if event.is_directory:
            return
        filepath = event.src_path
        if self._is_supported(filepath):
            with self.lock:
                if filepath in self.processing or self._is_recently_processed(filepath):
                    return
                if not self._has_file_changed(filepath):
                    return
            self.executor.submit(self.process_file, filepath)

    def on_moved(self, event):
        """Called when a file is renamed/moved - catches atomic writes (temp -> final)"""
        if event.is_directory:
            return
        filepath = event.dest_path
        if self._is_supported(filepath):
            with self.lock:
                if filepath in self.processing or self._is_recently_processed(filepath):
                    return
            self.executor.submit(self.process_file, filepath)


    def shutdown(self):
        """Cleanup thread pool (called when stopping watcher)"""
        try:
            if hasattr(self, 'executor') and self.executor:
                # Use wait=True to allow current tasks to complete gracefully
                self.executor.shutdown(wait=True)
                app_logger.debug("File processing thread pool shut down")
        except Exception as e:
            app_logger.debug(f"Error shutting down thread pool: {e}")


class FileWatcher:
    """Main file watcher class"""
    
    def __init__(self, config, on_image_processed=None, weather_service=None):
        self.config = config
        self.on_image_processed = on_image_processed
        self.weather_service = weather_service
        self.observer = None
        self.handler = None
    
    def start(self):
        """Start watching the directory"""
        watch_dir = self.config.get('watch_directory', '')
        
        if not watch_dir or not os.path.exists(watch_dir):
            raise ValueError("Invalid watch directory")
        
        recursive = self.config.get('watch_recursive', True)
        
        # Create handler
        self.handler = ImageFileHandler(self.config, self.on_image_processed, self.weather_service)
        
        # Create observer
        self.observer = Observer()
        self.observer.schedule(self.handler, watch_dir, recursive=recursive)
        self.observer.start()
        
        mode = "recursively" if recursive else "non-recursively"
        app_logger.info(f"Watching {watch_dir} ({mode})")
    
    def stop(self):
        """Stop watching"""
        if self.observer:
            self.observer.stop()
            self.observer.join()
            self.observer = None
        
        app_logger.info("Stopped watching")
    
    def is_running(self):
        """Check if watcher is running"""
        return self.observer is not None and self.observer.is_alive()
