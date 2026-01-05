"""
Image Processor for Qt UI
Handles image processing pipeline using services/processor.py functions
"""
from PySide6.QtCore import QObject, Signal, QThread
from PIL import Image, ImageEnhance, ImageDraw, ImageFont
import numpy as np
import os
import json
import queue
import traceback
from datetime import datetime

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from services.logger import app_logger
from services.processor import add_overlays, auto_stretch_image


class ImageProcessingTask:
    """Encapsulates an image processing task"""
    def __init__(self, img: Image.Image, metadata: dict, config: dict):
        self.img = img.copy()  # Copy to avoid race conditions
        self.metadata = metadata.copy() if metadata else {}
        self.config = config.copy() if config else {}


class ImageProcessorWorker(QThread):
    """Background worker for image processing"""
    
    # Signals
    processing_complete = Signal(object, dict, str)  # processed PIL Image, metadata, output_path
    preview_ready = Signal(object, dict)  # PIL Image for preview, histogram data
    error_occurred = Signal(str)
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self._queue = queue.Queue(maxsize=10)
        self._running = False
        self._weather_service = None
        self._main_window = None  # Reference to main window for camera access
    
    def set_weather_service(self, weather_service):
        """Set weather service for overlay tokens"""
        self._weather_service = weather_service
    
    def set_main_window(self, main_window):
        """Set main window reference for camera access"""
        self._main_window = main_window
    
    def run(self):
        """Main processing loop"""
        self._running = True
        app_logger.debug("Image processing worker started")
        
        while self._running:
            try:
                # Wait for task with timeout so we can check _running
                try:
                    task = self._queue.get(timeout=0.5)
                except queue.Empty:
                    continue
                
                if task is None:  # Sentinel to stop
                    break
                
                self._process_task(task)
                self._queue.task_done()
                
            except Exception as e:
                app_logger.error(f"Processing worker error: {e}")
                app_logger.error(traceback.format_exc())
        
        app_logger.debug("Image processing worker stopped")
    
    def stop(self):
        """Stop the worker thread"""
        self._running = False
        try:
            self._queue.put_nowait(None)  # Sentinel
        except queue.Full:
            pass
    
    def queue_task(self, task: ImageProcessingTask):
        """Queue a processing task"""
        try:
            self._queue.put_nowait(task)
        except queue.Full:
            app_logger.warning("Processing queue full, dropping task")
    
    def _process_task(self, task: ImageProcessingTask):
        """Process a single image task"""
        try:
            img = task.img
            metadata = task.metadata
            config = task.config
            
            # Extract config values
            output_dir = config.get('output_dir', '')
            output_format = config.get('output_format', 'PNG')
            jpg_quality = config.get('jpg_quality', 85)
            resize_percent = config.get('resize_percent', 100)
            auto_brightness = config.get('auto_brightness', False)
            brightness_factor = config.get('brightness_factor', 1.0)
            saturation_factor = config.get('saturation_factor', 1.0)
            timestamp_corner = config.get('timestamp_corner', False)
            filename_pattern = config.get('filename_pattern', '{filename}')
            overlays = config.get('overlays', [])
            auto_stretch_config = config.get('auto_stretch', {})
            dev_mode_config = config.get('dev_mode', {})
            
            # DEBUG: Log dev mode status
            app_logger.debug(f"Dev mode config: enabled={dev_mode_config.get('enabled', False)}, save_stats={dev_mode_config.get('save_histogram_stats', True)}")
            
            if not output_dir:
                app_logger.error("Output directory not configured")
                self.error_occurred.emit("Output directory not configured")
                return
            
            os.makedirs(output_dir, exist_ok=True)
            
            # Calculate RAW histogram before processing
            # For dev mode: prefer 16-bit raw if available, then 8-bit no-WB, then image
            raw_array = metadata.get('RAW_RGB_16BIT')  # Full 16-bit if RAW16 mode
            if raw_array is None:
                raw_array = metadata.get('RAW_RGB_NO_WB')  # 8-bit pre-WB fallback
            if raw_array is None:
                raw_array = np.asarray(img)  # Final fallback for watch mode
            
            # === DEV MODE: Save raw image and log detailed stats ===
            if dev_mode_config.get('enabled', False):
                self._save_dev_mode_data(img, raw_array, output_dir, metadata, dev_mode_config)
            
            # Get auto-exposure settings for histogram display
            # Check if camera controller exists and has auto_exposure enabled
            zwo_auto_exposure = False
            target_brightness = 30
            
            if self._main_window:
                if hasattr(self._main_window, 'camera_controller') and self._main_window.camera_controller:
                    if hasattr(self._main_window.camera_controller, 'zwo_camera') and self._main_window.camera_controller.zwo_camera:
                        zwo_auto_exposure = self._main_window.camera_controller.zwo_camera.auto_exposure
                        target_brightness = self._main_window.camera_controller.zwo_camera.target_brightness
                        app_logger.debug(f"Histogram config from camera: auto_exposure={zwo_auto_exposure}, target={target_brightness}")
            
            # Calculate histogram - use appropriate range based on bit depth
            # 16-bit data needs to be scaled down for 256-bin histogram display
            if raw_array.dtype == np.uint16:
                # Scale 16-bit to 8-bit range for histogram display
                hist_array = (raw_array / 257).astype(np.uint8)
            else:
                hist_array = raw_array
            
            hist_data = {
                'r': np.histogram(hist_array[:, :, 0], bins=256, range=(0, 256))[0],
                'g': np.histogram(hist_array[:, :, 1], bins=256, range=(0, 256))[0],
                'b': np.histogram(hist_array[:, :, 2], bins=256, range=(0, 256))[0],
                'auto_exposure': zwo_auto_exposure,
                'target_brightness': target_brightness
            }
            del hist_array
            # Note: We keep metadata['RAW_RGB_16BIT'] alive for auto-stretch below
            
            # Resize if needed (only for 8-bit PIL image, 16-bit handled in stretch)
            if resize_percent < 100:
                new_width = int(img.width * resize_percent / 100)
                new_height = int(img.height * resize_percent / 100)
                img = img.resize((new_width, new_height), Image.Resampling.LANCZOS)
            
            # Apply auto-stretch (MTF) if enabled
            # Use 16-bit raw data when available for higher precision stretching
            if auto_stretch_config.get('enabled', False):
                raw_16bit = metadata.get('RAW_RGB_16BIT')  # Will be None if RAW8 mode
                if raw_16bit is not None:
                    # Resize 16-bit data if needed to match PIL image size
                    if resize_percent < 100:
                        import cv2
                        new_height = int(raw_16bit.shape[0] * resize_percent / 100)
                        new_width = int(raw_16bit.shape[1] * resize_percent / 100)
                        raw_16bit = cv2.resize(raw_16bit, (new_width, new_height), interpolation=cv2.INTER_LANCZOS4)
                img = auto_stretch_image(img, auto_stretch_config, raw_16bit=raw_16bit)
                app_logger.debug("Applied auto-stretch")
            
            # Cache stretched image for preview
            stretched_for_preview = img.copy()
            
            # Apply auto brightness
            if auto_brightness:
                gray_img = img.convert('L')
                img_array = np.asarray(gray_img)
                mean_brightness = np.mean(img_array)
                del gray_img
                
                target_brightness = 128
                auto_factor = target_brightness / max(mean_brightness, 10)
                auto_factor = max(0.5, min(auto_factor, 4.0))
                manual_factor = brightness_factor if brightness_factor else 1.0
                final_factor = auto_factor * manual_factor
                
                enhancer = ImageEnhance.Brightness(img)
                img = enhancer.enhance(final_factor)
                app_logger.debug(f"Auto brightness: mean={mean_brightness:.1f}, factor={final_factor:.2f}")
            
            # Apply saturation
            if saturation_factor != 1.0:
                enhancer = ImageEnhance.Color(img)
                img = enhancer.enhance(saturation_factor)
                app_logger.debug(f"Saturation adjusted: factor={saturation_factor:.2f}")
            
            # Add timestamp corner
            if timestamp_corner:
                draw = ImageDraw.Draw(img)
                timestamp_text = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                try:
                    font = ImageFont.truetype("arial.ttf", 20)
                except:
                    font = ImageFont.load_default()
                draw.text((img.width - 200, 10), timestamp_text, fill='white', font=font)
            
            # Add overlays using services/processor.py function
            img = add_overlays(img, overlays, metadata, weather_service=self._weather_service)
            
            # Generate output path
            session = metadata.get('session', datetime.now().strftime('%Y-%m-%d'))
            original_filename = metadata.get('FILENAME', 'capture.png')
            base_filename = os.path.splitext(original_filename)[0]
            output_filename = filename_pattern.replace('{filename}', base_filename)
            output_filename = output_filename.replace('{session}', session)
            output_filename = output_filename.replace('{timestamp}', datetime.now().strftime('%Y%m%d_%H%M%S'))
            output_filename += '.png' if output_format.lower() == 'png' else '.jpg'
            output_path = os.path.join(output_dir, output_filename)
            
            # Save to disk
            if output_format.lower() == 'png':
                img.save(output_path, 'PNG')
            else:
                img.save(output_path, 'JPEG', quality=jpg_quality)
            
            app_logger.info(f"Saved: {os.path.basename(output_path)}")
            
            # Clean up large arrays from metadata before emitting (avoid memory leaks)
            metadata.pop('RAW_RGB_16BIT', None)
            metadata.pop('RAW_RGB_NO_WB', None)
            
            # Emit preview signal with stretched image and histogram
            self.preview_ready.emit(stretched_for_preview, hist_data)
            
            # Emit completion signal
            self.processing_complete.emit(img, metadata, output_path)
            
        except Exception as e:
            app_logger.error(f"Image processing failed: {e}")
            app_logger.error(traceback.format_exc())
            self.error_occurred.emit(str(e))
    
    def _save_dev_mode_data(self, img, raw_array, output_dir, metadata, dev_config):
        """
        Save raw image and detailed statistics for debugging stretch issues.
        
        Improvements:
        - Preserves true dynamic range (no fake scaling for RAW16)
        - Correct normalization based on actual bit depth
        - Saves grayscale luminance FITS for stretch calibration
        - Outputs JSON with stretch calibration parameters
        
        Args:
            img: PIL Image (raw, pre-processing)
            raw_array: numpy array of the image (uint8 or uint16, mono or RGB)
            output_dir: Base output directory
            metadata: Image metadata dict
            dev_config: Dev mode config dict
        """
        app_logger.info("=== DEV MODE: Starting raw image save ===")
        try:
            # Create raw debug subfolder
            raw_folder = dev_config.get('raw_folder', 'raw_debug')
            raw_dir = os.path.join(output_dir, raw_folder)
            os.makedirs(raw_dir, exist_ok=True)
            
            # Generate filename with timestamp
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            
            # Get camera bit depth info from metadata
            camera_bit_depth = metadata.get('CAMERA_BIT_DEPTH', 8)  # ADC bit depth (e.g., 12)
            image_bit_depth = metadata.get('IMAGE_BIT_DEPTH', 8)    # Capture mode (RAW8=8, RAW16=16)
            
            # Infer correct normalization denominator with full diagnostics
            denom, denom_reason, denom_details = self._infer_normalization_denom(raw_array, image_bit_depth, camera_bit_depth)
            
            # Log single comprehensive line with all key diagnostics
            app_logger.info(
                f"DEV MODE Raw Values: min={denom_details['raw_min']}, "
                f"median={denom_details.get('raw_median', 'N/A')}, "
                f"p99={denom_details.get('raw_p99', 'N/A')}, "
                f"max={denom_details['raw_max']}, "
                f"denom={int(denom)}, "
                f"mul16_rate={denom_details['mul16_rate']:.3f}, "
                f"unique_ratio={denom_details['unique_ratio']:.4f}, "
                f"reason=\"{denom_reason}\""
            )
            
            app_logger.info(f"DEV MODE: Camera ADC: {camera_bit_depth}-bit, Image mode: {image_bit_depth}-bit, Denom: {denom} ({denom_reason})")
            
            # Note: Raw diagnostics already logged above, no separate call needed
            
            # Base header info for FITS files
            base_header = {
                'CAMERA': metadata.get('CAMERA', 'Unknown'),
                'EXPOSURE': metadata.get('EXPOSURE', 'N/A'),
                'GAIN': metadata.get('GAIN', 'N/A'),
                'TEMP': metadata.get('TEMP', 'N/A'),
                'DATE-OBS': metadata.get('DATETIME', datetime.now().isoformat()),
                'INSTRUME': metadata.get('CAMERA', 'ZWO ASI Camera'),
                'BITDEPTH': (camera_bit_depth, 'Camera ADC bit depth'),
                'IMGBITS': (image_bit_depth, 'Original image bit depth'),
                'NORMDNOM': (denom, 'Normalization denominator used'),
                'BAYERPAT': metadata.get('BAYER_PATTERN', 'BGGR'),
                'PIXSIZE': (metadata.get('PIXEL_SIZE', 0), 'Pixel size in microns'),
                'EGAIN': (metadata.get('ELEC_PER_ADU', 1.0), 'Electrons per ADU'),
            }
            
            # === Save raw RGB/mono FITS ===
            raw_fits_path = os.path.join(raw_dir, f"raw_{timestamp}.fits")
            self._save_raw_fits(raw_fits_path, raw_array, image_bit_depth, base_header)
            
            # === Compute normalized array for statistics ===
            norm_array = raw_array.astype(np.float32) / denom
            
            # === Log per-channel statistics ===
            if dev_config.get('save_histogram_stats', True):
                self._log_channel_statistics(norm_array, raw_array)
            
            # === Compute and save grayscale luminance FITS ===
            # Grayscale/luminance helps stretch calibration:
            # - Stabilizes percentile-based black/white point selection
            # - Makes stretch repeatable independent of RGB color balance
            # - Dark scene detection based on luminance, not individual channels
            lum = self._compute_luminance(norm_array)
            
            lum_fits_path = os.path.join(raw_dir, f"lum_{timestamp}.fits")
            lum_header = base_header.copy()
            lum_header['COMMENT'] = 'Grayscale luminance (0.299R + 0.587G + 0.114B) for stretch calibration'
            lum_header['DATATYPE'] = ('float32', 'Luminance in 0..1 range')
            self._write_fits(lum_fits_path, lum.astype(np.float32), lum_header)
            app_logger.info(f"DEV MODE: ✓ Saved luminance FITS to lum_{timestamp}.fits (shape: {lum.shape})")
            
            # === Generate and save stretch calibration JSON ===
            calibration = self._compute_stretch_calibration(lum, norm_array, metadata, denom, denom_reason, denom_details)
            json_path = os.path.join(raw_dir, f"calibration_{timestamp}.json")
            self._write_json(json_path, calibration)
            app_logger.info(f"DEV MODE: ✓ Saved calibration JSON to calibration_{timestamp}.json")
            
            # Log key calibration values
            app_logger.info(
                f"DEV MODE Calibration: black_pt={calibration['stretch']['black_point']:.4f}, "
                f"white_pt={calibration['stretch']['white_point']:.4f}, "
                f"median_lum={calibration['stretch']['median_lum']:.4f}, "
                f"asinh_strength={calibration['stretch']['recommended_asinh_strength']:.1f}"
            )
            
        except Exception as e:
            app_logger.error(f"DEV MODE: Failed to save debug data: {e}")
            app_logger.error(traceback.format_exc())
    
    def _infer_normalization_denom(self, raw_array, image_bit_depth, camera_bit_depth):
        """
        Infer the correct normalization denominator based on actual data.
        
        Analyzes actual pixel values to detect:
        - 8-bit payload in 16-bit container (e.g., RAW8 data when IMAGE_BIT_DEPTH=16)
        - 12-bit data (max <= 4095)
        - 12-bit left-shifted to 16-bit (many multiples of 16)
        - True 16-bit data
        
        Example: raw_max=254 yields denom=255 with reason explaining 8-bit payload.
        
        Args:
            raw_array: numpy array (uint8 or uint16), shape (H,W) or (H,W,3)
            image_bit_depth: Reported image bit depth from camera (8 or 16)
            camera_bit_depth: Camera ADC bit depth (e.g., 12 for ASI676MC)
            
        Returns:
            (denom, reason, details): 
                - denom: float normalization denominator (255, 4095, or 65535)
                - reason: str explaining why this denom was chosen
                - details: dict with diagnostic info (raw_min, raw_max, mul16_rate, unique_ratio, etc.)
        """
        # Early exit for 8-bit capture mode
        if image_bit_depth == 8:
            return 255.0, "8-bit capture mode (IMAGE_BIT_DEPTH=8)", {
                'raw_min': int(np.min(raw_array)),
                'raw_max': int(np.max(raw_array)),
                'mul16_rate': 0.0,
                'unique_ratio': 1.0,
                'unique_count': 0,  # Not computed for 8-bit
            }
        
        # For 16-bit container, analyze actual values
        # Use uint16 view for consistent analysis (handles both uint8 and uint16 inputs)
        if raw_array.dtype == np.uint8:
            # Should not happen for IMAGE_BIT_DEPTH=16, but handle gracefully
            return 255.0, "8-bit array dtype despite IMAGE_BIT_DEPTH=16", {
                'raw_min': int(np.min(raw_array)),
                'raw_max': int(np.max(raw_array)),
                'mul16_rate': 0.0,
                'unique_ratio': 1.0,
                'unique_count': 0,
            }
        
        # Compute statistics on flattened array (all channels combined)
        # This is consistent regardless of mono/RGB and simplifies analysis
        flat = raw_array.flatten().astype(np.uint16)
        
        raw_min = int(np.min(flat))
        raw_max = int(np.max(flat))
        raw_median = int(np.median(flat))
        raw_p99 = int(np.percentile(flat, 99))
        
        # Sample for expensive computations
        sample_size = min(100000, flat.size)
        if flat.size > sample_size:
            sample = flat[np.random.choice(flat.size, sample_size, replace=False)]
        else:
            sample = flat
        
        # Detect left-shifted 12-bit data (values are multiples of 16 = 2^4)
        mul16_rate = float(np.mean(sample % 16 == 0))
        
        # Unique value analysis - helps detect quantization/bit depth
        # For true 8-bit: ~256 unique values
        # For true 12-bit: ~4096 unique values  
        # For left-shifted: fewer unique values than expected for bit depth
        unique_vals = np.unique(sample)
        unique_count = len(unique_vals)
        unique_ratio = unique_count / len(sample)
        
        # Build base details dict
        details = {
            'raw_min': raw_min,
            'raw_max': raw_max,
            'raw_median': raw_median,
            'raw_p99': raw_p99,
            'mul16_rate': round(mul16_rate, 4),
            'unique_count': unique_count,
            'unique_ratio': round(unique_ratio, 6),
            'sample_size': len(sample),
        }
        
        # === Inference rules (in priority order) ===
        
        # Rule 1: 8-bit payload in 16-bit container
        # Example: raw_max=254 -> clearly 8-bit data
        if raw_max <= 255:
            details['suggested_downshift_bits'] = 0
            return 255.0, f"8-bit payload detected (max={raw_max}), even if container is RAW16", details
        
        # Rule 2: 12-bit range (max <= 4095)
        if raw_max <= 4095:
            details['suggested_downshift_bits'] = 0
            return 4095.0, f"12-bit range detected (max={raw_max})", details
        
        # Rule 3: Left-shifted 12-bit data
        # If >= 90% of values are multiples of 16, likely 12-bit << 4
        if mul16_rate >= 0.90:
            details['suggested_downshift_bits'] = 4
            return 65535.0, f"12-bit left-shifted to 16-bit (mul16_rate={mul16_rate:.2f}, max={raw_max})", details
        
        # Rule 4: Default to 16-bit
        details['suggested_downshift_bits'] = 0
        return 65535.0, f"16-bit range detected (max={raw_max})", details
    
    def _compute_luminance(self, norm_array):
        """
        Compute luminance from normalized RGB array.
        Uses standard Rec.601 coefficients.
        
        Args:
            norm_array: Normalized array (0-1 float), shape (H,W) or (H,W,3)
            
        Returns:
            Luminance array (H,W) in 0-1 range
        """
        if norm_array.ndim == 2:
            # Already grayscale/mono
            return norm_array
        elif norm_array.ndim == 3 and norm_array.shape[2] == 3:
            # RGB - compute luminance
            return 0.299 * norm_array[:,:,0] + 0.587 * norm_array[:,:,1] + 0.114 * norm_array[:,:,2]
        else:
            # Unknown shape, return as-is (flattened if needed)
            app_logger.warning(f"DEV MODE: Unexpected array shape {norm_array.shape} for luminance")
            return norm_array.mean(axis=-1) if norm_array.ndim > 2 else norm_array
    
    def _save_raw_fits(self, path, raw_array, image_bit_depth, header_kv):
        """
        Save raw image data as FITS, preserving true dynamic range.
        
        For RAW16: saves uint16 data as-is (no fake scaling)
        For RAW8: scales to uint16 for FITS compatibility, clearly labeled
        """
        try:
            from astropy.io import fits
            
            # Prepare data based on bit depth
            if image_bit_depth == 16:
                # RAW16: save as-is, no scaling
                data = raw_array.astype(np.uint16)
                header_kv['SCALED'] = (False, 'Data saved without scaling')
                header_kv['COMMENT'] = 'RAW16 data - true sensor values preserved'
            else:
                # RAW8: scale to uint16 for FITS compatibility
                data = (raw_array.astype(np.uint16) * 257)  # 0-255 -> 0-65535
                header_kv['SCALED'] = (True, 'RAW8 scaled to 16-bit (x257)')
                header_kv['COMMENT'] = 'RAW8 data scaled to 16-bit for FITS compatibility'
            
            # Handle RGB vs mono
            if data.ndim == 3 and data.shape[2] == 3:
                # RGB: transpose to FITS convention (C, H, W)
                data = np.transpose(data, (2, 0, 1))
                header_kv['COLORTYP'] = ('RGB', 'Color type of image')
            elif data.ndim == 2:
                header_kv['COLORTYP'] = ('MONO', 'Grayscale/mono image')
            
            self._write_fits(path, data, header_kv)
            app_logger.info(f"DEV MODE: ✓ Saved raw FITS to {os.path.basename(path)} (shape: {data.shape}, scaled={image_bit_depth != 16})")
            
        except ImportError:
            # Fallback to TIFF
            tiff_path = path.replace('.fits', '.tiff')
            Image.fromarray(raw_array).save(tiff_path, 'TIFF', compression=None)
            app_logger.info(f"DEV MODE: ✓ Saved raw TIFF to {os.path.basename(tiff_path)} (astropy not installed)")
    
    def _write_fits(self, path, data, header_kv):
        """
        Write data to FITS file with header keywords.
        
        Args:
            path: Output file path
            data: numpy array to save
            header_kv: dict of header keyword-value pairs
                       Values can be simple values or (value, comment) tuples
        """
        from astropy.io import fits
        
        hdu = fits.PrimaryHDU(data)
        
        for key, val in header_kv.items():
            if isinstance(val, tuple) and len(val) == 2:
                hdu.header[key] = val  # (value, comment)
            else:
                hdu.header[key] = val
        
        hdu.writeto(path, overwrite=True)
    
    def _write_json(self, path, payload):
        """Write dictionary to JSON file with pretty formatting"""
        with open(path, 'w') as f:
            json.dump(payload, f, indent=2, default=str)
    
    def _log_channel_statistics(self, norm_array, raw_array):
        """Log detailed per-channel statistics"""
        channel_names = ['R', 'G', 'B'] if norm_array.ndim == 3 and norm_array.shape[2] == 3 else ['Y']
        
        for c, channel_name in enumerate(channel_names):
            if norm_array.ndim == 3:
                channel = norm_array[:, :, c]
                raw_channel = raw_array[:, :, c]
            else:
                channel = norm_array
                raw_channel = raw_array
            
            median = np.median(channel)
            mean = np.mean(channel)
            std = np.std(channel)
            min_val = np.min(channel)
            max_val = np.max(channel)
            p1 = np.percentile(channel, 1)
            p99 = np.percentile(channel, 99)
            mad = np.median(np.abs(channel - median))
            
            # Raw values (integer)
            raw_min = int(np.min(raw_channel))
            raw_max = int(np.max(raw_channel))
            
            app_logger.info(
                f"DEV MODE {channel_name}: median={median:.4f}, mean={mean:.4f}, "
                f"std={std:.4f}, MAD={mad:.4f}, min={min_val:.4f}, max={max_val:.4f}, "
                f"p1={p1:.4f}, p99={p99:.4f}, raw_range=[{raw_min}-{raw_max}]"
            )
            
            if norm_array.ndim == 2:
                break  # Only one channel for mono
        
        # Log luminance stats for RGB
        if norm_array.ndim == 3 and norm_array.shape[2] == 3:
            lum = self._compute_luminance(norm_array)
            app_logger.info(
                f"DEV MODE Luminance: median={np.median(lum):.4f}, mean={np.mean(lum):.4f}, "
                f"MAD={np.median(np.abs(lum - np.median(lum))):.4f}"
            )
            
            # Color balance info
            r_mean = np.mean(norm_array[:,:,0])
            g_mean = np.mean(norm_array[:,:,1])
            b_mean = np.mean(norm_array[:,:,2])
            app_logger.info(
                f"DEV MODE Color Balance: R/G={r_mean/g_mean:.3f}, B/G={b_mean/g_mean:.3f} "
                f"(1.0 = neutral)"
            )
    
    def _compute_stretch_calibration(self, lum, norm_array, metadata, denom, denom_reason, denom_details):
        """
        Compute stretch calibration parameters from luminance.
        
        These parameters can be used to reproduce the same stretch on future frames
        or to tune the stretch algorithm.
        
        Args:
            lum: Luminance array (H,W) in 0-1 range
            norm_array: Normalized RGB array (H,W,3) in 0-1 range
            metadata: Image metadata dict
            denom: Normalization denominator used
            denom_reason: Explanation of why denom was chosen
            denom_details: dict with raw_min, raw_max, mul16_rate, unique_ratio, etc.
        
        Example output JSON:
        {
            "timestamp": "2026-01-04T22:35:12",
            "camera": "ZWO ASI676MC",
            "exposure": "0.1s",
            "gain": "100",
            "image_bit_depth": 16,
            "normalization": {
                "denom": 255,
                "reason": "8-bit payload detected (max=254), even if container is RAW16",
                "raw_max": 254,
                "mul16_rate": 0.0039,
                "unique_ratio": 0.0021
            },
            "stretch": {
                "black_point": 0.0234,
                "white_point": 0.1875,
                "median_lum": 0.0312,
                "mean_lum": 0.0298,
                "dynamic_range": 0.1641,
                "is_dark_scene": true,
                "recommended_asinh_strength": 150
            },
            "color_balance": {"r_g": 0.823, "b_g": 1.456}
        }
        """
        # Compute stretch parameters from luminance
        black_point = float(np.percentile(lum, 2))
        white_point = float(np.percentile(lum, 99.7))  # Using 99.7 to avoid hot pixels
        median_lum = float(np.median(lum))
        mean_lum = float(np.mean(lum))
        dynamic_range = white_point - black_point
        
        # Dark scene detection (same threshold as processor.py)
        is_dark_scene = median_lum < 0.05
        
        # Recommended asinh strength based on median luminance
        # Lower median -> higher stretch strength needed
        # Formula: strength = base / median, clamped to reasonable range
        if median_lum > 0.001:
            asinh_strength = min(500, max(50, 5.0 / median_lum))
        else:
            asinh_strength = 500  # Very dark scene
        
        # Color balance (for RGB)
        color_balance = {}
        if norm_array.ndim == 3 and norm_array.shape[2] == 3:
            r_mean = float(np.mean(norm_array[:,:,0]))
            g_mean = float(np.mean(norm_array[:,:,1]))
            b_mean = float(np.mean(norm_array[:,:,2]))
            if g_mean > 0:
                color_balance = {
                    'r_g': round(r_mean / g_mean, 3),
                    'b_g': round(b_mean / g_mean, 3)
                }
        
        calibration = {
            'timestamp': datetime.now().isoformat(),
            'camera': metadata.get('CAMERA', 'Unknown'),
            'exposure': metadata.get('EXPOSURE', 'N/A'),
            'gain': metadata.get('GAIN', 'N/A'),
            'image_bit_depth': metadata.get('IMAGE_BIT_DEPTH', 8),
            'camera_bit_depth': metadata.get('CAMERA_BIT_DEPTH', 8),
            'bayer_pattern': metadata.get('BAYER_PATTERN', 'BGGR'),
            'normalization': {
                'denom': int(denom),
                'reason': denom_reason,
                'raw_max': denom_details.get('raw_max', 0),
                'raw_min': denom_details.get('raw_min', 0),
                'mul16_rate': denom_details.get('mul16_rate', 0.0),
                'unique_ratio': denom_details.get('unique_ratio', 0.0),
                'unique_count': denom_details.get('unique_count', 0),
                'suggested_downshift_bits': denom_details.get('suggested_downshift_bits', 0),
            },
            'stretch': {
                'black_point': round(black_point, 6),
                'white_point': round(white_point, 6),
                'median_lum': round(median_lum, 6),
                'mean_lum': round(mean_lum, 6),
                'dynamic_range': round(dynamic_range, 6),
                'is_dark_scene': is_dark_scene,
                'recommended_asinh_strength': round(asinh_strength, 1)
            },
            'color_balance': color_balance
        }
        
        return calibration


