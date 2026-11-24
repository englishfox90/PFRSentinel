"""
Status Strip Component

Persistent observatory-telemetry band shown directly below the app bar. The
left group holds at-a-glance tiles (Web, Discord, Roof, Sky, Weather, Seeing,
Imaging); the right edge holds a single state pill that reports capture / camera state.

Layout + display formatting only. All values are pushed in from the main
window's 1 Hz status timer and per-frame handlers — this component never
fetches anything itself.
"""
from PySide6.QtWidgets import QFrame, QHBoxLayout, QVBoxLayout, QLabel, QWidget
from PySide6.QtCore import Qt, Signal

from services.observing_window import REASON_KEY as GATE_REASON_KEY

from ..theme.tokens import Colors, Typography
from ..theme.icons import qicon
from ..theme.styles import paint_border_lines
from .status_pill import ICON_SIZE as _ICON_SIZE, StatePill  # StatePill re-exported
from .status_strip_fit import (
    COMPACT, ELIDED, FULL, FitItem, chrome_width, elide_label_text, label_elide_steps,
    label_text_width, plan_fit,
)

# Sky tile text per observable-sky gate reason. 'roof' and 'static' are
# shown from the ML results above them; 'twilight' is daylight, not news.
_GATE_REASON_TEXT = {
    'no_stars': "No stars detected",
    'exposure': "Exposure too short",
}


# tone -> colour. 'stale' is a dimmed value kept in place when data goes cold.
_TONE_COLORS = {
    'ok': Colors.success_text,
    'info': Colors.info_text,
    'warn': Colors.warning_text,
    'error': Colors.error_text,
    'muted': Colors.text_muted,
    'primary': Colors.text_primary,
    'stale': Colors.gray_9,
}

NOT_CONFIGURED = "Not configured"

_IMAGING_TOOLTIP = "Show imaging conditions on Live Monitoring"
_STATIC_TOOLTIP = (
    "This frame is almost entirely sensor noise, so there is too little in it "
    "to judge the roof or the sky. The readings shown may be wrong."
)


