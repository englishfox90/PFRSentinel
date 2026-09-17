"""
Timelapse Panel
Dedicated nav page for daily timelapse video recording (camera mode only).
The ffmpeg install prompt lives in ffmpeg_install_card.
"""
import os
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QScrollArea,
    QFileDialog
)
from PySide6.QtCore import Qt, Signal, QTimer, QTime
from qfluentwidgets import (
    CardWidget, BodyLabel, CaptionLabel,
    PushButton, PrimaryPushButton, ComboBox, LineEdit, TimePicker,
    SpinBox
)

from ..theme.tokens import Colors, Spacing
from ..theme.icons import mdi
from ..components.cards import SettingsCard, FormRow, SwitchRow, CollapsibleCard, ClickSlider
from .ffmpeg_install_card import FfmpegInstallCard
from .timelapse_status_card import TimelapseStatusCard
from .youtube_upload_card import YouTubeUploadCard
from services.ffmpeg_utils import is_ffmpeg_available


# ------------------------------------------------------------------ #
#  Main panel                                                          #
# ------------------------------------------------------------------ #

class TimelapsePanel(QScrollArea):
    """
    Full-page timelapse settings panel (camera mode only).

    Shows an install prompt when ffmpeg is missing, and the
    full settings UI once ffmpeg is available.
    """

    settings_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.main_window = parent
        self._loading_config = True
        self._setup_ui()
        self._loading_config = False

        # Refresh status every 5 seconds
        self._status_timer = QTimer(self)
        self._status_timer.setInterval(5000)
        self._status_timer.timeout.connect(self._refresh_status)
        self._status_timer.start()

    def _setup_ui(self):
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setStyleSheet(f"""
            QScrollArea {{
                background-color: {Colors.bg_app};
                border: none;
            }}
        """)

        content = QWidget()
        self.setWidget(content)

        layout = QVBoxLayout(content)
        layout.setContentsMargins(Spacing.base, Spacing.base, Spacing.base, Spacing.base)
        layout.setSpacing(Spacing.card_gap)

        # Camera-mode-only notice (shown when in watch mode)
        self._watch_mode_notice = CardWidget()
        notice_layout = QVBoxLayout(self._watch_mode_notice)
        notice_layout.setContentsMargins(Spacing.card_padding, Spacing.card_padding,
                                          Spacing.card_padding, Spacing.card_padding)
        notice_lbl = BodyLabel("Timelapse recording is only available in Camera (ZWO) capture mode.")
        notice_lbl.setWordWrap(True)
        notice_lbl.setStyleSheet(f"color: {Colors.text_secondary};")
        notice_layout.addWidget(notice_lbl)
        layout.addWidget(self._watch_mode_notice)
        self._watch_mode_notice.hide()

        # ffmpeg install card (shown when ffmpeg missing, hidden otherwise)
        self._install_card = FfmpegInstallCard()
        self._install_card.install_succeeded.connect(self._on_ffmpeg_installed)
        layout.addWidget(self._install_card)

        # Settings cards (shown when ffmpeg available, hidden otherwise)
        self._settings_container = QWidget()
        settings_layout = QVBoxLayout(self._settings_container)
        settings_layout.setContentsMargins(0, 0, 0, 0)
        settings_layout.setSpacing(Spacing.card_gap)
        self._build_settings(settings_layout)
        layout.addWidget(self._settings_container)

        layout.addStretch()

        # Show correct initial state
        self._check_ffmpeg()

    def _check_ffmpeg(self):
        if is_ffmpeg_available():
            self._install_card.hide()
            self._settings_container.show()
        else:
            self._install_card.show()
            self._settings_container.hide()

    def _on_ffmpeg_installed(self):
        self._install_card.hide()
        self._settings_container.show()

    # ------------------------------------------------------------------ #
    #  Settings UI                                                         #
    # ------------------------------------------------------------------ #

    def _build_settings(self, layout: QVBoxLayout):
        """Build the full timelapse settings cards."""

        # === ENABLE ===
        enable_card = SettingsCard("Daily Timelapse", "Record a compressed video of each night's session")

        self._enable_switch = SwitchRow("Enable Timelapse", "Camera mode only")
        self._enable_switch.toggled.connect(self._on_enable_changed)
        enable_card.add_widget(self._enable_switch)
        layout.addWidget(enable_card)

        # === WINDOW ===
        window_card = CollapsibleCard("Recording Window", mdi('clock-time-four-outline'))

        self._window_mode_combo = ComboBox()
        self._window_mode_combo.addItems(["Sunset / Sunrise", "Fixed Times", "Always On", "Roof Open (Beta)"])
        self._window_mode_combo.currentIndexChanged.connect(self._on_window_mode_changed)
        window_card.add_row("Window Mode", self._window_mode_combo,
                             "When to record each day")

        # Sun mode sub-options
        self._sun_options = QWidget()
        sun_layout = QVBoxLayout(self._sun_options)
        sun_layout.setContentsMargins(0, 0, 0, 0)
        sun_layout.setSpacing(Spacing.input_gap)

        self._sun_mode_combo = ComboBox()
        self._sun_mode_combo.addItems([
            "Astronomical (darkest)",
            "Nautical",
            "Civil",
            "Sunset / Sunrise",
        ])
        self._sun_mode_combo.currentIndexChanged.connect(self._on_settings_changed)
        sun_layout.addWidget(FormRow("Twilight Depth", self._sun_mode_combo))

        sun_note = CaptionLabel("Location taken from weather settings (latitude / longitude).")
        sun_note.setStyleSheet(f"color: {Colors.text_muted};")
        sun_layout.addWidget(sun_note)
        window_card.add_widget(self._sun_options)

        # Fixed time sub-options
        self._fixed_options = QWidget()
        fixed_layout = QVBoxLayout(self._fixed_options)
        fixed_layout.setContentsMargins(0, 0, 0, 0)
        fixed_layout.setSpacing(Spacing.input_gap)

        times_row = QHBoxLayout()
        times_row.setSpacing(Spacing.sm)
        self._start_time_input = TimePicker()
        self._start_time_input.setTime(QTime(18, 0))
        self._start_time_input.setToolTip("Recording start time (24hr)")
        self._start_time_input.timeChanged.connect(self._on_settings_changed)
        end_label = BodyLabel("→")
        self._end_time_input = TimePicker()
        self._end_time_input.setTime(QTime(6, 0))
        self._end_time_input.setToolTip("Recording end time (24hr, can span midnight)")
        self._end_time_input.timeChanged.connect(self._on_settings_changed)
        times_row.addWidget(self._start_time_input)
        times_row.addWidget(end_label)
        times_row.addWidget(self._end_time_input)
        times_row.addStretch()
        times_widget = QWidget()
        times_widget.setLayout(times_row)
        fixed_layout.addWidget(FormRow("Start → End", times_widget,
                                        "Overnight crossing (e.g. 18:00 → 06:00) is supported"))
        self._fixed_options.hide()
        window_card.add_widget(self._fixed_options)

        # Roof open sub-options (Beta)
        self._roof_options = QWidget()
        roof_layout = QVBoxLayout(self._roof_options)
        roof_layout.setContentsMargins(0, 0, 0, 0)
        roof_layout.setSpacing(Spacing.xs)

        roof_warning = CaptionLabel(
            "\u26a0 Beta — Records while the ML roof model reports the roof as open. "
            "ML Models must be enabled in Image Processing settings. "
            "If ML is disabled or no frame has been processed yet, recording will not start."
        )
        roof_warning.setWordWrap(True)
        roof_warning.setStyleSheet(f"color: {Colors.warning_text};")
        roof_layout.addWidget(roof_warning)

        self._roof_options.hide()
        window_card.add_widget(self._roof_options)

        layout.addWidget(window_card)

        # === FRAME / QUALITY ===
        quality_card = CollapsibleCard("Frame & Quality", mdi('video'))

        self._fps_spin = SpinBox()
        self._fps_spin.setRange(1, 60)
        self._fps_spin.setValue(24)
        self._fps_spin.setSuffix(" fps")
        self._fps_spin.valueChanged.connect(self._on_settings_changed)
        quality_card.add_row("Playback speed", self._fps_spin, "Output video frame rate")

        self._resolution_combo = ComboBox()
        self._resolution_combo.addItems(["Original", "1920 px", "1440 px", "1280 px", "720 px"])
        self._resolution_combo.setCurrentIndex(1)  # default 1920px
        self._resolution_combo.currentIndexChanged.connect(self._on_settings_changed)
        quality_card.add_row("Output resolution", self._resolution_combo,
                              "Downscale longest side — reduces file size significantly")

        self._quality_combo = ComboBox()
        self._quality_combo.addItems([
            "Efficient  ·  smallest file",
            "Balanced  ·  recommended",
            "High quality  ·  larger file",
            "Maximum  ·  largest file",
        ])
        self._quality_combo.setCurrentIndex(1)  # default: Balanced
        self._quality_combo.currentIndexChanged.connect(self._on_settings_changed)
        quality_card.add_row("Video quality", self._quality_combo,
                              "Higher quality = larger file size")

        self._overlays_switch = SwitchRow(
            "Include overlays in video",
            "Overlay text will cycle visibly at playback speed"
        )
        self._overlays_switch.toggled.connect(self._on_settings_changed)
        quality_card.add_widget(self._overlays_switch)

        layout.addWidget(quality_card)

        # === FPS CALCULATOR ===
        fps_calc_card = CollapsibleCard("Playback Speed Calculator", mdi('play-speed'))

        calc_note = CaptionLabel(
            "How long should a typical session's video be? "
            "Drag the slider and the FPS will update automatically."
        )
        calc_note.setWordWrap(True)
        calc_note.setStyleSheet(f"color: {Colors.text_muted};")
        fps_calc_card.add_widget(calc_note)

        self._calc_hours_spin = SpinBox()
        self._calc_hours_spin.setRange(1, 24)
        self._calc_hours_spin.setValue(6)
        self._calc_hours_spin.setSuffix(" hr")
        self._calc_hours_spin.valueChanged.connect(self._update_calculator)
        fps_calc_card.add_row("Session duration", self._calc_hours_spin,
                              "Length of a typical imaging session")

        self._calc_interval_sec = 300
        self._calc_interval_label = BodyLabel("300 s")
        self._calc_interval_label.setStyleSheet(f"color: {Colors.text_secondary};")
        fps_calc_card.add_row("Capture interval", self._calc_interval_label,
                              "Read from Capture settings — change it there")

        # Slider: track row + min/max legend row stacked vertically
        slider_widget = QWidget()
        slider_vbox = QVBoxLayout(slider_widget)
        slider_vbox.setContentsMargins(0, 0, 0, 0)
        slider_vbox.setSpacing(2)

        # Track row: [slider] [live value]
        track_row = QWidget()
        slider_layout = QHBoxLayout(track_row)
        slider_layout.setContentsMargins(0, 0, 0, 0)
        slider_layout.setSpacing(Spacing.sm)

        self._calc_slider = ClickSlider()
        self._calc_slider.setRange(5, 180)
        self._calc_slider.setValue(30)
        self._calc_slider.valueChanged.connect(self._update_calculator)
        slider_layout.addWidget(self._calc_slider, 1)

        self._calc_length_label = BodyLabel("30 s")
        self._calc_length_label.setFixedWidth(44)
        self._calc_length_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        slider_layout.addWidget(self._calc_length_label)

        slider_vbox.addWidget(track_row)

        # Legend row: "5 s" flush-left, "3 min" flush with right end of slider track
        legend_row = QWidget()
        legend_layout = QHBoxLayout(legend_row)
        legend_layout.setContentsMargins(0, 0, 0, 0)
        legend_layout.setSpacing(0)

        min_lbl = CaptionLabel("5 s")
        min_lbl.setStyleSheet(f"color: {Colors.text_muted};")
        legend_layout.addWidget(min_lbl)

        legend_layout.addStretch()

        max_lbl = CaptionLabel("3 min")
        max_lbl.setStyleSheet(f"color: {Colors.text_muted};")
        legend_layout.addWidget(max_lbl)

        # Right-pad to align "3 min" with the slider track end, not the value label
        legend_layout.addSpacing(44 + Spacing.sm)

        slider_vbox.addWidget(legend_row)

        fps_calc_card.add_row("Target video length", slider_widget)

        self._calc_result_label = BodyLabel("")
        self._calc_result_label.setWordWrap(True)
        self._calc_result_label.setStyleSheet(f"color: {Colors.text_secondary};")
        fps_calc_card.add_widget(self._calc_result_label)

        self._calc_apply_btn = PrimaryPushButton("Set FPS to …")
        self._calc_apply_btn.setEnabled(False)
        self._calc_apply_btn.clicked.connect(self._apply_calculated_fps)
        fps_calc_card.add_widget(self._calc_apply_btn)

        layout.addWidget(fps_calc_card)

        # === OUTPUT ===
        output_card = CollapsibleCard("Output", mdi('folder-outline'))

        dir_row = QHBoxLayout()
        dir_row.setSpacing(Spacing.sm)
        self._output_dir_input = LineEdit()
        self._output_dir_input.setPlaceholderText("Default: AppData/PFRSentinel/timelapse/")
        self._output_dir_input.textChanged.connect(self._on_settings_changed)
        dir_row.addWidget(self._output_dir_input, 1)
        browse_btn = PushButton("Browse")
        browse_btn.setIcon(mdi('folder-outline'))
        browse_btn.clicked.connect(self._browse_output_dir)
        dir_row.addWidget(browse_btn)
        dir_widget = QWidget()
        dir_widget.setLayout(dir_row)
        output_card.add_row("Output folder", dir_widget)

        self._keep_spin = SpinBox()
        self._keep_spin.setRange(1, 365)
        self._keep_spin.setValue(30)
        self._keep_spin.setSuffix(" days")
        self._keep_spin.valueChanged.connect(self._on_settings_changed)
        output_card.add_row("Keep videos for", self._keep_spin,
                             "Oldest files deleted automatically beyond this limit")

        layout.addWidget(output_card)

        # === YOUTUBE UPLOAD ===
        self._youtube_card = YouTubeUploadCard(self)
        self._youtube_card.settings_changed.connect(self.settings_changed)
        layout.addWidget(self._youtube_card)

        # === STATUS ===
        self._status_card = TimelapseStatusCard()
        layout.addWidget(self._status_card)

    # ------------------------------------------------------------------ #
    #  Event handlers                                                      #
    # ------------------------------------------------------------------ #

    def _on_enable_changed(self, checked: bool):
        if self._loading_config:
            return
        self._save_config()

    def _on_window_mode_changed(self, index: int):
        self._sun_options.setVisible(index == 0)
        self._fixed_options.setVisible(index == 1)
        self._roof_options.setVisible(index == 3)
        if not self._loading_config:
            self._save_config()

    def _on_settings_changed(self, *_):
        if self._loading_config:
            return
        self._save_config()

    def _browse_output_dir(self):
        current = self._output_dir_input.text()
        start_dir = current if current and os.path.isdir(current) else ""
        path = QFileDialog.getExistingDirectory(self, "Select Timelapse Output Folder", start_dir)
        if path:
            self._output_dir_input.setText(path)

    def _update_calculator(self, *_):
        """Recompute playback FPS from session parameters and update the result label."""
        if not hasattr(self, '_calc_slider'):
            return

        session_hours = self._calc_hours_spin.value()
        capture_interval = self._calc_interval_sec
        target_seconds = self._calc_slider.value()

        # Update live slider value label (mm:ss for values ≥ 60)
        if target_seconds >= 60:
            m, s = divmod(target_seconds, 60)
            length_str = f"{m}m {s}s" if s else f"{m}m"
        else:
            length_str = f"{target_seconds}s"
        self._calc_length_label.setText(length_str)

        # Compute required FPS
        total_frames = (session_hours * 3600) / max(1, capture_interval)
        fps_exact = total_frames / max(1, target_seconds)
        fps_int = max(1, min(60, round(fps_exact)))
        self._calc_fps_int = fps_int

        self._calc_result_label.setText(
            f"{session_hours}h · {capture_interval}s interval "
            f"→ {total_frames:.0f} frames ÷ {target_seconds}s "
            f"= {fps_exact:.1f} fps"
        )
        self._calc_apply_btn.setText(f"Set FPS to {fps_int}")
        self._calc_apply_btn.setEnabled(True)

        if not self._loading_config:
            self._save_config()

    def _apply_calculated_fps(self):
        """Apply the calculator's computed FPS to the Playback FPS spinbox."""
        fps_int = getattr(self, '_calc_fps_int', None)
        if fps_int is not None:
            self._fps_spin.setValue(fps_int)   # triggers _on_settings_changed → _save_config

    # ------------------------------------------------------------------ #
    #  Config persistence                                                  #
    # ------------------------------------------------------------------ #

    _SUN_MODE_MAP = {
        0: 'astronomical',
        1: 'nautical',
        2: 'civil',
        3: 'sunset_sunrise',
    }
    _SUN_MODE_REVERSE = {v: k for k, v in _SUN_MODE_MAP.items()}

    _WINDOW_MODE_MAP = {0: 'sun', 1: 'fixed', 2: 'always', 3: 'roof'}
    _WINDOW_MODE_REVERSE = {'sun': 0, 'fixed': 1, 'always': 2, 'roof': 3}

    def _save_config(self):
        if not self.main_window or not hasattr(self.main_window, 'config'):
            return
        tl = self.main_window.config.get('timelapse', {}).copy()
        tl['enabled'] = self._enable_switch.is_checked()
        tl['window_mode'] = self._WINDOW_MODE_MAP.get(self._window_mode_combo.currentIndex(), 'sun')
        tl['sun_mode'] = self._SUN_MODE_MAP.get(self._sun_mode_combo.currentIndex(), 'astronomical')
        tl['fixed_start'] = self._start_time_input.getTime().toString('HH:mm')
        tl['fixed_end'] = self._end_time_input.getTime().toString('HH:mm')
        tl['playback_fps'] = self._fps_spin.value()
        _res_map = {0: 0, 1: 1920, 2: 1440, 3: 1280, 4: 720}
        tl['output_max_dim'] = _res_map.get(self._resolution_combo.currentIndex(), 0)
        _quality_map = {0: 28, 1: 23, 2: 18, 3: 12}
        tl['video_crf'] = _quality_map.get(self._quality_combo.currentIndex(), 23)
        tl['include_overlays'] = self._overlays_switch.is_checked()
        tl['output_dir'] = self._output_dir_input.text()
        tl['max_videos_to_keep'] = self._keep_spin.value()
        tl['calc_session_hours'] = self._calc_hours_spin.value()
        tl['calc_target_seconds'] = self._calc_slider.value()

        # Sun-window coordinates are sourced live from the weather config by
        # timelapse_controller — not stored here. See _get_timelapse_config.

        self.main_window.config.set('timelapse', tl)
        self.main_window.config.save()
        self.settings_changed.emit()
        # Redraw the projected window now rather than on the next 5 s tick.
        self._refresh_status()

    def load_from_config(self, config):
        """Load settings from config object."""
        self._loading_config = True
        try:
            tl = config.get('timelapse', {})

            self._enable_switch.set_checked(tl.get('enabled', False))

            window_idx = self._WINDOW_MODE_REVERSE.get(tl.get('window_mode', 'sun'), 0)
            self._window_mode_combo.setCurrentIndex(window_idx)
            self._sun_options.setVisible(window_idx == 0)
            self._fixed_options.setVisible(window_idx == 1)
            self._roof_options.setVisible(window_idx == 3)

            sun_idx = self._SUN_MODE_REVERSE.get(tl.get('sun_mode', 'astronomical'), 0)
            self._sun_mode_combo.setCurrentIndex(sun_idx)

            def _parse_time(s: str, fallback: QTime) -> QTime:
                try:
                    h, m = map(int, s.split(':'))
                    return QTime(h, m)
                except Exception:
                    return fallback

            self._start_time_input.setTime(_parse_time(tl.get('fixed_start', '18:00'), QTime(18, 0)))
            self._end_time_input.setTime(_parse_time(tl.get('fixed_end', '06:00'), QTime(6, 0)))
            self._fps_spin.setValue(tl.get('playback_fps', 24))
            _res_reverse = {0: 0, 1920: 1, 1440: 2, 1280: 3, 720: 4}
            self._resolution_combo.setCurrentIndex(
                _res_reverse.get(tl.get('output_max_dim', 1920), 1)
            )
            _quality_reverse = {28: 0, 23: 1, 18: 2, 12: 3}
            self._quality_combo.setCurrentIndex(
                _quality_reverse.get(tl.get('video_crf', 23), 1)
            )
            self._overlays_switch.set_checked(tl.get('include_overlays', False))
            self._output_dir_input.setText(tl.get('output_dir', ''))
            self._keep_spin.setValue(tl.get('max_videos_to_keep', 30))
            self._calc_hours_spin.setValue(tl.get('calc_session_hours', 6))
            if hasattr(self, '_youtube_card'):
                self._youtube_card.load_from_config(config.get('youtube', {}))
            zwo_interval = config.get('zwo_interval', 300.0)
            self._calc_interval_sec = max(1, int(round(zwo_interval)))
            self._calc_interval_label.setText(f"{self._calc_interval_sec} s")
            self._calc_slider.setValue(tl.get('calc_target_seconds', 30))
        finally:
            self._loading_config = False

        # Refresh calculator display to match the loaded values
        self._update_calculator()

        # Show watch mode notice if applicable
        capture_mode = config.get('capture_mode', 'camera')
        self._watch_mode_notice.setVisible(capture_mode == 'watch')

    # ------------------------------------------------------------------ #
    #  Status display                                                      #
    # ------------------------------------------------------------------ #

    def update_status(self, status: dict):
        """Called by TimelapseController.status_updated signal."""
        if hasattr(self, '_status_card'):
            self._status_card.update_status(status)

    def _refresh_status(self):
        """Pull status from controller if available."""
        if self.main_window and hasattr(self.main_window, 'timelapse_controller'):
            self.update_status(self.main_window.timelapse_controller.get_display_status())
