"""
Per-frame entry point: judge the frame, advance the streaks, publish.

Called once per processed frame from both pipelines, right after the
frame's star detection: ``ui/controllers/image_processor.py`` (ZWO and
Alpaca camera captures, and Watch-mode reprocesses, which are skipped) and
``services/processor.py`` (Directory Watch). Everything it reads is already
on the frame's metadata — ML results, ``sky_evidence``'s star count, the
``{FWHM}`` measurement — so the cost is bookkeeping, not image work
(test_imaging_conditions_feed's budget test).

State that spans frames lives here: the recent star counts the trend is
taken against, the night's FWHM readings, the frame interval the automatic
staleness is derived from, the streaks and the learned baseline (a store of
clear nights, ``star_baseline``, which also keeps the FWHM across nights). The
process-wide feed also keeps that state across an app restart
(``session_state``), so a restart mid-night carries on where it stopped.
"""
from __future__ import annotations

import statistics
import threading
import time
from collections import deque
from datetime import datetime, timezone
from typing import Callable, Optional

from services.logger import app_logger

from . import frame_rules
from .conditions_history import ConditionsHistory, get_conditions_history
from .conditions_store import ConditionsStore, STALE, WARMING_UP, get_conditions_store
from .frame_signals import read_frame_signals
from .plate_scale import PlateScale
from .session_state import SessionState
from .settings import auto_stale_after_s, read_settings
from .star_baseline import StarBaseline, moon_bin, night_key
from .verdict_hysteresis import VerdictHysteresis

RECOVERING = 'recovering'

MOON_CACHE_S = 300.0
_HISTORY_MAX = 60
_FWHM_HISTORY_MAX = 600
_INTERVALS_MAX = 10


def _default_sun_alt(config):
    from services.observing_window import sun_elevation
    return sun_elevation(config)


class _CachedMoon:
    """``moon.compute_moon_context`` re-reads config.json and runs astral;
    the Moon moves slowly, so five minutes is fresh enough."""

    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self._at = None
        self._value = {}

    def __call__(self):
        now = self._clock()
        if self._at is None or now - self._at > MOON_CACHE_S:
            try:
                from services.moon import compute_moon_context
                self._value = compute_moon_context() or {}
            except Exception as e:
                app_logger.debug(f"Imaging conditions: moon context failed: {e}")
                self._value = {}
            self._at = now
        return self._value


def _round(value, digits):
    return None if value is None else round(float(value), digits)