class StatusTile(QFrame):
    """Icon + uppercase eyebrow label + value, with a trailing separator.
    ``set_clickable`` makes it a link: pointing cursor, hover shade, ``clicked``.

    On a narrow strip the tile can fold to its icon (``COMPACT``) or elide its
    value at a word boundary (``ELIDED``); either way the tooltip carries what
    is no longer on screen."""

    clicked = Signal()
    content_changed = Signal()   # the full text or its style changed

    def __init__(self, icon_name: str, label: str, parent=None):
        super().__init__(parent)
        self._clickable = False
        self._hover = False
        self._icon_name = icon_name
        self._text = "—"
        self._tone = 'muted'
        self._mono = False
        self._configured = False  # muted/not-configured tiles ignore stale toggling
        self._stale = False
        self._render_key = None   # skips redundant re-render on the 1 Hz refresh
        self._mode = FULL
        self._elide_w = None
        self._base_tip = ""
        self._build(label)

    def _build(self, label: str):
        lay = QHBoxLayout(self)
        lay.setContentsMargins(16, 0, 16, 0)
        lay.setSpacing(9)

        self.icon_label = QLabel()
        self.icon_label.setFixedSize(_ICON_SIZE)
        lay.addWidget(self.icon_label, 0, Qt.AlignVCenter)

        # A widget, not a bare layout, so the compact form can hide it and
        # take the spacing with it.
        self._text_box = QWidget()
        # The app stylesheet fills every QWidget with the app background, which
        # would paint over this tile's bottom border and hover shade.
        self._text_box.setStyleSheet("background: transparent;")
        col = QVBoxLayout(self._text_box)
        col.setSpacing(1)
        col.setContentsMargins(0, 0, 0, 0)
        self.eyebrow = QLabel(label)
        self.eyebrow.setStyleSheet(
            f"color: {Colors.text_muted}; font-size: 10px; font-weight: 600; "
            f"letter-spacing: 0.07em; border: none;"
        )
        self.value = QLabel(self._text)
        col.addStretch(1)
        col.addWidget(self.eyebrow)
        col.addWidget(self.value)
        col.addStretch(1)
        lay.addWidget(self._text_box)

        self._apply()

    def _apply(self):
        # The 1 Hz status timer pushes the same values repeatedly; only re-render
        # (and re-rasterize the icon) when something actually changed.
        key = (self._text, self._tone, self._icon_name, self._mono,
               self._stale, self._configured)
        if key == self._render_key:
            return
        self._render_key = key
        self._render()

    def _render(self):
        # A stale, configured tile dims to grey but keeps its real tone stored.
        tone = 'stale' if (self._stale and self._configured) else self._tone
        color = _TONE_COLORS.get(tone, Colors.text_primary)
        family = Typography.family_mono if self._mono else Typography.family_text
        self.value.setStyleSheet(
            f"color: {color}; font-size: 13px; font-weight: 700; "
            f"font-family: {family}; border: none;"
        )
        self.icon_label.setPixmap(qicon(self._icon_name, color).pixmap(_ICON_SIZE))
        self._present()
        self.content_changed.emit()

    # --- Narrow-strip presentation ----------------------------------------
    @property
    def full_text(self) -> str:
        return self._text

    @property
    def presentation(self) -> str:
        return self._mode

    def is_not_configured(self) -> bool:
        return self._text == NOT_CONFIGURED

    def set_presentation(self, mode: str, value_width: int = None):
        if (mode, value_width) == (self._mode, self._elide_w):
            return
        self._mode, self._elide_w = mode, value_width
        self._present()

    def fit_item(self, key, collapse_rank=None, elide_rank=None) -> FitItem:
        lay = self.layout()
        m = lay.contentsMargins()
        return FitItem(
            key=key,
            chrome_w=chrome_width(lay, _ICON_SIZE.width()),
            label_w=self.eyebrow.sizeHint().width(),
            value_w=label_text_width(self.value, self._text),
            steps=label_elide_steps(self.value, self._text),
            compact_w=m.left() + m.right() + _ICON_SIZE.width(),
            collapse_rank=collapse_rank,
            elide_rank=elide_rank,
        )

    def _present(self):
        self._text_box.setVisible(self._mode != COMPACT)
        shown = self._text
        if self._mode == ELIDED and self._elide_w is not None:
            shown = elide_label_text(self.value, self._text, self._elide_w)
        if self.value.text() != shown:
            self.value.setText(shown)
        self._compose_tooltip(shown)

    def _compose_tooltip(self, shown: str):
        lead = ""
        if self._mode == COMPACT:
            lead = f"{self.eyebrow.text().capitalize()}: {self._text}"
        elif shown != self._text:
            lead = self._text
        super().setToolTip("\n".join(p for p in (lead, self._base_tip) if p))

    def setToolTip(self, text: str):
        """The tile's own tooltip; the folded or elided value is put above it."""
        self._base_tip = text or ""
        self._compose_tooltip(self.value.text())

    def set_value(self, text: str, tone: str = 'primary', icon_name: str = None, mono: bool = False):
        if icon_name:
            self._icon_name = icon_name
        self._text = text
        self._tone = tone
        self._mono = mono
        self._configured = tone not in ('muted', 'stale')
        self._apply()

    def set_stale(self, stale: bool):
        """Dim a configured tile when its data is no longer live (kept in place)."""
        if not self._configured or stale == self._stale:
            return
        self._stale = stale
        self._apply()

    def refresh_styles(self):
        self._render_key = None  # force a re-render after an accent change
        self._apply()

    def set_clickable(self, clickable: bool, tooltip: str = ""):
        self._clickable = bool(clickable)
        self.setCursor(Qt.PointingHandCursor if clickable else Qt.ArrowCursor)
        self.setToolTip(tooltip)

    def enterEvent(self, event):
        if self._clickable:
            self._hover = True
            self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        if self._hover:
            self._hover = False
            self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if self._clickable and event.button() == Qt.LeftButton:
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if (self._clickable and event.button() == Qt.LeftButton
                and self.rect().contains(event.position().toPoint())):
            self.clicked.emit()
        super().mouseReleaseEvent(event)

    def paintEvent(self, event):
        if self._hover:
            from PySide6.QtGui import QPainter, QColor
            p = QPainter(self)
            p.fillRect(self.rect().adjusted(0, 0, -1, -1), QColor(Colors.bg_hover))
            p.end()
        super().paintEvent(event)
        w, h = self.width(), self.height()
        paint_border_lines(self, [
            (w - 1, 0, w - 1, h - 1),   # right divider
            (0, h - 1, w - 1, h - 1),   # bottom border
        ])


