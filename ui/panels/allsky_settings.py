"""
All-Sky Overlay Settings Panel.

Provides:
  - Calibration status + "Calibrate Now" button
  - Layer toggles: Constellations, Bright Stars, Messier, NGC, Planets, NINA target
  - Per-layer color, opacity, line width controls
  - Enabled/disabled master toggle
  - Observable-sky gate floors (exposure, star detections)
"""
import copy

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QScrollArea, QFrame,
)
from PySide6.QtCore import Qt, Signal
from qfluentwidgets import (
    PushButton, ComboBox, CaptionLabel, BodyLabel, SubtitleLabel, MessageBox,
)
from services.allsky.label_size import (
    CONFIG_KEY as LABEL_SIZE_KEY, DEFAULT_PRESET as LABEL_SIZE_DEFAULT,
    preset_keys, preset_names,
)
from services.allsky.object_label_text import (
    CONFIG_KEY as LABEL_STYLE_KEY, DEFAULT_STYLE as LABEL_STYLE_DEFAULT,
    known_style, style_keys, style_names,
)
from services.config_defaults import DEFAULT_CONFIG
from ..components.scroll_safe_spinbox import DoubleSpinBox, SpinBox

from ..theme.tokens import Spacing
from ..theme.icons import mdi
from ..components.cards import CollapsibleCard
from .allsky_nina_target_card import NinaTargetCard
from .allsky_settings_rows import (  # re-exported for existing callers
    LayerToggleRow, ColorPaletteRow, section_card as _section_card,
)


def _merge(base: dict, over: dict) -> dict:
    """Recursively lay ``over`` onto ``base`` (in place) and return it."""
    for key, value in (over or {}).items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _merge(base[key], value)
        else:
            base[key] = copy.deepcopy(value)
    return base


class QualityBadge(QFrame):
    """Colored pill badge showing the current calibration quality level."""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 0, 10, 0)
        layout.setSpacing(4)

        self._dot = QLabel()
        self._dot.setFixedSize(8, 8)
        layout.addWidget(self._dot)

        self._label = CaptionLabel("Not calibrated")
        layout.addWidget(self._label)

        self.setFixedHeight(26)
        self._level = 'none'
        self._attention = ''
        self._note = ''
        self.set_quality('none')

    # Amber, whatever the rating: a green pill over a calibration the app
    # can no longer confirm reads as "all is well" (issue #79).
    _ATTENTION_COLOURS = ('#2D2305', '#FFD166')
    _ATTENTION_SUFFIX = {'unconfirmed': 'unconfirmed',
                         'misaligned': 'check alignment'}

    def set_attention(self, attention: str) -> None:
        self._attention = attention if attention in self._ATTENTION_SUFFIX else ''
        self.set_quality(self._level)

    def set_note(self, note: str) -> None:
        """Why the rating is what it is (a capped chance fit); '' for none.
        Shown in the tooltip, where the level's stock description would
        otherwise call a 60-frame chance fit a "single image"."""
        self._note = note or ''
        self.set_quality(self._level)

    def set_quality(self, level: str) -> None:
        from services.allsky.calibration_service import CalibrationQuality
        self._level = level
        bg, text = CalibrationQuality.badge_colors(level)
        desc = CalibrationQuality.description(level)
        label = level.capitalize() if level != 'none' else 'None'
        if self._attention and level != 'none':
            bg, text = self._ATTENTION_COLOURS
            label = f"{label} — {self._ATTENTION_SUFFIX[self._attention]}"
        # The note is the specific reason and names the saved rating itself;
        # the generic suffix is for a caution with no measurement behind it.
        if self._note and level != 'none':
            desc = f"{desc}. {self._note}"
        elif self._attention and level != 'none':
            desc = f"{desc} (rating from when it was saved)"

        self._label.setText(label)
        self.setToolTip(desc)
        self.setStyleSheet(
            f"QFrame {{ background: {bg}; border-radius: 13px; }}"
        )
        self._label.setStyleSheet(
            f"color: {text}; font-size: 11px; font-weight: 600;"
        )
        self._dot.setStyleSheet(
            f"background: {text}; border-radius: 4px;"
        )


