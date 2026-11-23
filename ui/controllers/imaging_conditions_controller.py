"""
Imaging Conditions controller — keeps the card (NINA & ASCOM page), the status
strip's IMAGING tile and Live Monitoring's conditions panel in step with the
conditions store, its history and the NINA check-ins, and saves the card's
edits.

The verdict itself is computed on the image worker thread
(``services/imaging_conditions/feed.py``); this only reads the thread-safe
store from a GUI-thread timer, so no widget is touched off the GUI thread.
"""
from PySide6.QtCore import QObject, QTimer

from datetime import datetime, timezone

from services.imaging_conditions import CONFIG_KEY, get_conditions_store, read_settings
from services.imaging_conditions.conditions_history import get_conditions_history
from services.imaging_conditions.nina_checkin import get_nina_checkins
from services.logger import app_logger

REFRESH_MS = 2000


class ImagingConditionsController(QObject):
    def __init__(self, main_window, store=None, parent=None, capturing=None,
                 history=None, checkins=None):
        super().__init__(parent or main_window)
        self._main_window = main_window
        self._store = store
        self._history = history
        self._checkins = checkins
        self._live = None
        self._capturing = capturing or (
            lambda: bool(getattr(self._main_window, 'is_capturing', False)))
        self._card = None
        self._strip = None
        self._timer = QTimer(self)
        self._timer.setInterval(REFRESH_MS)
        self._timer.timeout.connect(self.refresh)

    @property
    def store(self):
        return self._store if self._store is not None else get_conditions_store()

    @property
    def history(self):
        return self._history if self._history is not None else get_conditions_history()

    @property
    def checkins(self):
        return self._checkins if self._checkins is not None else get_nina_checkins()

    def bind(self, card, strip=None, live_panel=None) -> None:
        self._card = card
        self._strip = strip
        self._live = live_panel
        card.changed.connect(self._on_card_changed)
        if hasattr(card, 'reset_baseline_requested'):
            card.reset_baseline_requested.connect(self.reset_baseline)
        config = getattr(self._main_window, 'config', None)
        if config is not None:
            card.load(config.get(CONFIG_KEY, {}) or {})
            self.store.set_enabled(read_settings(config).enabled)
        for name in ('capture_started', 'capture_stopped'):
            signal = getattr(self._main_window, name, None)
            if hasattr(signal, 'connect'):
                signal.connect(self.refresh)
        self.refresh()
        self._timer.start()

    def refresh(self) -> None:
        try:
            snapshot = self.store.snapshot()
        except Exception as e:
            app_logger.debug(f"Imaging conditions refresh failed: {e}")
            return
        capturing = self._capturing()
        if self._card is not None:
            self._card.show_conditions(snapshot, capturing)
        if self._strip is not None and hasattr(self._strip, 'set_imaging'):
            from ..components.imaging_conditions_text import summarize_conditions
            self._strip.set_imaging(*summarize_conditions(snapshot, capturing))
        if self._live is not None and self._live.isVisible():
            self._refresh_live(snapshot, capturing)

    def _refresh_live(self, snapshot, capturing) -> None:
        try:
            config = getattr(self._main_window, 'config', None)
            good_needed = read_settings(config).good_frames if config is not None else None
            self._live.set_view(
                snapshot, capturing=capturing, good_needed=good_needed,
                samples=self.history.samples(), events=self.history.events(),
                checkins=self.checkins.last(), now=datetime.now(timezone.utc))
        except Exception as e:
            app_logger.debug(f"Imaging conditions live view refresh failed: {e}")

    def reset_baseline(self) -> None:
        """Forget the learned clear-sky star counts; learning starts again
        from this rig's next clear frames."""
        from services.imaging_conditions import get_imaging_conditions_feed
        get_imaging_conditions_feed().baseline.reset()
        app_logger.info("Imaging conditions: clear-sky star baseline reset")
        self.refresh()

    def _on_card_changed(self) -> None:
        config = getattr(self._main_window, 'config', None)
        if config is None or self._card is None:
            return
        block = dict(config.get(CONFIG_KEY, {}) or {})
        block.update(self._card.values())
        config.set(CONFIG_KEY, block)
        config.save()
        self.store.set_enabled(bool(block.get('enabled', True)))
        app_logger.debug(f"Imaging conditions settings saved: {self._card.values()}")
        self.refresh()
