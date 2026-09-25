"""Image processing and metadata parsing."""
import os
import tempfile
from datetime import datetime
from PIL import Image
import numpy as np
from .logger import app_logger
from .image_stretch import auto_stretch_image, mtf_stretch, _stretch_channel, _calculate_mtf_midtone  # noqa: F401
from .output_crop import METADATA_KEY as CROP_METADATA_KEY, apply_output_crop
from .overlay_renderer import add_overlays  # noqa: F401
from .sky_evidence import compute_sky_evidence


def save_image_atomic(img, output_path: str, format_name: str, **save_kwargs) -> None:
    """
    Save image atomically to prevent corruption on crash/power loss.
    REL-001 fix: Uses temp file + rename pattern for atomic writes.

    Args:
        img: PIL Image to save
        output_path: Final destination path
        format_name: Image format (JPEG, PNG, etc.)
        **save_kwargs: Additional arguments for PIL save()
    """
    output_dir = os.path.dirname(output_path)

    if output_dir:
        os.makedirs(output_dir, exist_ok=True)

    fd, temp_path = tempfile.mkstemp(
        suffix='.tmp',
        dir=output_dir if output_dir else '.',
        prefix='.saving_'
    )

    try:
        os.close(fd)
        img.save(temp_path, format_name, **save_kwargs)
        os.replace(temp_path, output_path)

    except Exception:
        try:
            if os.path.exists(temp_path):
                os.remove(temp_path)
        except OSError:
            pass
        raise


