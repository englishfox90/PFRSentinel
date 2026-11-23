"""Main-window navigation: the splitter, the nav rail's sections, and the
NINA & ASCOM page with the links into it (the IMAGING tile, the pointers left
on Output and Image Processing, the conditions panel's Settings link).

Moved out of ``window.py`` to keep it under the size cap.
"""
from services.logger import app_logger

from ..controllers.imaging_conditions_controller import ImagingConditionsController
from ..controllers.nina_ascom_controller import NinaAscomController
from ..panels.nina_ascom_panel import NinaAscomPanel

NINA_SECTION = 'nina'
CONDITIONS_OPEN_KEY = 'live_conditions_panel_open'


class _MainWindowNavigationMixin:

    # =========================================================================
    # NINA & ASCOM PAGE
    # =========================================================================

    def _build_nina_page(self):
        self.nina_panel = NinaAscomPanel(self)
        self.inspector_stack.addWidget(self.nina_panel)
        self.live_panel.set_conditions_open(bool(self.config.get(CONDITIONS_OPEN_KEY, True)))

    def _wire_nina_page(self):
        self.nina_ascom_controller = NinaAscomController(self)
        self.nina_ascom_controller.bind(self.nina_panel)
        self.imaging_conditions_controller = ImagingConditionsController(self)
        self.imaging_conditions_controller.bind(
            self.nina_panel.conditions_card, self.status_strip,
            live_panel=self.live_panel.conditions_panel)
        self.nina_panel.conditions_card.live_view_requested.connect(self.open_conditions_view)
        self.live_panel.conditions_panel.settings_requested.connect(self.open_nina_page)
        self.live_panel.conditions_toggled.connect(self._on_conditions_toggled)
        self.status_strip.imaging_clicked.connect(self.open_conditions_view)
        self.output_panel.open_nina_requested.connect(self.open_nina_page)
        self.processing_panel.ml_section.open_nina_requested.connect(self.open_nina_page)

    def open_conditions_view(self):
        """Live Monitoring with the conditions panel open (the IMAGING tile)."""
        self.live_panel.set_conditions_open(True, emit=True)
        self.go_to_section('monitoring')
        self.imaging_conditions_controller.refresh()

    def open_nina_page(self):
        self.go_to_section(NINA_SECTION)

    def go_to_section(self, section: str):
        if self._current_section_key() == section:
            self._on_nav_changed(section)
        else:
            self.nav_rail.set_section(section)

    def _current_section_key(self) -> str:
        return getattr(self.nav_rail, '_current_section', '')

    def _on_conditions_toggled(self, open_: bool):
        self.config.set(CONDITIONS_OPEN_KEY, bool(open_))
        self.save_config()

    # =========================================================================
    # SPLITTER + SECTIONS
    # =========================================================================

    def _on_splitter_moved(self, pos, index):
        sizes = self.splitter.sizes()
        total = sum(sizes) if sizes else 1

        if total > 0 and len(sizes) >= 2:
            ratio = sizes[0] / total
            self.config.set('splitter_sizes', sizes)
            self.config.set('splitter_ratio', ratio)

    def _restore_splitter_sizes(self):
        """Restore splitter sizes after window is shown

        Uses saved ratio as primary method, falls back to absolute sizes.
        This prevents dramatic shifts when window size differs from saved state.
        """
        try:
            available_width = self.splitter.width()
            if available_width <= 0:
                self.splitter.setSizes([400, 500])
                return

            saved_ratio = self.config.get('splitter_ratio', None)
            if saved_ratio is not None and 0.1 <= saved_ratio <= 0.9:
                left_size = int(available_width * saved_ratio)
                right_size = available_width - left_size

                left_size = max(250, left_size)
                right_size = max(300, right_size)

                self.splitter.setSizes([left_size, right_size])
                app_logger.debug(f"Splitter restored via ratio: {saved_ratio:.2f} -> [{left_size}, {right_size}]")
                return

            saved_sizes = self.config.get('splitter_sizes', None)
            if saved_sizes and isinstance(saved_sizes, list) and len(saved_sizes) >= 2:
                if all(s >= 100 for s in saved_sizes[:2]):
                    total_saved = sum(saved_sizes[:2])
                    if total_saved > available_width * 1.5 or total_saved < available_width * 0.5:
                        ratio = saved_sizes[0] / total_saved
                        left_size = int(available_width * ratio)
                        right_size = available_width - left_size
                        self.splitter.setSizes([max(250, left_size), max(300, right_size)])
                    else:
                        self.splitter.setSizes(saved_sizes)
                    return

            left_size = int(available_width * 0.4)
            right_size = available_width - left_size
            self.splitter.setSizes([left_size, right_size])

        except Exception as e:
            app_logger.warning(f"Error restoring splitter sizes: {e}")
            self.splitter.setSizes([400, 500])

    # =========================================================================
    # NAVIGATION
    # =========================================================================

    def _on_nav_changed(self, section: str):
        section_map = {
            'monitoring': -1,  # Special: hide inspector panel, show live panel
            'capture': 0,
            'output': 1,
            'processing': 2,
            'overlays': 3,
            'timelapse': 4,
            'allsky': 5,
            'meteor': 6,
            'logs': 7,
            'settings': 8,
            'library': 9,
            'nina': 10,
        }

        index = section_map.get(section, 0)

        if section == 'settings':
            self.nav_rail.set_badge('settings', False)

        if index == -1:
            self.live_panel.show()
            self.live_panel.set_preview_only(True)
            self.inspector_stack.hide()
            self.config.set('inspector_visible', False)
            self.config.set('last_nav_section', section)
            self.config.save()
        elif section in ('overlays', 'settings', 'library'):
            # Overlays/Settings/Library: hide live panel, show panel full width
            self.live_panel.hide()
            self.live_panel.set_preview_only(False)
            self.inspector_stack.show()
            self.inspector_stack.setCurrentIndex(index)
            self.config.set('inspector_visible', True)
            self.config.set('last_nav_section', section)
            self.config.save()
        else:
            self.live_panel.show()
            self.live_panel.set_preview_only(False)
            self.inspector_stack.show()
            self.inspector_stack.setCurrentIndex(index)
            self.config.set('inspector_visible', True)
            self.config.set('last_nav_section', section)
            self.config.save()

        app_logger.debug(f"Navigation: {section}")

        from services.posthog_service import capture_event
        capture_event('$pageview', {'$current_url': f'/app/{section}'})
