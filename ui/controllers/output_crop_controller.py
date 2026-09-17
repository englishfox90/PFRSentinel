"""
Output framing controller — feeds the crop-box editor and runs "Fit to sky".

Owns the two pieces of work the Processing page's Output framing card must
not do itself: shrinking the latest full-frame image to a thumbnail the
editor can paint, and measuring the fisheye sky circle for the Fit-to-sky
button. Both run on daemon threads; results reach the card through signals.
"""
import threading

from PySide6.QtCore import QObject, Signal

from services.logger import app_logger
from services.output_crop import square_around_circle

# Long side of the editor thumbnail. Small enough that Image.reduce() on a
# 3552^2 frame is a few tens of ms and the sky-circle scan is instant.
THUMBNAIL_MAX_PX = 720


class OutputCropController(QObject):
    # (PIL thumbnail, reference frame width, reference frame height)
    thumbnail_ready = Signal(object, int, int)
    # (x, y, width, height) in reference-frame pixels
    fit_ready = Signal(int, int, int, int)
    fit_failed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._active = False
        self._thumbnail = None
        self._ref_size = (0, 0)
        self._job_running = False
        self._lock = threading.Lock()

    def bind(self, image_processor, crop_card):
        """Wire the processor's clean-frame signal and the card's requests."""
        image_processor.preview_ready.connect(self.on_preview_ready)
        self.thumbnail_ready.connect(crop_card.set_frame)
        self.fit_ready.connect(crop_card.apply_box)
        self.fit_failed.connect(crop_card.show_fit_error)
        crop_card.fit_requested.connect(self.fit_to_sky)
        crop_card.activity_changed.connect(self.set_active)

    # ---- inputs ---------------------------------------------------------

    def set_active(self, active: bool):
        """The card tells us when it is visible; thumbnails only refresh then."""
        self._active = bool(active)

    def on_preview_ready(self, pil_image, hist_data=None):
        """Slot for ImageProcessor.preview_ready — the clean full-frame image.

        ``hist_data['native_size']`` is the frame size before resize_percent;
        the box is kept in those pixels (the sensor's), so the issue's
        "2880 at (80, 320)" means the same thing at any output scale.
        The full-res frame is only referenced for the duration of the reduce
        job; nothing here keeps a 38 MB frame alive between captures.
        """
        if pil_image is None:
            return
        native = hist_data.get('native_size') if isinstance(hist_data, dict) else None
        try:
            native = (int(native[0]), int(native[1])) if native else None
        except (TypeError, ValueError, IndexError):
            native = None
        # Always build the first thumbnail so the editor has something to show
        # when the page is first opened; after that, only while it is visible.
        if self._thumbnail is not None and not self._active:
            return
        with self._lock:
            if self._job_running:
                return
            self._job_running = True
        threading.Thread(
            target=self._build_thumbnail, args=(pil_image, native), daemon=True,
            name="output-crop-thumbnail",
        ).start()

    def _build_thumbnail(self, pil_image, native=None):
        try:
            width, height = native if native and native[0] > 0 and native[1] > 0 else pil_image.size
            factor = max(1, -(-max(width, height) // THUMBNAIL_MAX_PX))  # ceil: honour the cap
            if pil_image.mode != 'RGB':
                pil_image = pil_image.convert('RGB')
            thumb = pil_image.reduce(factor) if factor > 1 else pil_image.copy()
            self._thumbnail = thumb
            self._ref_size = (width, height)
            self.thumbnail_ready.emit(thumb, width, height)
        except Exception as e:
            app_logger.debug(f"Output framing thumbnail skipped: {e}")
        finally:
            with self._lock:
                self._job_running = False

    # ---- fit to sky -----------------------------------------------------

    def fit_to_sky(self):
        """Measure the sky disc on the current thumbnail and emit a square crop."""
        thumb = self._thumbnail
        ref_w, ref_h = self._ref_size
        if thumb is None or ref_w <= 0 or ref_h <= 0:
            self.fit_failed.emit("No frame yet — start capture, then try again.")
            return
        threading.Thread(
            target=self._measure_sky, args=(thumb, ref_w, ref_h), daemon=True,
            name="output-crop-fit-sky",
        ).start()

    def _measure_sky(self, thumb, ref_w: int, ref_h: int):
        try:
            from services.allsky.star_centroid import measure_sky_circle
            # trim_fraction=0 keeps the full disc; the default 15% trim is for
            # calibration, where the horizon is noise, not for framing.
            result = measure_sky_circle(thumb, trim_fraction=0.0)
        except Exception as e:
            app_logger.debug(f"Fit to sky failed: {e}")
            result = None
        if result is None:
            self.fit_failed.emit(
                "Could not find the sky circle in the last frame. "
                "Try again on a frame with visible sky, or drag the box by hand."
            )
            return
        cx, cy, radius = result
        scale = ref_w / thumb.width
        x, y, w, h = square_around_circle(cx * scale, cy * scale, radius * scale, ref_w, ref_h)
        app_logger.info(f"Fit to sky: circle r={radius * scale:.0f}px → crop {w}x{h} at ({x}, {y})")
        self.fit_ready.emit(x, y, w, h)
