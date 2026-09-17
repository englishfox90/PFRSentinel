"""
Thread-safe logging module for GUI with 7-day rotating file logs
"""
import queue
import logging
import logging.handlers
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from .app_config import APP_NAME, LOG_FILE


LOCATION_NOTICE_MARKER = '.log_location_notice'


class SafeTimedRotatingFileHandler(logging.handlers.TimedRotatingFileHandler):
    """TimedRotatingFileHandler that tolerates the log file being locked.

    Dev tools (labeling_tool, validate_labels, ...) run as separate processes
    while the 24/7 app holds sentinel.log open. On Windows the rename inside
    doRollover then fails with PermissionError, and because rolloverAt is never
    advanced it retries — and errors — on every record. Swallow the failure,
    reopen the current file, and push rollover to the next period so the loser
    of the race just keeps appending instead of spamming.
    """

    def doRollover(self):
        try:
            super().doRollover()
        except OSError:
            try:
                if self.stream is None and not self.delay:
                    self.stream = self._open()
            except OSError:
                self.stream = None
            self.rolloverAt = self.computeRollover(int(time.time()))


def _safe_console_write(text):
    """Print `text` to stdout, tolerating any encoding or stream state.

    Console output here is a best-effort dev convenience — the GUI queue and
    the UTF-8 file handler are the sources of truth and both take the raw
    `text` untouched. This only degrades what appears on a plain-ASCII
    console; it must never be the thing that takes down a logging call.

    Two real failure modes, both hit in production:
    - A PyInstaller windowed build (`console=False`) has `sys.stdout` as
      None, so a bare `print()` raises AttributeError.
    - Windows defaults a redirected/piped stdout to cp1252, and log messages
      routinely contain glyphs (warning signs, arrows, checkmarks, emoji)
      that codec can't encode, raising UnicodeEncodeError from inside the
      logging path.
    """
    stream = sys.stdout
    if stream is None:
        return
    try:
        print(text, file=stream)
        return
    except UnicodeEncodeError:
        pass
    except Exception:
        return
    try:
        encoding = getattr(stream, 'encoding', None) or 'utf-8'
        safe_text = text.encode(encoding, errors='replace').decode(encoding, errors='replace')
        print(safe_text, file=stream)
    except Exception:
        pass


