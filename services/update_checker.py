"""
Update Checker Service
Checks GitHub releases for new versions with rate-limit friendly design.
- Checks once per 24 hours after app startup
- Caches last check time to avoid excessive API calls
- Downloads installer to user's Downloads folder
"""

import os
import re
import json
import time
import threading
import webbrowser
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional, Callable
from dataclasses import dataclass

import requests

from version import __version__
from services.logger import app_logger
from .utils_paths import get_app_data_dir


# GitHub API endpoint - rate limited to 60 requests/hour for unauthenticated
GITHUB_API_URL = "https://api.github.com/repos/englishfox90/PFRSentinel/releases/latest"
GITHUB_RELEASES_PAGE = "https://github.com/englishfox90/PFRSentinel/releases/latest"

# Check interval: 24 hours
CHECK_INTERVAL_HOURS = 24

# Cache file for last check time
CACHE_FILENAME = "update_check_cache.json"


@dataclass
class UpdateInfo:
    """Information about an available update."""
    current_version: str
    latest_version: str
    release_name: str
    release_notes: str
    download_url: str
    installer_name: str
    installer_size_mb: float
    published_at: str
    html_url: str


def parse_version(version_str: str) -> tuple:
    """
    Parse version string to comparable tuple.
    Handles formats like "3.2.5", "v3.2.5", "3.2.5-beta", "3.7.7-dev.14"

    Any suffix ranks below the plain release (semver precedence). Dev builds
    are stamped with the version they lead up to, so a tester on 3.7.7-dev.14
    must still be offered 3.7.7 when it ships.
    """
    clean = version_str.lstrip('v')
    match = re.match(r'(\d+)\.(\d+)\.(\d+)(-[^\d]*(\d+)?)?', clean)
    if match:
        major, minor, patch, suffix, counter = match.groups()
        is_release = 0 if suffix else 1
        return (int(major), int(minor), int(patch), is_release, int(counter or 0))
    return (0, 0, 0, 0, 0)


def compare_versions(current: str, latest: str) -> int:
    """
    Compare two version strings.
    Returns: -1 if current < latest, 0 if equal, 1 if current > latest
    """
    current_tuple = parse_version(current)
    latest_tuple = parse_version(latest)
    
    if current_tuple < latest_tuple:
        return -1
    elif current_tuple > latest_tuple:
        return 1
    return 0