class StatusStrip(QFrame):
    """Observatory telemetry band: tiles + capture/camera state pill."""

    # The IMAGING tile opens the conditions panel on Live Monitoring.
    imaging_clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._capture_mode = 'idle'
        self._frame_count = 0
        self._static_shown = False
        self._fit_ready = False   # set by the first resize; until then all full
        self._build()

    def _build(self):
        self.setFixedHeight(52)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(f"StatusStrip {{ background: {Colors.bg_app}; }}")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        self.web_tile = StatusTile('web', "WEB")
        self.discord_tile = StatusTile('fa6b.discord', "DISCORD")
        self.roof_tile = StatusTile('home-roof', "ROOF")
        self.sky_tile = StatusTile('weather-partly-cloudy', "SKY")
        self.weather_tile = StatusTile('thermometer', "WEATHER")
        self.seeing_tile = StatusTile('star-four-points-outline', "SEEING")
        self.imaging_tile = StatusTile('telescope', "IMAGING")
        self.imaging_tile.set_clickable(True, _IMAGING_TOOLTIP)
        self.imaging_tile.clicked.connect(self.imaging_clicked.emit)
        self._tiles = (self.web_tile, self.discord_tile, self.roof_tile,
                       self.sky_tile, self.weather_tile, self.seeing_tile, self.imaging_tile)
        for t in self._tiles:
            lay.addWidget(t)
            t.content_changed.connect(self._refit)

        lay.addStretch(1)

        self.pill = StatePill()
        lay.addWidget(self.pill)
        self.pill.content_changed.connect(self._refit)

        # Sensible defaults until the first update arrives.
        self.set_web(False, False)
        self.set_discord(False)
        self.set_roof("Not configured", 'muted')
        self.set_sky("Not configured", 'muted')
        self.set_weather("Not configured", 'muted')
        self.set_seeing("—", 'muted')
        self.set_imaging("—", 'muted')
        self.set_capture_state('idle', "Idle")

    def paintEvent(self, event):
        # The tiles + pill paint their own bottom edge; this fills the bottom
        # border across the stretch gap between the last tile and the pill.
        super().paintEvent(event)
        h = self.height()
        paint_border_lines(self, [(0, h - 1, self.width(), h - 1)])

    # --- Narrow widths ---------------------------------------------------
    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit_ready = True
        self._refit()

    def _fit_items(self):
        # Fold order: tiles that only say "Not configured" first, then the
        # publish toggles, then the weather and seeing detail. Roof, Sky, the
        # pill and Imaging keep their text and only elide, Imaging last.
        roof, sky, weather = self.roof_tile, self.sky_tile, self.weather_tile
        collapse = {
            'web': 10, 'discord': 11, 'weather': 20, 'seeing': 21,
            'roof': 0 if roof.is_not_configured() else None,
            'sky': 1 if sky.is_not_configured() else None,
        }
        if weather.is_not_configured():
            collapse['weather'] = 2
        elide = {'sky': 0, 'roof': 0, 'imaging': 1}
        items = [tile.fit_item(key, collapse.get(key), elide.get(key))
                 for key, tile in self._keyed_tiles()]
        items.append(self.pill.fit_item('pill', elide_rank=0))
        return items

    def _keyed_tiles(self):
        return zip(('web', 'discord', 'roof', 'sky', 'weather', 'seeing', 'imaging'),
                   self._tiles)

    def _refit(self):
        if not self._fit_ready:
            return
        plan = plan_fit(self._fit_items(), self.width())
        for key, tile in self._keyed_tiles():
            tile.set_presentation(*plan[key])
        mode, width = plan['pill']
        self.pill.set_value_width(width if mode == ELIDED else None)

    # --- Publish toggles -------------------------------------------------
    def set_web(self, enabled: bool, running: bool = False):
        if running:
            self.web_tile.set_value("On", 'info', 'web')
        elif enabled:
            self.web_tile.set_value("Starting…", 'warn', 'web')
        else:
            self.web_tile.set_value("Off", 'muted', 'web-off')

    def set_discord(self, enabled: bool):
        if enabled:
            self.discord_tile.set_value("On", 'info', 'fa6b.discord')
        else:
            self.discord_tile.set_value("Off", 'muted', 'fa6b.discord')

    # --- Sensor tiles ----------------------------------------------------
    def set_roof(self, text: str, tone: str = 'primary', icon_name: str = 'home-roof'):
        self.roof_tile.set_value(text, tone, icon_name)

    def set_sky(self, text: str, tone: str = 'primary', icon_name: str = 'weather-partly-cloudy'):
        self.sky_tile.set_value(text, tone, icon_name)

    def set_weather(self, text: str, tone: str = 'primary'):
        self.weather_tile.set_value(text, tone, 'thermometer', mono=(tone != 'muted'))

    def set_seeing(self, text: str, tone: str = 'primary'):
        self.seeing_tile.set_value(text, tone, 'star-four-points-outline',
                                   mono=(tone != 'muted'))

    def set_imaging(self, text: str, tone: str = 'primary'):
        """The imaging-conditions verdict (GET /conditions), already worded."""
        self.imaging_tile.set_value(text, tone, 'telescope')

    def set_imaging_detail(self, detail: str = ""):
        """A line under the IMAGING tile's tooltip (the target and the Moon)."""
        self.imaging_tile.setToolTip("\n".join(
            p for p in (_IMAGING_TOOLTIP, detail) if p))

    def set_sensors_stale(self, stale: bool):
        """Dim the per-frame sensor tiles (roof/sky/seeing) when capture is idle."""
        self.roof_tile.set_stale(stale)
        self.sky_tile.set_stale(stale)
        self.seeing_tile.set_stale(stale)

    def set_weather_stale(self, stale: bool):
        """Dim the weather tile when its OpenWeather cache has gone cold (a dead
        API key or sustained network failure), so old values aren't read as live."""
        self.weather_tile.set_stale(stale)

    # --- Capture/camera pill --------------------------------------------
    def set_capture_state(self, mode: str, text: str = None):
        self._capture_mode = mode
        if mode == 'capturing' and text is None:
            text = f"Capturing · frame {self._frame_count}"
        self.pill.set_state(mode, text or "")

    def set_frame_count(self, count: int):
        self._frame_count = count
        if self._capture_mode == 'capturing':
            self.pill.set_state('capturing', f"Capturing · frame {count}")

    # --- Per-frame metadata ---------------------------------------------
    def update_from_metadata(self, metadata: dict):
        """Fill roof/sky/seeing/weather from a freshly processed frame's metadata."""
        if not metadata:
            return

        # Weather tokens are merged into metadata by the overlay renderer, so
        # reading them here keeps the tile in lockstep with the image overlay.
        wtemp = metadata.get('WEATHER_TEMP')
        if wtemp:
            parts = [p for p in (wtemp, metadata.get('WEATHER_CONDITION'),
                                 metadata.get('WEATHER_CLOUDS')) if p]
            self.set_weather(" · ".join(parts), 'primary')

        ml = metadata.get('_ML_RESULTS') or {}

        # A noise-only frame still shows the model's roof reading — the static
        # score is unproven and must not hide an identification — but flags it.
        static = bool(ml.get('frame_is_static'))
        tooltip = _STATIC_TOOLTIP if static else ""
        self.roof_tile.setToolTip(tooltip)
        self.sky_tile.setToolTip(tooltip)

        # The warning is painted over whatever the tiles held, and a tile is
        # only repainted when a frame carries a verdict for it. A classifier
        # that is off or that failed gives none, so the first good frame after
        # a static one must clear the warning itself or it stays up for good.
        recovering = self._static_shown and not static
        self._static_shown = static

        roof = ml.get('roof_status')
        if roof in ('Open', 'Closed'):
            if static:
                self.set_roof(f"{roof} · unreliable", 'warn', 'alert-outline')
            else:
                self.set_roof(roof, 'ok' if roof == 'Open' else 'error')
        elif recovering:
            # The flagged reading came from a noise frame, so there is no last
            # good value to fall back to.
            self.set_roof("—", 'muted')

        if static:
            self.set_sky("Too much static", 'warn', 'alert-outline')
            # A star count taken from noise is not a seeing measurement.
            self.set_seeing("Roof closed" if roof == 'Closed' else "—", 'muted')
            return

        # With the roof closed the pier camera only sees the closed roof, so the
        # frame-derived sky condition and star/seeing values are meaningless —
        # show "Roof closed" rather than synthesised noise. (Weather is sourced
        # independently from OpenWeather, so its tile keeps updating.)
        if roof == 'Closed':
            self.set_sky("Roof closed", 'muted')
            self.set_seeing("Roof closed", 'muted')
            return

        # The observable-sky gate judged the frame to show no sky worth
        # reading (issue #93): the reason takes the Sky tile the way "Roof
        # closed" does, and star detection was skipped, so Seeing has nothing.
        gate_text = _GATE_REASON_TEXT.get(metadata.get(GATE_REASON_KEY))
        if gate_text:
            self.set_sky(gate_text, 'muted')
            self.set_seeing("—", 'muted')
            return

        sky = ml.get('sky_condition')
        if sky and sky != 'N/A':
            tone = 'ok' if sky == 'Clear' else ('warn' if sky == 'Partly Cloudy' else 'error')
            self.set_sky(sky, tone)
        elif recovering:
            self.set_sky("—", 'muted')

        stars = metadata.get('STAR_COUNT')
        if stars not in (None, 'N/A'):
            fwhm = metadata.get('FWHM')
            txt = f"{stars} stars"
            if fwhm not in (None, 'N/A'):
                txt += f" · FWHM {fwhm}"
            self.set_seeing(txt, 'primary')

    def refresh_styles(self):
        for t in self._tiles:
            t.refresh_styles()