class AllSkySettingsPanel(QScrollArea):
    """
    Scrollable settings panel for the All-Sky overlay feature.
    Panels are UI-only — business logic is in AllSkyController.
    """

    # Emitted whenever a setting changes so controller can save config
    settings_changed = Signal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        inner = QWidget()
        self.setWidget(inner)
        self._layout = QVBoxLayout(inner)
        self._layout.setContentsMargins(Spacing.base, Spacing.base, Spacing.base, Spacing.base)
        self._layout.setSpacing(Spacing.sm)

        # The allsky_overlay dict last loaded; get_config merges the panel's
        # controls over it so keys the panel has no control for
        # (utc_offset_hours, planets.colors, constellations.edge_fade_px,
        # anything added later) survive an edit on this page.
        self._loaded = {}

        self._build_header()
        self._build_calibration_card()
        self._build_master_toggle()
        self._build_gate_card()
        self._build_burn_in_card()
        self._build_constellations_card()
        self._build_bright_stars_card()
        self._build_messier_card()
        self._build_ngc_card()
        self._build_planets_card()
        self._build_nina_target_card()

        self._layout.addStretch()

    # ------------------------------------------------------------------
    # Build sections
    # ------------------------------------------------------------------

    def _build_header(self):
        title = SubtitleLabel("All-Sky Overlay")
        self._layout.addWidget(title)
        desc = CaptionLabel(
            "Overlay constellation lines, DSO labels, and planet positions on each frame.\n"
            "Requires fisheye lens calibration before first use.\n"
            "Designed for all-sky cameras: a fisheye lens on a square (or near-square) "
            "sensor, with the full sky circle visible in frame. Regular lenses, or "
            "wide sensors that crop the fisheye circle, will not calibrate correctly."
        )
        desc.setWordWrap(True)
        self._layout.addWidget(desc)

    def _build_calibration_card(self):
        card, vl = _section_card("Lens Calibration")

        # Quality badge + status label row
        badge_row = QHBoxLayout()
        self._quality_badge = QualityBadge()
        badge_row.addWidget(self._quality_badge)
        badge_row.addStretch()
        vl.addLayout(badge_row)

        self._status_label = BodyLabel("Not calibrated")
        self._status_label.setWordWrap(True)
        vl.addWidget(self._status_label)

        self._attention_label = CaptionLabel("")
        self._attention_label.setWordWrap(True)
        self._attention_label.setStyleSheet("color: #FFD166;")
        self._attention_label.hide()
        vl.addWidget(self._attention_label)

        self._calibrate_btn = PushButton("Calibrate Now", icon=mdi('refresh'))
        self._calibrate_btn.clicked.connect(self._on_calibrate_clicked)
        vl.addWidget(self._calibrate_btn)

        # Guided fallback for hard installs (obstructed / near-vertical / hazy)
        # where automatic calibration can't determine orientation.
        self._guided_btn = PushButton("Guided Calibration…", icon=mdi('target'))
        self._guided_btn.clicked.connect(self._on_guided_clicked)
        vl.addWidget(self._guided_btn)

        # Escape hatch for a bad saved calibration (a wrong model seeds every
        # background refinement, so deleting it beats keeping it).
        # Every calibration that went live is kept; this restores one
        # (a guided solve replaced by automatic refinement, discussion #105).
        self._history_btn = PushButton("Calibration History…", icon=mdi('history'))
        self._history_btn.clicked.connect(
            lambda: self.settings_changed.emit({'_action': 'calibration_history'}))
        vl.addWidget(self._history_btn)

        self._reset_btn = PushButton("Reset Calibration…", icon=mdi('delete'))
        self._reset_btn.clicked.connect(self._on_reset_clicked)
        vl.addWidget(self._reset_btn)

        # Support handle: writes the frames auto-calibration is working from
        # to a small file the diagnostics bundle picks up (no images).
        self._dump_btn = PushButton("Dump calibration buffer", icon=mdi('database-export'))
        self._dump_btn.setToolTip(
            "Save the star detections collected for automatic calibration to a "
            "file for a bug report. No images are saved.")
        self._dump_btn.clicked.connect(self._on_dump_clicked)
        vl.addWidget(self._dump_btn)

        # The learned equipment map outlives the calibration on purpose; this
        # is the way to forget it after the rig is rearranged.
        self._reset_map_btn = PushButton("Reset Equipment Map…", icon=mdi('delete'))
        self._reset_map_btn.clicked.connect(self._on_reset_map_clicked)
        vl.addWidget(self._reset_map_btn)

        self._layout.addWidget(card)

    def _build_master_toggle(self):
        card, vl = _section_card("Enable Overlay")
        self._master_toggle = LayerToggleRow("All-Sky overlay enabled", default=False)
        self._master_toggle.toggled.connect(self._on_setting_changed)
        vl.addWidget(self._master_toggle)
        self._top_n = self._spin_row(vl, "Max objects visible", 5, 50, 15, 5)
        row = QHBoxLayout()
        row.addWidget(BodyLabel("Label size"))
        self._label_size = ComboBox()
        self._label_size.addItems(list(preset_names()))
        self._label_size.setCurrentIndex(preset_keys().index(LABEL_SIZE_DEFAULT))
        self._label_size.currentIndexChanged.connect(self._on_setting_changed)
        row.addWidget(self._label_size)
        vl.addLayout(row)
        self._behind_equipment = LayerToggleRow(
            "Show labels behind equipment", default=False)
        self._behind_equipment.setToolTip(
            "Keep star and planet names on where the pier or a telescope blocks "
            "the sky. Off: names only where the sky is seen. A closed roof still "
            "hides every label.")
        self._behind_equipment.toggled.connect(self._on_setting_changed)
        vl.addWidget(self._behind_equipment)
        self._layout.addWidget(card)

    def _build_gate_card(self):
        card, vl = _section_card("When the overlay is drawn")
        desc = CaptionLabel(
            "The overlay, automatic calibration and star detection pause on frames "
            "that cannot show a night sky: exposures shorter than the floor, and a "
            "roof that reads Open while no stars are detected (a lit roof or an "
            "overcast sky). Set the exposure floor to 0 to turn that check off."
        )
        desc.setWordWrap(True)
        vl.addWidget(desc)
        row = QHBoxLayout()
        row.addWidget(BodyLabel("Minimum exposure (seconds)"))
        self._min_exposure = DoubleSpinBox()
        self._min_exposure.setRange(0.0, 60.0)
        self._min_exposure.setDecimals(2)
        self._min_exposure.setSingleStep(0.1)
        self._min_exposure.setValue(0.5)
        self._min_exposure.valueChanged.connect(self._on_setting_changed)
        row.addWidget(self._min_exposure)
        vl.addLayout(row)
        self._min_stars = self._spin_row(vl, "Minimum stars detected", 0, 500, 100, 10)
        self._layout.addWidget(card)

    def _build_burn_in_card(self):
        card, vl = _section_card("Burn overlay into output")
        desc = CaptionLabel(
            "Off by default: the overlay only shows in this app's live preview. "
            "Turn one on to bake it into that destination's actual pixels."
        )
        desc.setWordWrap(True)
        vl.addWidget(desc)
        self._burn_saved_file = LayerToggleRow("Saved image (also Discord)", default=False)
        self._burn_web = LayerToggleRow("Web server (also Image Library)", default=False)
        self._burn_timelapse = LayerToggleRow("Timelapse video", default=False)
        for row in (self._burn_saved_file, self._burn_web, self._burn_timelapse):
            row.toggled.connect(self._on_setting_changed)
            vl.addWidget(row)
        self._layout.addWidget(card)

    def _build_constellations_card(self):
        card = CollapsibleCard("Constellations", mdi('vector-polyline'))
        self._con_enabled = LayerToggleRow("Show constellations", default=True)
        self._con_lines = LayerToggleRow("Lines", default=True)
        self._con_labels = LayerToggleRow("Labels", default=True)
        for row in (self._con_enabled, self._con_lines, self._con_labels):
            row.toggled.connect(self._on_setting_changed)
            card.add_widget(row)
        self._con_color = ColorPaletteRow('#4488FF')
        self._con_color.color_changed.connect(self._on_setting_changed)
        card.add_widget(self._con_color)
        self._layout.addWidget(card)

    def _build_bright_stars_card(self):
        card = CollapsibleCard("Bright Stars", mdi('star-four-points'))
        self._stars_enabled = LayerToggleRow("Show named bright stars", default=False)
        self._stars_enabled.toggled.connect(self._on_setting_changed)
        card.add_widget(self._stars_enabled)
        self._stars_max_mag = self._spin_row_in(card, "Max magnitude", 1, 5, 3, 1)
        self._stars_bayer = LayerToggleRow("Use Bayer designation when unnamed", default=False)
        self._stars_bayer.toggled.connect(self._on_setting_changed)
        card.add_widget(self._stars_bayer)
        self._stars_color = ColorPaletteRow('#FFDD44')
        self._stars_color.color_changed.connect(self._on_setting_changed)
        card.add_widget(self._stars_color)
        self._layout.addWidget(card)

    def _build_messier_card(self):
        card = CollapsibleCard("Messier Objects", mdi('blur'))
        self._messier_enabled = LayerToggleRow("Show Messier objects", default=True)
        self._messier_enabled.toggled.connect(self._on_setting_changed)
        card.add_widget(self._messier_enabled)
        self._messier_style = self._style_row_in(card)
        self._messier_color = ColorPaletteRow('#FF8844')
        self._messier_color.color_changed.connect(self._on_setting_changed)
        card.add_widget(self._messier_color)
        self._layout.addWidget(card)

    def _build_ngc_card(self):
        card = CollapsibleCard("NGC/IC Objects", mdi('telescope'))
        self._ngc_enabled = LayerToggleRow("Show NGC objects (mag filtered)", default=False)
        self._ngc_enabled.toggled.connect(self._on_setting_changed)
        card.add_widget(self._ngc_enabled)
        self._ngc_max_mag = self._spin_row_in(card, "Max magnitude", 5, 12, 8, 1)
        self._ngc_style = self._style_row_in(card)
        self._ngc_color = ColorPaletteRow('#88FF44')
        self._ngc_color.color_changed.connect(self._on_setting_changed)
        card.add_widget(self._ngc_color)
        self._layout.addWidget(card)

    def _build_planets_card(self):
        card = CollapsibleCard("Planets & Moon", mdi('orbit'))
        self._planets_enabled = LayerToggleRow("Show planets & Moon", default=True)
        self._planets_enabled.toggled.connect(self._on_setting_changed)
        card.add_widget(self._planets_enabled)
        self._planets_color = ColorPaletteRow('#FFFFCC')
        self._planets_color.color_changed.connect(self._on_setting_changed)
        card.add_widget(self._planets_color)
        self._layout.addWidget(card)

    def _build_nina_target_card(self):
        self._nina_card = NinaTargetCard()
        self._nina_card.changed.connect(self._on_setting_changed)
        self._layout.addWidget(self._nina_card)

    def _spin_row(self, layout, label: str, min_v: int, max_v: int,
                  default: int, step: int) -> SpinBox:
        row = QHBoxLayout()
        row.addWidget(BodyLabel(label))
        spin = SpinBox()
        spin.setRange(min_v, max_v)
        spin.setValue(default)
        spin.setSingleStep(step)
        spin.valueChanged.connect(self._on_setting_changed)
        row.addWidget(spin)
        layout.addLayout(row)
        return spin

    def _spin_row_in(self, card: CollapsibleCard, label: str, min_v: int, max_v: int,
                     default: int, step: int) -> SpinBox:
        spin = SpinBox()
        spin.setRange(min_v, max_v)
        spin.setValue(default)
        spin.setSingleStep(step)
        spin.valueChanged.connect(self._on_setting_changed)
        card.add_row(label, spin)
        return spin

    def _style_row_in(self, card: CollapsibleCard) -> ComboBox:
        combo = ComboBox()
        combo.addItems(list(style_names()))
        combo.setCurrentIndex(style_keys().index(LABEL_STYLE_DEFAULT))
        combo.currentIndexChanged.connect(self._on_setting_changed)
        card.add_row("Label text", combo)
        return combo

    # ------------------------------------------------------------------
    # Public API (called by controller)
    # ------------------------------------------------------------------

    def set_status(self, message: str) -> None:
        self._status_label.setText(message)

    def set_quality(self, level: str) -> None:
        """Update the calibration quality badge."""
        self._quality_badge.set_quality(level)

    def set_quality_note(self, note: str) -> None:
        """Badge tooltip detail: why a rating is capped ('' clears it)."""
        self._quality_badge.set_note(note)

    def set_badge_quality(self, level: str, note: str) -> None:
        """Level and reason together (a live verdict on the saved model)."""
        self._quality_badge.set_note(note)
        self._quality_badge.set_quality(level)

    def set_attention(self, level: str, message: str) -> None:
        """Caution beside the badge when the rating can't be confirmed."""
        self._quality_badge.set_attention(level)
        self._attention_label.setText(message)
        self._attention_label.setVisible(bool(level and message))

    def set_calibrating(self, active: bool) -> None:
        self._calibrate_btn.setEnabled(not active)
        self._calibrate_btn.setText("Calibrating…" if active else "Calibrate Now")

    def load_from_config(self, config: dict) -> None:
        """Populate all controls from the given allsky_overlay config dict."""
        c = config
        self._loaded = copy.deepcopy(c) if isinstance(c, dict) else {}
        self._master_toggle.set_checked(c.get('enabled', False))
        self._top_n.setValue(int(c.get('top_n', 15)))
        self._min_exposure.setValue(float(c.get('min_exposure_s', 0.5)))
        self._min_stars.setValue(int(c.get('min_star_detections', 100)))
        keys = preset_keys()
        preset = c.get(LABEL_SIZE_KEY, LABEL_SIZE_DEFAULT)
        self._label_size.setCurrentIndex(
            keys.index(preset if preset in keys else LABEL_SIZE_DEFAULT))
        self._behind_equipment.set_checked(bool(c.get('labels_behind_equipment', False)))

        burn = c.get('burn_into_output', {})
        self._burn_saved_file.set_checked(burn.get('saved_file', False))
        self._burn_web.set_checked(burn.get('web', False))
        self._burn_timelapse.set_checked(burn.get('timelapse', False))

        con = c.get('constellations', {})
        self._con_enabled.set_checked(con.get('enabled', True))
        self._con_lines.set_checked(con.get('lines', True))
        self._con_labels.set_checked(con.get('labels', True))
        self._con_color.set_color(con.get('color', '#4488FF'))

        stars = c.get('bright_stars', {})
        self._stars_enabled.set_checked(stars.get('enabled', False))
        self._stars_max_mag.setValue(int(round(float(stars.get('max_magnitude', 3)))))
        self._stars_bayer.set_checked(stars.get('bayer_fallback', False))
        self._stars_color.set_color(stars.get('color', '#FFDD44'))

        messier = c.get('messier', {})
        self._messier_enabled.set_checked(messier.get('enabled', True))
        self._messier_color.set_color(messier.get('color', '#FF8844'))
        self._messier_style.setCurrentIndex(
            style_keys().index(known_style(messier.get(LABEL_STYLE_KEY))))

        ngc = c.get('ngc', {})
        self._ngc_enabled.set_checked(ngc.get('enabled', False))
        self._ngc_max_mag.setValue(int(ngc.get('min_magnitude', 8)))
        self._ngc_color.set_color(ngc.get('color', '#88FF44'))
        self._ngc_style.setCurrentIndex(
            style_keys().index(known_style(ngc.get(LABEL_STYLE_KEY))))

        planets = c.get('planets', {})
        self._planets_enabled.set_checked(planets.get('enabled', True))
        self._planets_color.set_color(planets.get('color', '#FFFFCC'))

        self._nina_card.load(c.get('nina_target', {}))

    def get_config(self) -> dict:
        """Current UI state merged over the last loaded allsky_overlay dict.

        Defaults underneath, the loaded dict over them, the panel's own
        controls on top — so a key without a control here is carried through
        unchanged rather than dropped on the first edit (H11, issue #93).
        """
        cfg = _merge(copy.deepcopy(DEFAULT_CONFIG['allsky_overlay']), self._loaded)
        cfg['enabled'] = self._master_toggle.is_checked()
        cfg['top_n'] = self._top_n.value()
        cfg['min_exposure_s'] = float(self._min_exposure.value())
        cfg['min_star_detections'] = int(self._min_stars.value())
        cfg[LABEL_SIZE_KEY] = preset_keys()[self._label_size.currentIndex()]
        cfg['labels_behind_equipment'] = self._behind_equipment.is_checked()
        cfg['burn_into_output'].update({
            'saved_file': self._burn_saved_file.is_checked(),
            'web': self._burn_web.is_checked(),
            'timelapse': self._burn_timelapse.is_checked(),
        })
        cfg['constellations'].update({
            'enabled': self._con_enabled.is_checked(),
            'lines': self._con_lines.is_checked(),
            'labels': self._con_labels.is_checked(),
            'color': self._con_color.selected_color(),
        })
        cfg['bright_stars'].update({
            'enabled': self._stars_enabled.is_checked(),
            'max_magnitude': float(self._stars_max_mag.value()),
            'bayer_fallback': self._stars_bayer.is_checked(),
            'color': self._stars_color.selected_color(),
        })
        cfg['messier'].update({
            'enabled': self._messier_enabled.is_checked(),
            'color': self._messier_color.selected_color(),
            LABEL_STYLE_KEY: style_keys()[self._messier_style.currentIndex()],
        })
        cfg['ngc'].update({
            'enabled': self._ngc_enabled.is_checked(),
            'min_magnitude': float(self._ngc_max_mag.value()),
            'color': self._ngc_color.selected_color(),
            LABEL_STYLE_KEY: style_keys()[self._ngc_style.currentIndex()],
        })
        cfg['planets'].update({
            'enabled': self._planets_enabled.is_checked(),
            'color': self._planets_color.selected_color(),
        })
        cfg['nina_target'].update(self._nina_card.values())
        return cfg

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _on_calibrate_clicked(self):
        """Signal controller to start calibration (controller is wired in main_window)."""
        # Controller is connected externally; emit settings_changed as a trigger
        self.settings_changed.emit({'_action': 'calibrate'})

    def _on_guided_clicked(self):
        """Signal main_window to open the guided-calibration dialog."""
        self.settings_changed.emit({'_action': 'guided_calibrate'})

    def _on_reset_clicked(self):
        """Confirm, then signal main_window to reset the calibration."""
        box = MessageBox(
            "Reset calibration?",
            "This deletes the saved lens calibration. The overlay stops "
            "rendering until a new calibration exists — created "
            "automatically as frames accumulate, or via Guided Calibration."
            "\n\nUse this if the overlay is badly misaligned and refuses to "
            "improve: a bad saved calibration can hold back every automatic "
            "refinement.",
            self.window(),
        )
        box.yesButton.setText("Reset")
        box.cancelButton.setText("Cancel")
        if box.exec():
            self.settings_changed.emit({'_action': 'reset_calibration'})

    def _on_dump_clicked(self):
        """Signal main_window to dump the calibration buffer."""
        self.settings_changed.emit({'_action': 'dump_buffer'})

    def _on_reset_map_clicked(self):
        """Confirm, then signal main_window to forget the equipment map."""
        box = MessageBox(
            "Reset equipment map?",
            "This forgets where Sentinel has learned that telescopes, mounts "
            "and other equipment sit in the frame. Labels may land on "
            "equipment for a while; the map relearns it over the next "
            "clear nights."
            "\n\nUse this after moving the camera or rearranging the rig.",
            self.window(),
        )
        box.yesButton.setText("Reset")
        box.cancelButton.setText("Cancel")
        if box.exec():
            self.settings_changed.emit({'_action': 'reset_equipment_map'})

    def _on_setting_changed(self, *_):
        self.settings_changed.emit(self.get_config())
