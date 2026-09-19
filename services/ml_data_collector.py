"""
ML Data Contribution Collector

Collects anonymized training data from opt-in users to improve ML models.
Saves downscaled images (256x256) and calibration JSON in the same format
as the existing ML training pipeline for seamless integration.

Data is stored locally until the user exports and uploads via Google Form.
"""
import os
import json
import shutil
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Dict, Any, Tuple

import numpy as np

from services.logger import app_logger
from .reveal_in_file_manager import reveal_path
from .utils_paths import get_ml_contribution_dir

# Optional: astropy for FITS
# Astropy is excluded from PyInstaller builds to save ~30MB.
# We use a lightweight FITS writer as fallback (sufficient for our simple needs).
try:
    from astropy.io import fits as astropy_fits
    # Verify PrimaryHDU is actually accessible (partial imports can succeed
    # but leave submodule attributes missing in PyInstaller bundles)
    _test = astropy_fits.PrimaryHDU
    del _test
    _astropy_fits = astropy_fits
    ASTROPY_AVAILABLE = True
except Exception:
    _astropy_fits = None
    ASTROPY_AVAILABLE = False


def _write_simple_fits(path: Path, data: np.ndarray, header_cards: dict):
    """Write a minimal FITS file without astropy.
    
    Supports a single Primary HDU with a 2D float32 image and string header cards.
    Compliant with FITS standard (2880-byte blocks, 80-char card images).
    """
    import struct

    data = data.astype(np.float32)
    if data.ndim != 2:
        raise ValueError(f"Expected 2D array, got {data.ndim}D")

    naxis1, naxis2 = data.shape[1], data.shape[0]  # FITS: NAXIS1=cols, NAXIS2=rows

    def _card(keyword: str, value, comment: str = '') -> bytes:
        """Format a single 80-byte FITS header card."""
        kw = keyword.upper().ljust(8)[:8]
        if isinstance(value, bool):
            val_str = 'T' if value else 'F'
            val_field = f"= {val_str:>20s}"
        elif isinstance(value, int):
            val_field = f"= {value:>20d}"
        elif isinstance(value, float):
            val_field = f"= {value:>20.6E}"
        else:
            # String value - must be quoted, left-justified within quotes
            s = str(value)[:68]
            val_field = f"= '{s}'"
            val_field = val_field.ljust(30)  # Pad for readability
        card = f"{kw}{val_field}"
        if comment:
            remaining = 80 - len(card) - 3  # space + / + space
            if remaining > 0:
                card += f" / {comment[:remaining]}"
        return card.ljust(80)[:80].encode('ascii', errors='replace')

    # Build header
    cards = []
    cards.append(_card('SIMPLE', True, 'Conforms to FITS standard'))
    cards.append(_card('BITPIX', -32, 'IEEE single precision float'))
    cards.append(_card('NAXIS', 2, 'Number of axes'))
    cards.append(_card('NAXIS1', naxis1, 'Width'))
    cards.append(_card('NAXIS2', naxis2, 'Height'))

    for key, val in header_cards.items():
        key_upper = key.upper()[:8]
        if key_upper not in ('SIMPLE', 'BITPIX', 'NAXIS', 'NAXIS1', 'NAXIS2', 'END'):
            cards.append(_card(key_upper, val))

    cards.append(b'END'.ljust(80))

    # Pad header to multiple of 2880 bytes (36 cards per block)
    while len(cards) % 36 != 0:
        cards.append(b' ' * 80)

    header_bytes = b''.join(cards)

    # Write data in big-endian (FITS standard)
    data_bytes = data.astype('>f4').tobytes()
    # Pad data to multiple of 2880
    pad_len = (2880 - len(data_bytes) % 2880) % 2880
    data_bytes += b'\x00' * pad_len

    with open(path, 'wb') as f:
        f.write(header_bytes)
        f.write(data_bytes)

# Google Form URL for data submission
UPLOAD_FORM_URL = "https://forms.gle/ZW5rEZC2eyQognDMA"

# Collection settings
DEFAULT_MIN_INTERVAL_MINUTES = 30
DEFAULT_MAX_SAMPLES = 500
TARGET_IMAGE_SIZE = 256  # Downscale to 256x256 for both roof (128) and sky (256) models


