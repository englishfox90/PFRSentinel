"""Tests for services.allsky.frame_ring — the long-baseline, same-night ring."""
from datetime import datetime, timedelta, timezone

from services.allsky.frame_ring import (
    LONG_RING_LEN, LONG_RING_SPACING_MIN, NIGHT_GAP_HOURS, FrameRing)

T0 = datetime(2026, 9, 18, 2, 0, tzinfo=timezone.utc)


def frame(minutes: float) -> dict:
    return {'dt': T0 + timedelta(minutes=minutes), 'detected': [(1.0, 2.0, 3.0)]}


class TestSpacing:
    def test_first_frame_is_kept(self):
        ring = FrameRing()
        assert ring.offer(frame(0)) is True
        assert len(ring) == 1

    def test_one_frame_per_spacing(self):
        ring = FrameRing()
        kept = [ring.offer(frame(m)) for m in range(0, 31)]   # 30 s cadence would be denser
        assert kept[0] and kept[10] and kept[20] and kept[30]
        assert not any(kept[1:10]) and not any(kept[11:20])
        assert len(ring) == 4
        assert ring.span_minutes() == 30.0

    def test_spacing_constant_is_what_is_applied(self):
        ring = FrameRing()
        ring.offer(frame(0))
        assert not ring.offer(frame(LONG_RING_SPACING_MIN - 0.1))
        assert ring.offer(frame(LONG_RING_SPACING_MIN))

    def test_frame_without_timestamp_is_ignored(self):
        ring = FrameRing()
        assert not ring.offer({'detected': []})
        assert not ring.offer(None)
        assert len(ring) == 0


class TestLength:
    def test_capped_at_six_hours_oldest_dropped(self):
        ring = FrameRing()
        for k in range(LONG_RING_LEN + 5):
            ring.offer(frame(k * LONG_RING_SPACING_MIN))
        assert len(ring) == LONG_RING_LEN
        frames = ring.frames()
        assert frames[0]['dt'] == T0 + timedelta(minutes=5 * LONG_RING_SPACING_MIN)
        assert LONG_RING_LEN * LONG_RING_SPACING_MIN == 360.0


class TestNightGap:
    def test_day_gap_starts_a_new_ring(self):
        ring = FrameRing()
        for k in range(6):
            ring.offer(frame(k * 10))
        assert len(ring) == 6
        assert ring.offer(frame(50 + NIGHT_GAP_HOURS * 60 + 1))
        assert len(ring) == 1   # dawn frames never fit with the evening's (H14)

    def test_gap_just_under_the_limit_continues_the_ring(self):
        ring = FrameRing()
        ring.offer(frame(0))
        assert ring.offer(frame(NIGHT_GAP_HOURS * 60 - 1))
        assert len(ring) == 2

    def test_clock_step_backwards_starts_over(self):
        ring = FrameRing()
        ring.offer(frame(0))
        ring.offer(frame(10))
        assert ring.offer(frame(-30))
        assert len(ring) == 1


class TestSnapshot:
    def test_frames_is_a_copy_of_shared_dicts(self):
        ring = FrameRing()
        f = frame(0)
        ring.offer(f)
        snap = ring.frames()
        snap.append(frame(99))
        assert len(ring) == 1
        assert ring.frames()[0] is f     # detections are shared, never copied

    def test_clear(self):
        ring = FrameRing()
        ring.offer(frame(0))
        ring.clear()
        assert len(ring) == 0 and ring.span_minutes() == 0.0