def parse_sidecar_file(sidecar_path):
    """
    Parse the sidecar text file into a dictionary.
    Format:
    [ZWO ASI676MC]
    Key = Value
    """
    metadata = {}
    camera_name = None

    if not os.path.exists(sidecar_path):
        return metadata

    try:
        with open(sidecar_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()

                if line.startswith('[') and line.endswith(']'):
                    camera_name = line[1:-1]
                    metadata['CAMERA'] = camera_name
                    continue

                if '=' in line:
                    key, value = line.split('=', 1)
                    key = key.strip()
                    value = value.strip()
                    metadata[key.upper()] = value

    except Exception as e:
        app_logger.error(f"Error parsing sidecar file {sidecar_path}: {e}")

    return metadata


def derive_metadata(metadata, image_filename, session_name):
    """Derive additional metadata values from parsed data."""
    derived = metadata.copy()

    if 'CAPTURE AREA SIZE' in metadata:
        area = metadata['CAPTURE AREA SIZE']
        area = area.replace('*', 'x').replace(' ', '')
        derived['RES'] = area

    derived['FILENAME'] = image_filename
    derived['SESSION'] = session_name
    derived['DATETIME'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    for key in ['EXPOSURE', 'GAIN', 'TEMPERATURE']:
        if key not in derived and key.title() in metadata:
            derived[key] = metadata[key.title()]

    if 'TEMP' not in derived and 'TEMPERATURE' in derived:
        derived['TEMP'] = derived['TEMPERATURE']

    return derived


def build_output_filename(pattern, metadata, output_format='PNG'):
    """
    Build output filename from pattern and metadata.
    Supports tokens: {filename}, {session}, {timestamp}
    """
    result = pattern

    if '{filename}' in result:
        filename_no_ext = os.path.splitext(metadata.get('FILENAME', 'image'))[0]
        result = result.replace('{filename}', filename_no_ext)

    if '{session}' in result:
        result = result.replace('{session}', metadata.get('SESSION', 'unknown'))

    if '{timestamp}' in result:
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        result = result.replace('{timestamp}', timestamp)

    ext_map = {
        'PNG': '.png',
        'JPG': '.jpg',
        'JPEG': '.jpg',
        'BMP': '.bmp',
        'TIFF': '.tiff'
    }
    extension = ext_map.get(output_format.upper(), '.jpg')

    if not any(result.lower().endswith(ext) for ext in ['.png', '.jpg', '.jpeg', '.bmp', '.tiff']):
        result += extension

    return result


def process_image(image_path, config, metadata_dict=None, weather_service=None, extras=None):
    """
    Main processing function:
    1. Parse sidecar file OR use provided metadata
    2. Add overlays
    3. Save to output directory

    Args:
        image_path: Path to image file OR PIL Image object
        config: Config object
        metadata_dict: Optional pre-built metadata dictionary (for camera capture)
        extras: Optional dict the caller owns; filled with 'metadata', 'native_size'
            (frame size before resize) and 'clean_frame' (the pre-resize, pre-crop,
            pre-overlay image) so watch mode caches a full frame for Calibrate Now
            and reprocess (issue #12)

    Returns: (success: bool, output_path: str, error: str)
    """
    try:
        output_dir = config.get('output_directory', '')
        output_pattern = config.get('output_pattern', '{session}_{filename}')
        overlays = config.get_overlays()
        resize_percent = config.get('resize_percent', 100)
        show_timestamp = config.get('show_timestamp_corner', False)
        timestamp_corner = config.get('timestamp_corner', 'Top-Right')

        if not output_dir:
            return False, None, "Output directory not configured"

        os.makedirs(output_dir, exist_ok=True)

        if metadata_dict:
            metadata = metadata_dict
            image_filename = metadata.get('FILENAME', 'image.png')
            parent_folder = metadata.get('SESSION', 'session')
        else:
            image_filename = os.path.basename(image_path)
            parent_folder = os.path.basename(os.path.dirname(image_path))

            sidecar_path = image_path + '.txt'

            metadata = parse_sidecar_file(sidecar_path)
            metadata = derive_metadata(metadata, image_filename, parent_folder)

        overlays_to_apply = overlays.copy()
        if show_timestamp:
            timestamp_overlay = {
                'text': '{DATETIME}',
                'anchor': timestamp_corner,
                'x_offset': 10,
                'y_offset': 10,
                'font_size': 24,
                'color': 'white',
                'background': True
            }
            overlays_to_apply.append(timestamp_overlay)

        ml_config = config.get('ml_models', {})
        if ml_config.get('enabled', False):
            try:
                from services.ml_service import get_ml_service, analyze_image_for_tokens
                ml_service = get_ml_service()
                if not ml_service.is_available():
                    ml_service.initialize()
                if ml_service.is_available():
                    load_src = image_path if isinstance(image_path, str) else image_path
                    ml_img = Image.open(load_src) if isinstance(load_src, str) else load_src
                    ml_tokens = analyze_image_for_tokens(np.array(ml_img.convert('RGB')), config=ml_config)
                    metadata.update(ml_tokens)
                    app_logger.debug(f"ML tokens: {ml_tokens}")
            except Exception as e:
                app_logger.debug(f"ML prediction skipped: {e}")

        raw_img = Image.open(image_path) if isinstance(image_path, str) else image_path

        auto_stretch_config = config.get('auto_stretch', {})
        if auto_stretch_config.get('enabled', False):
            if raw_img.mode not in ('RGB', 'RGBA', 'L'):
                raw_img = raw_img.convert('RGB')
            raw_img = auto_stretch_image(raw_img, auto_stretch_config)

        # Star evidence for the observable-sky gate (issue #93), on the
        # stretched frame before the gate is first consulted by star detection
        # below. Here that frame is pre-resize; camera mode measures its
        # post-resize stretched copy — the evidence resamples either to one
        # fixed plane, so the count means the same thing in both.
        compute_sky_evidence(raw_img, metadata, config)

        try:
            from .star_detection import analyze_stars, should_run_star_detection
            if should_run_star_detection(config, metadata):
                src_img = Image.open(image_path) if isinstance(image_path, str) else image_path
                star_tokens = analyze_stars(np.array(src_img.convert('RGB')))
                metadata.update(star_tokens)
            else:
                metadata.update({'STAR_COUNT': 'N/A', 'FWHM': 'N/A', 'SEEING': 'N/A'})
        except Exception as e:
            app_logger.debug(f"Star detection skipped: {e}")

        # The pre-resize, pre-crop frame is what a watch-mode reprocess and
        # Calibrate Now must start from — resizing or cropping it twice would
        # compound (issue #12). A copy, not an alias: with resize off and no
        # crop, add_overlays draws onto this very object (RGBA skips convert).
        clean_frame = raw_img.copy() if isinstance(extras, dict) else None
        native_size = raw_img.size
        if resize_percent > 0 and resize_percent != 100:
            new_width = int(raw_img.width * resize_percent / 100)
            new_height = int(raw_img.height * resize_percent / 100)
            raw_img = raw_img.resize((new_width, new_height), Image.Resampling.LANCZOS)

        # Output framing (issue #12) — after every analysis stage above, so only
        # the rendered output is cut down; overlays anchor on the cropped edges.
        raw_img, crop_box = apply_output_crop(raw_img, config.get('output_crop', {}))
        if isinstance(extras, dict):
            extras.update(metadata=metadata, native_size=native_size, clean_frame=clean_frame)
        if crop_box is not None:
            metadata[CROP_METADATA_KEY] = crop_box.as_metadata()
        else:
            metadata.pop(CROP_METADATA_KEY, None)

        processed_img = add_overlays(raw_img, overlays_to_apply, metadata, weather_service=weather_service)

        if config.get('auto_brightness', False):
            from PIL import ImageEnhance

            img_array = np.array(processed_img.convert('L'))
            mean_brightness = np.mean(img_array)

            target_brightness = 128
            auto_factor = target_brightness / max(mean_brightness, 10)
            auto_factor = max(0.5, min(auto_factor, 4.0))

            manual_factor = config.get('brightness_factor', 1.0)
            final_factor = auto_factor * manual_factor

            enhancer = ImageEnhance.Brightness(processed_img)
            processed_img = enhancer.enhance(final_factor)

            app_logger.debug(f"Auto brightness: mean={mean_brightness:.1f}, auto_factor={auto_factor:.2f}, manual={manual_factor:.2f}, final={final_factor:.2f}")

        saturation_factor = config.get('saturation_factor', 1.0)
        if saturation_factor != 1.0:
            from PIL import ImageEnhance
            enhancer = ImageEnhance.Color(processed_img)
            processed_img = enhancer.enhance(saturation_factor)
            app_logger.debug(f"Saturation adjusted: factor={saturation_factor:.2f}")

        output_format = config.get('output_format', 'JPG')
        output_filename = build_output_filename(output_pattern, metadata, output_format)
        output_path = os.path.join(output_dir, output_filename)

        if output_format.upper() in ['JPG', 'JPEG']:
            if processed_img.mode == 'RGBA':
                rgb_img = Image.new('RGB', processed_img.size, (0, 0, 0))
                rgb_img.paste(processed_img, mask=processed_img.split()[3])
                processed_img = rgb_img
            elif processed_img.mode != 'RGB':
                processed_img = processed_img.convert('RGB')

            jpg_quality = config.get('jpg_quality', 85)
            save_image_atomic(processed_img, output_path, 'JPEG', quality=jpg_quality, optimize=True)
        else:
            save_image_atomic(processed_img, output_path, output_format.upper())

        if crop_box is not None:
            # Watch mode only gets the image back (FileWatcher's callback is
            # (path, image)), so the all-sky preview reads the crop from here.
            processed_img.info[CROP_METADATA_KEY] = crop_box.as_metadata()
        return True, output_path, None, processed_img

    except Exception as e:
        return False, None, str(e), None