class ImageProcessor(QObject):
    """
    Image processor for Qt UI
    
    Uses a background thread for heavy image processing to keep UI responsive.
    Reuses functions from services/processor.py for actual processing.
    """
    
    # Signals forwarded from worker
    processing_complete = Signal(object, dict, str)  # PIL Image, metadata, output_path
    preview_ready = Signal(object, dict)  # PIL Image for preview, histogram data
    error_occurred = Signal(str)
    
    def __init__(self, parent=None):
        super().__init__(parent)
        
        # Worker thread
        self._worker = ImageProcessorWorker()
        self._worker.processing_complete.connect(self._on_processing_complete)
        self._worker.preview_ready.connect(self._on_preview_ready)
        self._worker.error_occurred.connect(self._on_error)
        
        # Reference to main window for config access
        self._main_window = None
        
    def set_main_window(self, main_window):
        """Set reference to main window for config access"""
        self._main_window = main_window
        
        # Pass main window to worker for camera access
        self._worker.set_main_window(main_window)
        
        # Pass weather service to worker
        if hasattr(main_window, 'weather_service'):
            self._worker.set_weather_service(main_window.weather_service)
    
    def start(self):
        """Start the processing worker"""
        if not self._worker.isRunning():
            self._worker.start()
            app_logger.debug("Image processor started")
    
    def stop(self):
        """Stop the processing worker"""
        self._worker.stop()
        self._worker.wait(5000)  # Wait up to 5 seconds
        app_logger.debug("Image processor stopped")
    
    def process_and_save(self, img: Image.Image, metadata: dict):
        """
        Process image and save to disk
        
        Gathers config from UI, then queues processing to background thread.
        
        Args:
            img: PIL Image to process
            metadata: Image metadata dict
        """
        if not self._main_window:
            app_logger.error("ImageProcessor: main_window not set")
            return
        
        try:
            # Gather config from UI
            config = self._gather_config()
            
            if not config.get('output_dir'):
                app_logger.error("Output directory not configured")
                return
            
            # Create task and queue it
            task = ImageProcessingTask(img, metadata, config)
            self._worker.queue_task(task)
            
        except Exception as e:
            app_logger.error(f"Failed to queue image processing: {e}")
            app_logger.error(traceback.format_exc())
    
    def _gather_config(self) -> dict:
        """Gather processing config from UI components"""
        mw = self._main_window
        
        # Get auto_stretch config - merge with defaults if missing keys
        auto_stretch_defaults = {
            'enabled': False,
            'target_median': 0.25,
            'linked_stretch': True,
            'preserve_blacks': True,
            'black_point': 0.0,
            'shadow_aggressiveness': 2.8,
            'saturation_boost': 1.5
        }
        auto_stretch_config = mw.config.get('auto_stretch', {})
        # Merge with defaults - saved config overrides defaults
        auto_stretch = auto_stretch_defaults.copy()
        auto_stretch.update(auto_stretch_config)
        
        app_logger.debug(f"Auto-stretch config: enabled={auto_stretch.get('enabled')}, target_median={auto_stretch.get('target_median')}")
        
        config = {
            'output_dir': mw.config.get('output_directory', ''),
            'output_format': mw.config.get('output_format', 'PNG'),
            'jpg_quality': mw.config.get('jpg_quality', 85),
            'resize_percent': mw.config.get('resize_percent', 100),
            'auto_brightness': mw.config.get('auto_brightness', False),
            'brightness_factor': mw.config.get('brightness_factor', 1.0),
            'saturation_factor': mw.config.get('saturation_factor', 1.0),
            'timestamp_corner': mw.config.get('timestamp_corner', False),
            'filename_pattern': mw.config.get('filename_pattern', '{filename}'),
            'auto_stretch': auto_stretch,
            'overlays': mw.config.get('overlays', []),
            'dev_mode': mw.config.get('dev_mode', {'enabled': False, 'raw_folder': 'raw_debug', 'save_histogram_stats': True}),
        }
        
        return config
    
    def _on_processing_complete(self, img, metadata, output_path):
        """Forward processing complete signal"""
        self.processing_complete.emit(img, metadata, output_path)
    
    def _on_preview_ready(self, img, hist_data):
        """Forward preview ready signal"""
        self.preview_ready.emit(img, hist_data)
    
    def _on_error(self, error_msg):
        """Forward error signal"""
        self.error_occurred.emit(error_msg)