class AppLogger:
    """Thread-safe logger with GUI queue and 7-day rotating file logs"""
    
    def __init__(self):
        self.message_queue = queue.Queue()
        self.log_callbacks = []
        self.error_callback = None  # Callback for Discord error alerts
        
        # Set up file logging
        self.log_dir = self._get_log_directory()
        self._setup_file_logging()
        self._cleanup_old_logs()
        self._announce_log_location()
    
    def _get_log_directory(self):
        """Resolve the shared log directory under the app data root."""
        # Imported inside the function: this module is constructed at import
        # time by almost every other module, so keeping utils_paths (and the
        # app_config/platformdirs chain behind it) out of logger's module-level
        # imports leaves those modules free to log without a circular import.
        from .utils_paths import get_log_dir
        return Path(get_log_dir())
    
    def _setup_file_logging(self):
        """Set up rotating file handler for 7-day logs"""
        # CRITICAL: Completely silence third-party loggers FIRST
        # Set to CRITICAL (50) - only log fatal errors, nothing else
        for logger_name in ['urllib3', 'PIL', 'requests', 
                           'urllib3.connectionpool', 'PIL.PngImagePlugin']:
            third_party_logger = logging.getLogger(logger_name)
            third_party_logger.setLevel(logging.CRITICAL)  # Highest level - silence everything
            third_party_logger.propagate = False  # Don't let messages bubble up
        
        # Create dedicated logger for our app (not root logger)
        self.file_logger = logging.getLogger(APP_NAME)
        self.file_logger.setLevel(logging.DEBUG)
        
        # Prevent propagation to root logger to avoid duplicate messages
        self.file_logger.propagate = False
        
        # Remove any existing handlers to avoid duplicates
        for handler in list(self.file_logger.handlers):
            self.file_logger.removeHandler(handler)
            handler.close()
        
        # Always create our own file handler
        log_file = self.log_dir / LOG_FILE
        handler = SafeTimedRotatingFileHandler(
            log_file,
            when='midnight',
            interval=1,
            backupCount=7,  # Keep 7 days
            encoding='utf-8'
        )
        
        # Format: [2025-12-22 18:30:43] INFO - Message
        formatter = logging.Formatter('[%(asctime)s] %(levelname)-8s - %(message)s', 
                                     datefmt='%Y-%m-%d %H:%M:%S')
        handler.setFormatter(formatter)
        
        self.file_logger.addHandler(handler)
        self.file_handler = handler
    
    def _cleanup_old_logs(self):
        """Delete log files older than 7 days"""
        if not self.log_dir.exists():
            return
        
        cutoff = datetime.now() - timedelta(days=7)
        # Derived from LOG_FILE so it cannot drift from the file actually
        # written; matches the active log and the date-suffixed rotations
        # (sentinel.log.2026-09-04) SafeTimedRotatingFileHandler leaves behind.
        for log_file in self.log_dir.glob(f'{LOG_FILE}*'):
            # The active file is held open by the handler, and on an install
            # idle for over a week its mtime is past the cutoff too.
            if log_file.name == LOG_FILE:
                continue
            try:
                mtime = datetime.fromtimestamp(log_file.stat().st_mtime)
                if mtime < cutoff:
                    log_file.unlink()
                    _safe_console_write(f"Deleted old log file: {log_file.name}")
            except Exception as e:
                _safe_console_write(f"Error cleaning up old log: {e}")
    
    def _announce_log_location(self):
        """Log the log directory once, the first time it is written to.

        The directory moved out of %APPDATA% into the single app data root;
        older wiki pages and support threads still point at the previous path,
        so leave a breadcrumb rather than migrating years of disposable logs.
        """
        from .utils_paths import get_app_data_dir

        # The marker lives in the app data root, not the log directory:
        # diagnostics_bundle sweeps every recent file out of the log directory
        # into support ZIPs, and an unexplained dotfile there costs the reader
        # time at exactly the wrong moment.
        marker = Path(get_app_data_dir()) / LOCATION_NOTICE_MARKER
        try:
            if marker.exists():
                return
            marker.touch()
        except OSError:
            return
        self.info(f"Log files are now stored in: {self.log_dir}")

    def get_log_dir(self):
        """Get the log directory path for UI display"""
        return str(self.log_dir)
    
    def set_error_callback(self, callback):
        """Set callback for error messages (used for Discord alerts)"""
        self.error_callback = callback
    
    def log(self, message, level="INFO"):
        """Add a log message to queue AND file"""
        timestamp = datetime.now().strftime('%H:%M:%S')
        formatted_message = f"[{timestamp}] {level}: {message}"
        
        # Queue for GUI
        self.message_queue.put(formatted_message)
        
        # Console (best-effort; see _safe_console_write)
        _safe_console_write(formatted_message)
        
        # File logging
        log_level = getattr(logging, level, logging.INFO)
        self.file_logger.log(log_level, message)
        
        # Call error callback if this is an error
        if level == "ERROR" and self.error_callback:
            try:
                self.error_callback(message)
            except Exception as e:
                _safe_console_write(f"Error in Discord callback: {e}")

    
    def info(self, message):
        """Log info message"""
        self.log(message, "INFO")
    
    def error(self, message):
        """Log error message"""
        self.log(message, "ERROR")
    
    def warning(self, message):
        """Log warning message"""
        self.log(message, "WARN")
    
    def debug(self, message):
        """Log debug message"""
        self.log(message, "DEBUG")
    
    def get_messages(self):
        """Get all queued messages (non-blocking)"""
        messages = []
        while not self.message_queue.empty():
            try:
                messages.append(self.message_queue.get_nowait())
            except queue.Empty:
                break
        return messages
    
    def get_log_location(self):
        """Get the log file location for display to users"""
        return str(self.log_dir / LOG_FILE)


# Singleton pattern to ensure only one logger instance
_logger_instance = None

def get_app_logger():
    """Get or create the singleton logger instance"""
    global _logger_instance
    if _logger_instance is None:
        _logger_instance = AppLogger()
    return _logger_instance

# Global logger instance (singleton)
app_logger = get_app_logger()