class MLDataCollector:
    """
    Handles collection of ML training data from opt-in users.
    
    Collects at most one sample per min_interval_minutes, stores locally,
    and provides export functionality for user upload.
    """
    
    def __init__(self, config_getter):
        """
        Initialize the collector.
        
        Args:
            config_getter: Callable that returns current config dict
        """
        self.get_config = config_getter
        self._contribution_dir = None
        self._samples_dir = None
        self._exports_dir = None
        self._manifest_path = None
        self._manifest = None
    
    @property
    def contribution_dir(self) -> Path:
        """Get/create the contribution directory."""
        if self._contribution_dir is None:
            self._contribution_dir = Path(get_ml_contribution_dir())
        return self._contribution_dir
    
    @property
    def samples_dir(self) -> Path:
        """Get/create the samples directory."""
        if self._samples_dir is None:
            self._samples_dir = self.contribution_dir / "samples"
            self._samples_dir.mkdir(parents=True, exist_ok=True)
        return self._samples_dir
    
    @property
    def exports_dir(self) -> Path:
        """Get/create the exports directory."""
        if self._exports_dir is None:
            self._exports_dir = self.contribution_dir / "exports"
            self._exports_dir.mkdir(parents=True, exist_ok=True)
        return self._exports_dir
    
    @property
    def manifest_path(self) -> Path:
        """Get path to manifest file."""
        if self._manifest_path is None:
            self._manifest_path = self.contribution_dir / "manifest.json"
        return self._manifest_path
    
    def _load_manifest(self) -> Dict[str, Any]:
        """Load or create the manifest file."""
        if self._manifest is not None:
            return self._manifest
        
        if self.manifest_path.exists():
            try:
                with open(self.manifest_path, 'r') as f:
                    self._manifest = json.load(f)
            except Exception as e:
                app_logger.warning(f"ML Contribution: Failed to load manifest: {e}")
                self._manifest = self._create_default_manifest()
        else:
            self._manifest = self._create_default_manifest()
        
        return self._manifest
    
    def _create_default_manifest(self) -> Dict[str, Any]:
        """Create a default manifest structure."""
        return {
            "version": "1.0",
            "created_at": datetime.now().isoformat(),
            "total_samples": 0,
            "last_collection": None,
            "samples": [],  # List of sample timestamps
            "cameras_seen": [],  # Unique camera models
        }
    
    def _save_manifest(self):
        """Save the manifest to disk."""
        if self._manifest is None:
            return
        
        try:
            self.contribution_dir.mkdir(parents=True, exist_ok=True)
            with open(self.manifest_path, 'w') as f:
                json.dump(self._manifest, f, indent=2)
        except Exception as e:
            app_logger.error(f"ML Contribution: Failed to save manifest: {e}")
    
    def is_enabled(self) -> bool:
        """Check if ML data contribution is enabled."""
        config = self.get_config()
        ml_contrib = config.get("ml_contribution", {})
        return ml_contrib.get("enabled", False)
    
    def get_min_interval(self) -> int:
        """Get minimum interval between collections in minutes."""
        config = self.get_config()
        ml_contrib = config.get("ml_contribution", {})
        return ml_contrib.get("min_interval_minutes", DEFAULT_MIN_INTERVAL_MINUTES)
    
    def get_max_samples(self) -> int:
        """Get maximum number of samples to store locally."""
        config = self.get_config()
        ml_contrib = config.get("ml_contribution", {})
        return ml_contrib.get("max_samples", DEFAULT_MAX_SAMPLES)
    
    def should_collect(self) -> Tuple[bool, str]:
        """
        Check if we should collect a sample now.
        
        Returns:
            Tuple of (should_collect, reason)
        """
        if not self.is_enabled():
            return False, "ML contribution disabled"
        
        manifest = self._load_manifest()
        
        # Check sample count limit
        if manifest["total_samples"] >= self.get_max_samples():
            return False, f"Max samples reached ({self.get_max_samples()})"
        
        # Check time interval
        last_collection = manifest.get("last_collection")
        if last_collection:
            try:
                last_time = datetime.fromisoformat(last_collection)
                min_interval = timedelta(minutes=self.get_min_interval())
                next_allowed = last_time + min_interval
                
                if datetime.now() < next_allowed:
                    remaining = (next_allowed - datetime.now()).total_seconds() / 60
                    return False, f"Too soon ({remaining:.1f} min remaining)"
            except Exception:
                pass  # If we can't parse, allow collection
        
        return True, "Ready to collect"
    
    def collect_sample(
        self,
        lum_array: np.ndarray,
        calibration_data: Dict[str, Any],
        metadata: Dict[str, Any]
    ) -> bool:
        """
        Collect a training sample if conditions are met.
        
        Args:
            lum_array: Full-resolution luminance array (will be downscaled)
            calibration_data: The calibration dict from dev_mode_utils
            metadata: Image metadata dict
            
        Returns:
            True if sample was collected, False otherwise
        """
        should, reason = self.should_collect()
        if not should:
            return False
        
        try:
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            
            # Downscale luminance to target size
            lum_small = self._downscale_image(lum_array, TARGET_IMAGE_SIZE)
            
            # Save downscaled FITS
            fits_path = self.samples_dir / f"lum_{timestamp}.fits"
            self._save_fits(fits_path, lum_small, metadata)
            
            # Prepare calibration data (remove privacy-sensitive fields)
            clean_cal = self._anonymize_calibration(calibration_data)
            
            # Add contribution metadata
            clean_cal["_contribution"] = {
                "version": "1.0",
                "collected_at": datetime.now().isoformat(),
                "image_size": TARGET_IMAGE_SIZE,
                "original_shape": list(lum_array.shape),
            }
            
            # Save calibration JSON
            json_path = self.samples_dir / f"calibration_{timestamp}.json"
            with open(json_path, 'w') as f:
                json.dump(clean_cal, f, indent=2)
            
            # Update manifest
            manifest = self._load_manifest()
            manifest["total_samples"] += 1
            manifest["last_collection"] = datetime.now().isoformat()
            manifest["samples"].append(timestamp)
            
            # Track unique cameras
            camera = metadata.get("CAMERA", "Unknown")
            if camera and camera not in manifest["cameras_seen"]:
                manifest["cameras_seen"].append(camera)
            
            self._save_manifest()
            
            app_logger.info(
                f"ML Contribution: Collected sample {timestamp} "
                f"({manifest['total_samples']}/{self.get_max_samples()})"
            )
            return True
            
        except Exception as e:
            app_logger.error(f"ML Contribution: Failed to collect sample: {e}")
            return False
    
    def _downscale_image(self, img: np.ndarray, target_size: int) -> np.ndarray:
        """
        Downscale image to target size using block averaging.
        
        Args:
            img: Input image array (2D)
            target_size: Target width/height
            
        Returns:
            Downscaled image array
        """
        if img.ndim != 2:
            # If 3D (RGB), take first channel or compute luminance
            if img.ndim == 3:
                if img.shape[2] == 3:
                    # RGB to luminance
                    img = 0.299 * img[:,:,0] + 0.587 * img[:,:,1] + 0.114 * img[:,:,2]
                else:
                    img = img[:,:,0]
        
        h, w = img.shape
        
        # If already smaller than target, just return
        if h <= target_size and w <= target_size:
            return img.astype(np.float32)
        
        # Calculate block size for averaging
        block_h = h // target_size
        block_w = w // target_size
        
        if block_h == 0:
            block_h = 1
        if block_w == 0:
            block_w = 1
        
        # Trim to exact multiple
        trim_h = block_h * target_size
        trim_w = block_w * target_size
        trimmed = img[:trim_h, :trim_w].astype(np.float32)
        
        # Reshape and average
        result = trimmed.reshape(target_size, block_h, target_size, block_w).mean(axis=(1, 3))
        
        return result
    
    def _save_fits(self, path: Path, data: np.ndarray, metadata: Dict[str, Any]):
        """Save image data as FITS file with metadata header."""
        header_cards = {
            'CAMERA': metadata.get('CAMERA', 'Unknown'),
            'EXPOSURE': str(metadata.get('EXPOSURE', 'N/A')),
            'GAIN': str(metadata.get('GAIN', 'N/A')),
            'DATE-OBS': metadata.get('DATETIME', datetime.now().isoformat()),
            'IMGSIZE': TARGET_IMAGE_SIZE,
            'MLCONTRI': True,
        }
        
        if ASTROPY_AVAILABLE:
            hdu = _astropy_fits.PrimaryHDU(data.astype(np.float32))
            for key, val in header_cards.items():
                hdu.header[key] = val
            hdu.writeto(path, overwrite=True)
        else:
            _write_simple_fits(path, data, header_cards)
    
    def _anonymize_calibration(self, cal: Dict[str, Any]) -> Dict[str, Any]:
        """
        Remove privacy-sensitive fields from calibration data.
        
        Removes:
        - GPS coordinates (lat/lon)
        - File paths
        - URLs
        - Weather location name
        """
        import copy
        clean = copy.deepcopy(cal)
        
        # Remove location from time_context
        if 'time_context' in clean:
            tc = clean['time_context']
            if 'location' in tc:
                # Keep only timezone info if present, remove coords
                tc['location'] = {'name': 'anonymized'}
        
        # Remove weather location
        if 'weather_context' in clean:
            wc = clean['weather_context']
            wc.pop('location', None)
            wc.pop('city', None)
        
        # Remove allsky snapshot URL and path
        if 'allsky_snapshot' in clean:
            ass = clean['allsky_snapshot']
            ass.pop('url', None)
            ass.pop('saved_path', None)
            ass.pop('filename', None)
        
        # Remove any file paths
        for key in list(clean.keys()):
            if isinstance(clean[key], str):
                if '\\' in clean[key] or clean[key].startswith('/'):
                    clean[key] = '[path removed]'
        
        return clean
    
    def get_stats(self) -> Dict[str, Any]:
        """Get statistics about collected samples."""
        manifest = self._load_manifest()
        
        # Calculate disk usage
        disk_usage_bytes = 0
        if self.samples_dir.exists():
            for f in self.samples_dir.iterdir():
                if f.is_file():
                    disk_usage_bytes += f.stat().st_size
        
        return {
            "enabled": self.is_enabled(),
            "total_samples": manifest.get("total_samples", 0),
            "max_samples": self.get_max_samples(),
            "last_collection": manifest.get("last_collection"),
            "cameras_seen": manifest.get("cameras_seen", []),
            "disk_usage_bytes": disk_usage_bytes,
            "disk_usage_mb": round(disk_usage_bytes / (1024 * 1024), 2),
            "samples_dir": str(self.samples_dir),
            "upload_url": UPLOAD_FORM_URL,
        }
    
    def export_for_upload(self) -> Optional[Path]:
        """
        Create a ZIP file of all samples for upload.
        
        Returns:
            Path to created ZIP file, or None if failed
        """
        manifest = self._load_manifest()
        
        if manifest["total_samples"] == 0:
            app_logger.warning("ML Contribution: No samples to export")
            return None
        
        try:
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            zip_name = f"PFRSentinel_ML_{timestamp}.zip"
            zip_path = self.exports_dir / zip_name
            
            # Create ZIP with all samples
            with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
                # Add all sample files
                for sample_file in self.samples_dir.iterdir():
                    if sample_file.is_file():
                        zf.write(sample_file, sample_file.name)
                
                # Add manifest
                zf.write(self.manifest_path, "manifest.json")
            
            file_size_mb = zip_path.stat().st_size / (1024 * 1024)
            app_logger.info(
                f"ML Contribution: Exported {manifest['total_samples']} samples "
                f"to {zip_name} ({file_size_mb:.1f} MB)"
            )
            
            return zip_path
            
        except Exception as e:
            app_logger.error(f"ML Contribution: Export failed: {e}")
            return None
    
    def clear_samples(self) -> bool:
        """
        Clear all collected samples (after successful upload).
        
        Returns:
            True if successful
        """
        try:
            # Remove all sample files
            if self.samples_dir.exists():
                for f in self.samples_dir.iterdir():
                    if f.is_file():
                        f.unlink()
            
            # Reset manifest
            self._manifest = self._create_default_manifest()
            self._save_manifest()
            
            app_logger.info("ML Contribution: Cleared all samples")
            return True
            
        except Exception as e:
            app_logger.error(f"ML Contribution: Failed to clear samples: {e}")
            return False
    
    def open_upload_form(self):
        """Open the Google Form for upload in default browser."""
        import webbrowser
        webbrowser.open(UPLOAD_FORM_URL)
    
    def open_samples_folder(self):
        """Open the samples folder in the OS file manager."""
        reveal_path(str(self.samples_dir))

    def open_exports_folder(self):
        """Open the exports folder in the OS file manager."""
        reveal_path(str(self.exports_dir))


# Singleton instance - initialized lazily when config is available
_collector_instance: Optional[MLDataCollector] = None


def get_ml_collector(config_getter=None) -> Optional[MLDataCollector]:
    """
    Get the singleton ML data collector instance.
    
    Args:
        config_getter: Callable returning config dict (required on first call)
    
    Returns:
        MLDataCollector instance or None if not initialized
    """
    global _collector_instance
    
    if _collector_instance is None and config_getter is not None:
        _collector_instance = MLDataCollector(config_getter)
    
    return _collector_instance


def init_ml_collector(config_getter) -> MLDataCollector:
    """
    Initialize the ML data collector with config access.
    
    Args:
        config_getter: Callable returning config dict
        
    Returns:
        MLDataCollector instance
    """
    global _collector_instance
    _collector_instance = MLDataCollector(config_getter)
    return _collector_instance