class UpdateChecker:
    """
    Manages update checking with rate limiting and caching.
    
    Usage:
        checker = UpdateChecker(on_update_available=my_callback)
        checker.start_delayed_check(delay_hours=24)  # Check 24h after boot
    """
    
    def __init__(self, on_update_available: Optional[Callable[[UpdateInfo], None]] = None):
        """
        Initialize the update checker.
        
        Args:
            on_update_available: Callback when update is found. Called with UpdateInfo.
        """
        self.on_update_available = on_update_available
        self._check_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._last_update_info: Optional[UpdateInfo] = None
        self._cache_path = Path(get_app_data_dir()) / CACHE_FILENAME
        
    def _load_cache(self) -> dict:
        """Load cached check data."""
        try:
            if self._cache_path.exists():
                with open(self._cache_path, 'r') as f:
                    return json.load(f)
        except Exception as e:
            app_logger.warning(f"Failed to load update cache: {e}")
        return {}
    
    def _save_cache(self, data: dict):
        """Save cache data."""
        try:
            with open(self._cache_path, 'w') as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            app_logger.warning(f"Failed to save update cache: {e}")
    
    def _should_check(self) -> bool:
        """Determine if we should check for updates based on cache."""
        cache = self._load_cache()
        last_check = cache.get('last_check_timestamp')
        
        if not last_check:
            return True

        last_check_time = datetime.fromisoformat(last_check)
        hours_since = (datetime.now() - last_check_time).total_seconds() / 3600

        if hours_since < CHECK_INTERVAL_HOURS:
            app_logger.debug(
                f"Update check skipped — last checked {hours_since:.1f}h ago "
                f"(next in {CHECK_INTERVAL_HOURS - hours_since:.1f}h)"
            )
            return False
        return True
    
    def _record_check(self):
        """Record that we performed a check."""
        cache = self._load_cache()
        cache['last_check_timestamp'] = datetime.now().isoformat()
        self._save_cache(cache)
    
    def check_for_update(self, force: bool = False) -> Optional[UpdateInfo]:
        """
        Check GitHub for updates.
        
        Args:
            force: If True, check regardless of cache time
            
        Returns:
            UpdateInfo if update available, None otherwise
        """
        if not force and not self._should_check():
            return self._last_update_info

        if force:
            app_logger.info("Update check: forced by user")
        app_logger.info("Checking for updates...")
        
        try:
            response = requests.get(
                GITHUB_API_URL,
                headers={
                    'Accept': 'application/vnd.github.v3+json',
                    'User-Agent': f'PFRSentinel/{__version__}'
                },
                timeout=10
            )
            response.raise_for_status()
            
            data = response.json()
            
            # Record successful check
            self._record_check()

            # /releases/latest already excludes these. The check keeps the dev
            # channel (a rolling prerelease) out of production installs should
            # this ever poll a listing that includes them.
            if data.get('prerelease') or data.get('draft'):
                app_logger.info(f"Ignoring prerelease {data.get('tag_name')}")
                self._last_update_info = None
                return None

            # Parse version from tag
            latest_version = data.get('tag_name', '').lstrip('v')
            current_version = __version__
            
            app_logger.info(f"Current: v{current_version}, Latest: v{latest_version}")
            
            # Compare versions
            if compare_versions(current_version, latest_version) >= 0:
                app_logger.info("Already up to date!")
                self._last_update_info = None
                return None
            
            # Find installer asset
            download_url = ""
            installer_name = ""
            installer_size_mb = 0.0
            
            for asset in data.get('assets', []):
                name = asset.get('name', '')
                if name.endswith('.exe') and 'setup' in name.lower():
                    download_url = asset.get('browser_download_url', '')
                    installer_name = name
                    installer_size_mb = asset.get('size', 0) / (1024 * 1024)
                    break
            
            # Build update info
            update_info = UpdateInfo(
                current_version=current_version,
                latest_version=latest_version,
                release_name=data.get('name', f'v{latest_version}'),
                release_notes=data.get('body', 'No release notes available.'),
                download_url=download_url,
                installer_name=installer_name,
                installer_size_mb=installer_size_mb,
                published_at=data.get('published_at', ''),
                html_url=data.get('html_url', GITHUB_RELEASES_PAGE)
            )
            
            self._last_update_info = update_info
            
            app_logger.info(f"Update available: v{latest_version} ({installer_size_mb:.1f} MB)")
            
            # Trigger callback
            if self.on_update_available:
                self.on_update_available(update_info)
            
            return update_info
            
        except requests.exceptions.RequestException as e:
            app_logger.warning(f"Failed to check for updates: {e}")
            return None
        except Exception as e:
            app_logger.error(f"Update check error: {e}")
            return None
    
    def start_delayed_check(self, delay_hours: float = 24.0):
        """
        Start a background thread that checks for updates after a delay.
        
        Args:
            delay_hours: Hours to wait before first check (default 24h)
        """
        if self._check_thread and self._check_thread.is_alive():
            app_logger.debug("Update check thread already running")
            return
        
        self._stop_event.clear()
        
        def delayed_check():
            delay_seconds = delay_hours * 3600
            app_logger.debug(f"Update checker will run in {delay_hours} hours")
            
            # Wait for delay, checking stop event periodically
            start = time.time()
            while time.time() - start < delay_seconds:
                if self._stop_event.wait(timeout=60):  # Check every minute
                    app_logger.debug("Update checker stopped during delay")
                    return

            # Perform check
            if not self._stop_event.is_set():
                app_logger.debug("Update checker: scheduled delay elapsed, running check")
                self.check_for_update()
        
        self._check_thread = threading.Thread(
            target=delayed_check,
            name="UpdateChecker",
            daemon=True
        )
        self._check_thread.start()
        app_logger.debug("Update checker thread started")
    
    def stop(self):
        """Stop the background check thread."""
        self._stop_event.set()
        if self._check_thread:
            self._check_thread.join(timeout=2)
            self._check_thread = None
    
    def get_last_update_info(self) -> Optional[UpdateInfo]:
        """Get cached update info from last check."""
        return self._last_update_info
    
    def download_installer(
        self, 
        update_info: UpdateInfo,
        progress_callback: Optional[Callable[[int, int], None]] = None
    ) -> Optional[Path]:
        """
        Download the installer to user's Downloads folder.
        
        Args:
            update_info: The update info with download URL
            progress_callback: Optional callback(downloaded_bytes, total_bytes)
            
        Returns:
            Path to downloaded file, or None if failed
        """
        if not update_info.download_url:
            app_logger.error("No download URL available")
            return None
        
        # Get Downloads folder
        downloads_dir = Path.home() / "Downloads"
        if not downloads_dir.exists():
            downloads_dir = Path.home()
        
        output_path = downloads_dir / update_info.installer_name
        
        app_logger.info(f"Downloading {update_info.installer_name} to {output_path}")
        
        try:
            response = requests.get(
                update_info.download_url,
                stream=True,
                timeout=30,
                headers={'User-Agent': f'PFRSentinel/{__version__}'}
            )
            response.raise_for_status()
            
            total_size = int(response.headers.get('content-length', 0))
            downloaded = 0
            _last_logged_pct = -1

            # Use larger chunk size (256KB) for faster downloads
            with open(output_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=262144):
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        if progress_callback:
                            progress_callback(downloaded, total_size)
                        # Log at 25 / 50 / 75 % milestones
                        if total_size > 0:
                            pct = int(downloaded * 100 / total_size)
                            milestone = (pct // 25) * 25
                            if milestone > _last_logged_pct and milestone in (25, 50, 75):
                                _last_logged_pct = milestone
                                mb = downloaded / (1024 * 1024)
                                app_logger.debug(
                                    f"Downloading update: {milestone}% "
                                    f"({mb:.1f} / {total_size / (1024*1024):.1f} MB)"
                                )
            
            app_logger.info(f"Download complete: {output_path}")
            return output_path
            
        except Exception as e:
            app_logger.error(f"Download failed: {e}")
            # Clean up partial download
            if output_path.exists():
                try:
                    output_path.unlink()
                except OSError:
                    pass
            return None
    
    def open_releases_page(self, update_info: Optional[UpdateInfo] = None):
        """Open the GitHub releases page in browser."""
        url = update_info.html_url if update_info else GITHUB_RELEASES_PAGE
        webbrowser.open(url)
        app_logger.info(f"Opened releases page: {url}")


# Singleton instance for app-wide use
_update_checker: Optional[UpdateChecker] = None


def get_update_checker(
    on_update_available: Optional[Callable[[UpdateInfo], None]] = None
) -> UpdateChecker:
    """Get or create the global update checker instance."""
    global _update_checker
    if _update_checker is None:
        _update_checker = UpdateChecker(on_update_available)
    elif on_update_available:
        _update_checker.on_update_available = on_update_available
    return _update_checker