class ImagingConditionsFeed:
    def __init__(self, store: Optional[ConditionsStore] = None,
                 baseline: Optional[StarBaseline] = None,
                 plate_scale: Optional[PlateScale] = None,
                 history: Optional[ConditionsHistory] = None,
                 session_state: Optional[SessionState] = None,
                 sun_alt_fn: Callable = _default_sun_alt,
                 moon_fn: Optional[Callable[[], dict]] = None,
                 clock: Callable[[], float] = time.monotonic,
                 wall: Callable[[], datetime] = lambda: datetime.now(timezone.utc)):
        self._store = store
        self._history = history
        self._baseline = baseline if baseline is not None else StarBaseline()
        self._plate_scale = plate_scale if plate_scale is not None else PlateScale()
        self._sun_alt_fn = sun_alt_fn
        self._moon_fn = moon_fn if moon_fn is not None else _CachedMoon()
        self._clock = clock
        self._wall = wall
        self._lock = threading.Lock()
        self._verdict = VerdictHysteresis()
        self._counts = deque(maxlen=_HISTORY_MAX)
        self._fwhm = deque(maxlen=_FWHM_HISTORY_MAX)
        self._intervals = deque(maxlen=_INTERVALS_MAX)
        self._last_mono: Optional[float] = None
        self._night: Optional[str] = None
        self._session = session_state
        self._restore_pending = session_state is not None

    @property
    def store(self) -> ConditionsStore:
        return self._store if self._store is not None else get_conditions_store()

    @property
    def history(self) -> ConditionsHistory:
        return self._history if self._history is not None else get_conditions_history()

    @property
    def baseline(self) -> StarBaseline:
        return self._baseline

    def stale_after_s(self, settings) -> float:
        if settings.stale_after_s > 0:
            return settings.stale_after_s
        interval = statistics.median(self._intervals) if self._intervals else None
        return auto_stale_after_s(interval)

    def observe(self, metadata: dict, config, frame_size=None) -> Optional[dict]:
        """Judge one frame. ``frame_size`` = (width, height) of the frame the
        FWHM was measured on. Returns the published record, or None."""
        settings = read_settings(config)
        store = self.store
        if not settings.enabled:
            store.set_enabled(False)
            return None
        from services.observing_window import SAME_CAPTURE_KEY
        if metadata.get(SAME_CAPTURE_KEY):
            return None
        with self._lock:
            return self._observe_locked(metadata, config, settings, frame_size, store)

    def _observe_locked(self, metadata, config, settings, frame_size, store):
        now_mono = self._clock()
        now = self._wall()
        if self._restore_pending:
            self._restore_locked(now, settings)
        stale_after = self.stale_after_s(settings)
        gap_limit = stale_after
        if self._last_mono is not None:
            gap = now_mono - self._last_mono
            if gap > stale_after:
                app_logger.info(f"Imaging conditions: frames resumed after {gap:.0f} s; "
                                "starting over")
                self._verdict.reset(now)
                self._counts.clear()
            else:
                self._intervals.append(gap)
                stale_after = self.stale_after_s(settings)
        self._last_mono = now_mono

        signals = read_frame_signals(metadata)
        self._check_stamp(metadata, frame_size)
        sun_alt = self._sun_alt_fn(config)
        moon = self._moon_fn() or {}
        bin_name = moon_bin(moon.get('moon_is_up'), moon.get('illumination_pct'))
        night = night_key(now)
        if self._night is not None and night != self._night:
            # Capture off through the day sees no daylight frame to clear
            # these; last night's seeing is not "tonight's median".
            self._fwhm.clear()
        self._night = night
        min_samples = settings.baseline_min_samples
        baseline = self._baseline.baseline(bin_name, min_samples)

        night_fwhm = frame_rules.night_median(self._fwhm)
        ev = frame_rules.evaluate_frame(
            signals, settings, sun_alt_deg=sun_alt, baseline=baseline,
            recent_counts=list(self._counts), night_fwhm_median=night_fwhm)
        self._remember(signals, ev)

        if frame_rules.is_baseline_frame(signals, settings, sun_alt):
            self._baseline.add(bin_name, signals.star_count, night, min_samples)
            if ev.fwhm_px is not None:
                self._baseline.add_fwhm(ev.fwhm_px, night, min_samples)
        baseline_status = self._baseline.status(bin_name, min_samples, night)
        fwhm_reference = self._baseline.fwhm_reference(min_samples)
        self._baseline.maybe_save()

        was_ok = self._verdict.state()['imaging_ok']
        changed = self._verdict.observe(ev.ok, bad_frames=settings.bad_frames,
                                        good_frames=settings.good_frames, now=now)
        state = self._verdict.state()
        record = self._record(signals, ev, state, sun_alt, moon, bin_name, baseline,
                              night_fwhm, frame_size, settings)
        record['baseline_status'] = baseline_status
        # Reported only: fwhm_high is still judged against tonight's median.
        record['fwhm']['reference_px'] = _round(fwhm_reference[0], 2)
        record['fwhm']['reference_nights'] = fwhm_reference[1]
        store.publish(record, updated_at=now, stale_after_s=stale_after)
        self.history.record(record, at=now, stale_after_s=gap_limit)
        if self._session is not None:
            self._session.save(at=now, verdict=self._verdict.export(),
                               counts=self._counts, intervals=self._intervals,
                               fwhm=[round(v, 2) for v in self._fwhm], force=changed)
        if changed:
            self._log_transition(state['imaging_ok'], ev.reasons)
        elif was_ok and not ev.ok:
            app_logger.debug(f"Imaging conditions: bad frame {state['bad_streak']}/"
                             f"{settings.bad_frames} ({', '.join(ev.reasons)})")
        return record

    def _restore_locked(self, now, settings) -> None:
        """Adopt the state a previous run saved, if it is younger than the
        gap after which this run would have started over anyway."""
        self._restore_pending = False
        saved = self._session.load(now=now, counts_max=_HISTORY_MAX,
                                   fwhm_max=_FWHM_HISTORY_MAX,
                                   intervals_max=_INTERVALS_MAX)
        if saved is None:
            return
        limit = settings.stale_after_s
        if limit <= 0:
            intervals = saved['intervals']
            limit = auto_stale_after_s(statistics.median(intervals) if intervals else None)
        if saved['age_s'] > limit or not self._verdict.restore(saved['verdict']):
            return
        self._counts.extend(saved['counts'])
        self._fwhm.extend(saved['fwhm'])
        self._intervals.extend(saved['intervals'])
        app_logger.info(f"Imaging conditions: carrying on from the state saved "
                        f"{saved['age_s']:.0f} s ago")

    def _check_stamp(self, metadata, frame_size) -> None:
        """Start the baseline over on another camera, frame size or lens."""
        if not frame_size:
            return
        w, h = int(frame_size[0]), int(frame_size[1])
        camera = metadata.get('CAMERA')
        lens = self._plate_scale.lens_a1(w, h)
        if self._baseline.ensure_stamp({'camera': camera if isinstance(camera, str) else '',
                                        'width': w, 'height': h,
                                        'lens_a1': None if lens is None else round(lens, 2)}):
            self._counts.clear()

    def _remember(self, signals, ev) -> None:
        if frame_rules.DAYLIGHT in ev.reasons:
            # A new night: last night's seeing and star counts say nothing.
            self._counts.clear()
            self._fwhm.clear()
            return
        if frame_rules.ROOF_NOT_OPEN in ev.reasons or frame_rules.FRAME_STATIC in ev.reasons:
            return
        if signals.star_count is not None:
            self._counts.append(signals.star_count)
        if ev.fwhm_px is not None and set(ev.reasons) <= {frame_rules.FWHM_HIGH}:
            self._fwhm.append(ev.fwhm_px)

    def _record(self, signals, ev, state, sun_alt, moon, bin_name, baseline,
                night_fwhm, frame_size, settings) -> dict:
        if state['imaging_ok'] or ev.reasons:
            reasons = list(ev.reasons)
        else:
            reasons = [RECOVERING if state['ever_ok'] or state['bad_seen'] else WARMING_UP]
        arcsec = None
        if ev.fwhm_px is not None and frame_size:
            scale = self._plate_scale.arcsec_per_px(frame_size[0], frame_size[1])
            if scale is not None:
                arcsec = round(ev.fwhm_px * scale, 1)
        illum = moon.get('illumination_pct')
        return {
            'imaging_ok': state['imaging_ok'],
            'reasons': reasons,
            'frame_ok': ev.ok,
            'changed_at': state['changed_at'],
            'frames_considered': state['frames'],
            'bad_streak': state['bad_streak'],
            'good_streak': state['good_streak'],
            'roof': {'state': signals.roof_state,
                     'confidence': _round(signals.roof_confidence, 3)},
            'sky': {'condition': signals.sky_condition,
                    'confidence': _round(signals.sky_confidence, 3),
                    'stars_visible': signals.stars_visible},
            'stars': {'count': signals.star_count, 'baseline': _round(baseline, 1),
                      'ratio': ev.ratio, 'trend': ev.trend, 'change_pct': ev.change_pct,
                      'window_frames': settings.trend_frames},
            'cloud_cover_pct': ev.cloud_cover_pct,
            'cloud_cover_source': ev.cloud_cover_source,
            'fwhm': {'px': _round(ev.fwhm_px, 2), 'arcsec': arcsec,
                     'night_median_px': _round(night_fwhm, 2),
                     'change_pct': ev.fwhm_change_pct},
            'sun_altitude_deg': _round(sun_alt, 1),
            'moon': {'up': moon.get('moon_is_up') if isinstance(moon.get('moon_is_up'), bool) else None,
                     'illumination_pct': _round(illum, 1) if isinstance(illum, (int, float)) else None},
            'baseline_bin': bin_name,
        }

    @staticmethod
    def _log_transition(ok: bool, reasons) -> None:
        if ok:
            app_logger.info("Imaging conditions: OK for imaging")
        else:
            app_logger.info(f"Imaging conditions: NOT OK ({', '.join(reasons) or STALE})")

    def flush(self) -> bool:
        """Write the learned nights now (capture stop, shutdown), so the
        night in progress survives whatever comes next."""
        return self._baseline.save_now()

    def reset(self) -> None:
        with self._lock:
            self._verdict.reset()
            self._counts.clear()
            self._fwhm.clear()
            self._intervals.clear()
            self._last_mono = None


_feed: Optional[ImagingConditionsFeed] = None
_feed_lock = threading.Lock()


def get_imaging_conditions_feed() -> ImagingConditionsFeed:
    global _feed
    with _feed_lock:
        if _feed is None:
            _feed = ImagingConditionsFeed(session_state=SessionState())
        return _feed


def reset_imaging_conditions_feed() -> None:
    """Drop the singleton (tests)."""
    global _feed
    with _feed_lock:
        _feed = None


def flush_imaging_conditions() -> None:
    """Save the learned nights at capture stop and shutdown. Never raises,
    and does nothing when no frame was ever judged."""
    with _feed_lock:
        feed = _feed
    if feed is None:
        return
    try:
        feed.flush()
    except Exception as e:
        app_logger.debug(f"Imaging conditions save skipped: {e}")


def observe_imaging_conditions(metadata: dict, config, frame_size=None) -> None:
    """The pipelines' one-line hook. Never raises: conditions are advisory to
    the capture path and must not cost it a frame."""
    try:
        get_imaging_conditions_feed().observe(metadata, config, frame_size)
    except Exception as e:
        app_logger.debug(f"Imaging conditions update skipped: {e}")
