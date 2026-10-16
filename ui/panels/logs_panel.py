"""
Logs Panel
Full log viewer with filtering
"""
import html
from collections import deque

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QScrollArea, QFrame,
    QTextEdit, QSizePolicy
)
from PySide6.QtCore import Qt, Signal, QTimer
from qfluentwidgets import (
    CardWidget, SubtitleLabel, BodyLabel, CaptionLabel,
    PushButton, ComboBox, LineEdit, SwitchButton,
    InfoBar, InfoBarPosition
)

from services.log_line_level import (
    DEFAULT_LEVEL, LEVELS, at_or_above, line_level, normalise_threshold,
)

from ..theme.tokens import Colors, Typography, Spacing, Layout
from ..theme.icons import mdi
from ..components.cards import SettingsCard, SwitchRow


class LogsPanel(QScrollArea):
    """
    Full log viewer panel with:
    - Log text area
    - Filter by level
    - Search
    - Auto-scroll toggle
    - Clear button
    - Export Diagnostics (bundle built by DiagnosticsController)
    """

    export_diagnostics_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.main_window = parent
        self._max_lines = 1000
        # Raw lines kept apart from the document so a new search or level can
        # re-filter history. Deeper than the view: a rare keyword should still
        # find lines that DEBUG chatter has pushed off the visible 1000.
        self._history = deque(maxlen=10000)
        self._match_count = 0
        self._auto_scroll = True
        self._refilter_timer = QTimer(self)
        self._refilter_timer.setSingleShot(True)
        self._refilter_timer.setInterval(200)
        self._refilter_timer.timeout.connect(self._rerender)
        self._setup_ui()
    
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
        
        # === CONTROLS ===
        controls_card = CardWidget()
        controls_layout = QHBoxLayout(controls_card)
        controls_layout.setContentsMargins(Spacing.card_padding, Spacing.md,
                                          Spacing.card_padding, Spacing.md)
        controls_layout.setSpacing(Spacing.md)
        
        # Filter by level
        filter_label = BodyLabel("Level:")
        filter_label.setStyleSheet(f"color: {Colors.text_secondary};")
        controls_layout.addWidget(filter_label)
        
        self.level_filter = ComboBox()
        self.level_filter.addItems(list(LEVELS))
        self.level_filter.setCurrentText(DEFAULT_LEVEL)
        self.level_filter.setToolTip(
            "Shows this level and everything more severe: DEBUG shows all, "
            "INFO hides DEBUG, WARN shows warnings and errors, ERROR only errors"
        )
        self.level_filter.currentTextChanged.connect(self._on_filter_changed)
        self.level_filter.setFixedWidth(100)
        controls_layout.addWidget(self.level_filter)
        
        # Search
        self.search_input = LineEdit()
        self.search_input.setPlaceholderText("Search logs...")
        self.search_input.setClearButtonEnabled(True)
        self.search_input.setToolTip(
            "Shows only lines containing this text (not case-sensitive), "
            "both past lines and new ones as they arrive"
        )
        self.search_input.textChanged.connect(self._on_search_changed)
        self.search_input.setMinimumWidth(100)
        self.search_input.setMaximumWidth(250)
        controls_layout.addWidget(self.search_input)

        self.match_label = CaptionLabel("")
        self.match_label.setStyleSheet(f"color: {Colors.text_muted};")
        controls_layout.addWidget(self.match_label)

        controls_layout.addStretch()
        
        # Auto-scroll
        self.auto_scroll_switch = SwitchButton()
        self.auto_scroll_switch.setChecked(True)
        self.auto_scroll_switch.checkedChanged.connect(self._on_auto_scroll_changed)
        controls_layout.addWidget(BodyLabel("Auto-scroll"))
        controls_layout.addWidget(self.auto_scroll_switch)
        
        # Clear button
        self.clear_btn = PushButton("Clear")
        self.clear_btn.setIcon(mdi('delete-outline'))
        self.clear_btn.clicked.connect(self._clear_logs)
        controls_layout.addWidget(self.clear_btn)
        
        # Open log folder
        self.open_folder_btn = PushButton("Open Folder")
        self.open_folder_btn.setIcon(mdi('folder-outline'))
        self.open_folder_btn.clicked.connect(self._open_log_folder)
        controls_layout.addWidget(self.open_folder_btn)

        self.export_diag_btn = PushButton("Export Diagnostics")
        self.export_diag_btn.setIcon(mdi('folder-zip-outline'))
        self.export_diag_btn.setToolTip(
            "Zip recent logs, a redacted config, the all-sky calibration and a raw "
            "frame (captured fresh when the camera is running) to attach to a bug report"
        )
        self.export_diag_btn.clicked.connect(self._on_export_diagnostics)
        controls_layout.addWidget(self.export_diag_btn)

        layout.addWidget(controls_card)

        self.diag_status = CaptionLabel("")
        self.diag_status.setStyleSheet(f"color: {Colors.text_muted};")
        self.diag_status.setWordWrap(True)
        self.diag_status.hide()
        layout.addWidget(self.diag_status)
        
        # === LOG TEXT AREA ===
        log_card = CardWidget()
        log_layout = QVBoxLayout(log_card)
        log_layout.setContentsMargins(Spacing.card_padding, Spacing.card_padding,
                                      Spacing.card_padding, Spacing.card_padding)
        log_layout.setSpacing(0)
        
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setLineWrapMode(QTextEdit.NoWrap)  # Allow horizontal scroll
        # A manual cursor trim frees nothing while undo is on: QTextDocument
        # keeps every insert *and* every removal on the undo stack, so an
        # all-night run grows without bound. maximumBlockCount discards the
        # oldest block in-place instead.
        self.log_text.setUndoRedoEnabled(False)
        self.log_text.document().setMaximumBlockCount(self._max_lines)
        self.log_text.setStyleSheet(f"""
            QTextEdit {{
                background-color: {Colors.bg_input};
                border: 1px solid {Colors.border_subtle};
                border-radius: {Layout.radius_md}px;
                color: {Colors.text_secondary};
                font-family: {Typography.family_mono};
                font-size: {Typography.size_small}px;
                padding: 8px;
            }}
        """)
        log_layout.addWidget(self.log_text)
        
        # Log location info
        from services.logger import app_logger
        log_path = app_logger.get_log_location()
        log_info = CaptionLabel(f"Log file: {log_path}")
        log_info.setStyleSheet(f"color: {Colors.text_muted}; padding-top: 8px;")
        log_layout.addWidget(log_info)
        
        layout.addWidget(log_card, 1)
    
    def load_from_config(self, config):
        saved = normalise_threshold(config.get('ui_log_level', DEFAULT_LEVEL))
        self.level_filter.setCurrentIndex(self.level_filter.findText(saved))

    def _on_filter_changed(self, level):
        self._rerender()
        if self.main_window and hasattr(self.main_window, 'config'):
            self.main_window.config.set('ui_log_level', level)
            if hasattr(self.main_window, 'save_config'):
                self.main_window.save_config()
            else:
                self.main_window.config.save()
    
    def _on_search_changed(self, text):
        # Debounced: re-filtering 10k lines on every keystroke stutters.
        self._refilter_timer.start()
    
    def _on_auto_scroll_changed(self, checked):
        """Handle auto-scroll toggle"""
        self._auto_scroll = checked
        if checked:
            self._scroll_to_bottom()
    
    def _clear_logs(self):
        """Clear log display"""
        self._history.clear()
        self.log_text.clear()
        self._update_match_label()
    
    def _open_log_folder(self):
        """Open log folder in the OS file manager"""
        from services.logger import app_logger
        from services.reveal_in_file_manager import reveal_path

        reveal_path(app_logger.get_log_dir())
    
    # === DIAGNOSTICS EXPORT ===

    def _on_export_diagnostics(self):
        self.export_diag_btn.setEnabled(False)
        self.export_diag_btn.setText("Exporting…")
        self.diag_status.setText("Preparing diagnostics bundle…")
        self.diag_status.show()
        self.export_diagnostics_requested.emit()

    def on_diagnostics_progress(self, message: str):
        self.diag_status.setText(message)
        self.diag_status.show()

    def on_diagnostics_ready(self, path: str):
        self._reset_export_button()
        self.diag_status.setText(f"Diagnostics bundle saved: {path}")
        self._info_bar(InfoBar.success, "Diagnostics bundle ready",
                       "Secrets, location and URLs are redacted; file paths are not. "
                       "Check the ZIP, then attach it to your GitHub issue.")

    def on_diagnostics_failed(self, message: str):
        self._reset_export_button()
        self.diag_status.setText(f"Diagnostics export failed: {message}")
        self._info_bar(InfoBar.error, "Diagnostics export failed", message)

    def _reset_export_button(self):
        self.export_diag_btn.setEnabled(True)
        self.export_diag_btn.setText("Export Diagnostics")

    def _info_bar(self, factory, title, content):
        parent = getattr(self.main_window, 'content_area', self.main_window) if self.main_window else self
        bar = factory(title=title, content=content, parent=parent,
                      position=InfoBarPosition.TOP, duration=6000)
        bar.raise_()

    def _scroll_to_bottom(self):
        scrollbar = self.log_text.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def _passes(self, message: str, level: str, needle: str) -> bool:
        if not at_or_above(message, level):
            return False
        return not needle or needle in message.lower()

    def _append_one(self, message: str):
        """Render one message into the document. No scrolling — see append_logs."""
        color = {
            "ERROR": Colors.error_text,
            "WARN": Colors.warning_text,
            "DEBUG": Colors.text_muted,
        }.get(line_level(message), Colors.text_secondary)
        # Escaped: a line holding "<lambda>" or a URL with "<id>" vanished as a tag.
        self.log_text.append(f'<span style="color: {color};">{html.escape(message)}</span>')

    def _rerender(self):
        """Rebuild the view from history under the current level and search."""
        self._refilter_timer.stop()
        level = self.level_filter.currentText()
        needle = self.search_input.text().strip().lower()
        matches = [m for m in self._history if self._passes(m, level, needle)]
        self._match_count = len(matches)
        self.log_text.setUpdatesEnabled(False)
        try:
            self.log_text.clear()
            for msg in matches[-self._max_lines:]:
                self._append_one(msg)
        finally:
            self.log_text.setUpdatesEnabled(True)
        self._update_match_label()
        if self._auto_scroll:
            self._scroll_to_bottom()

    def _update_match_label(self):
        needle = self.search_input.text().strip()
        if not needle:
            self.match_label.setText("")
            self.log_text.setPlaceholderText("")
            return
        count = self._match_count
        self.match_label.setText(f"{count} match" + ("" if count == 1 else "es"))
        self.log_text.setPlaceholderText(
            f"No lines containing \"{needle}\" yet. New ones will appear here as they're logged."
        )

    def append_log(self, message: str):
        """Append a single log message"""
        self.append_logs([message])

    def append_logs(self, messages: list):
        """Append a batch of log messages with one repaint and one scroll."""
        if not messages:
            return
        self._history.extend(messages)
        level = self.level_filter.currentText()
        needle = self.search_input.text().strip().lower()
        # The switch is the only gate: a resize leaves the scrollbar short of
        # its new maximum, so an "already at bottom" check would silently stop
        # following after any window/splitter change.
        follow = self._auto_scroll
        added = 0
        self.log_text.setUpdatesEnabled(False)
        try:
            for msg in messages:
                if self._passes(msg, level, needle):
                    self._append_one(msg)
                    added += 1
        finally:
            self.log_text.setUpdatesEnabled(True)
        if added and needle:
            self._match_count = self._match_count + added
            self._update_match_label()
        if follow:
            self._scroll_to_bottom()
