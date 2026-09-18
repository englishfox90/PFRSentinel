"""
EscapeBackoff — basin-escape retry policy (#33).

Pure, clockless: every test drives the policy with an explicit `now` rather
than the real clock, so the doubling/exhaustion/lift math is exact.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from services.allsky.escape_policy import (
    ESCAPE_COOLDOWN_BASE_S, ESCAPE_COOLDOWN_CAP_S,
    ESCAPE_EXHAUSTION_HOLD_S, ESCAPE_EXHAUSTION_THRESHOLD, EscapeBackoff)


class TestCooldownDoubling:

    def test_starts_at_the_base_cooldown(self):
        eb = EscapeBackoff()
        assert eb.cooldown() == ESCAPE_COOLDOWN_BASE_S

    def test_doubles_per_consecutive_fruitless_escape(self):
        eb = EscapeBackoff()
        for k in range(1, ESCAPE_EXHAUSTION_THRESHOLD):
            eb.record_fruitless(now=float(k))
            assert eb.cooldown() == ESCAPE_COOLDOWN_BASE_S * 2 ** k

    def test_matches_the_33_worked_example(self):
        """10 + 20 + 40 + 80 minutes across the four attempts before the
        service gives up — the numbers cited in the issue and the module
        docstring."""
        eb = EscapeBackoff()
        waits = []
        for k in range(ESCAPE_EXHAUSTION_THRESHOLD):
            waits.append(eb.cooldown())
            eb.record_fruitless(now=float(k))
        assert [w / 60.0 for w in waits] == [10.0, 20.0, 40.0, 80.0]

    def test_cooldown_is_capped(self):
        eb = EscapeBackoff()
        for k in range(20):
            eb.record_fruitless(now=float(k))
        assert eb.cooldown() == ESCAPE_COOLDOWN_CAP_S


class TestExhaustion:

    def test_not_exhausted_before_the_threshold(self):
        eb = EscapeBackoff()
        for k in range(ESCAPE_EXHAUSTION_THRESHOLD - 1):
            eb.record_fruitless(now=float(k))
            assert eb.exhausted(now=float(k)) is False

    def test_exhausted_once_the_threshold_is_reached(self):
        eb = EscapeBackoff()
        for k in range(ESCAPE_EXHAUSTION_THRESHOLD):
            eb.record_fruitless(now=float(k))
        assert eb.exhausted(now=float(ESCAPE_EXHAUSTION_THRESHOLD)) is True

    def test_stays_exhausted_short_of_the_hold(self):
        eb = EscapeBackoff()
        for k in range(ESCAPE_EXHAUSTION_THRESHOLD):
            eb.record_fruitless(now=float(k))
        exhausted_at = float(ESCAPE_EXHAUSTION_THRESHOLD - 1)
        assert eb.exhausted(exhausted_at + ESCAPE_EXHAUSTION_HOLD_S - 1) is True

    def test_lifts_after_the_hold_and_resets(self):
        eb = EscapeBackoff()
        for k in range(ESCAPE_EXHAUSTION_THRESHOLD):
            eb.record_fruitless(now=float(k))
        exhausted_at = float(ESCAPE_EXHAUSTION_THRESHOLD - 1)
        later = exhausted_at + ESCAPE_EXHAUSTION_HOLD_S + 1
        assert eb.exhausted(later) is False
        # The lift is a full reset: next round gets the base cooldown again
        # and a fresh run of `threshold` attempts, not an immediately
        # re-exhausted counter.
        assert eb.cooldown() == ESCAPE_COOLDOWN_BASE_S
        assert eb.fruitless_count == 0


class TestOneShotWarning:

    def test_should_warn_is_false_before_exhaustion(self):
        eb = EscapeBackoff()
        for k in range(ESCAPE_EXHAUSTION_THRESHOLD - 1):
            eb.record_fruitless(now=float(k))
            assert eb.should_warn() is False

    def test_should_warn_fires_exactly_once(self):
        eb = EscapeBackoff()
        for k in range(ESCAPE_EXHAUSTION_THRESHOLD):
            eb.record_fruitless(now=float(k))
        assert eb.should_warn() is True
        assert eb.should_warn() is False

    def test_further_fruitless_calls_do_not_rearm_the_warning(self):
        eb = EscapeBackoff()
        for k in range(ESCAPE_EXHAUSTION_THRESHOLD):
            eb.record_fruitless(now=float(k))
        assert eb.should_warn() is True
        eb.record_fruitless(now=100.0)
        assert eb.should_warn() is False


class TestAdmissionAndReset:

    def test_admission_resets_everything(self):
        eb = EscapeBackoff()
        for k in range(ESCAPE_EXHAUSTION_THRESHOLD):
            eb.record_fruitless(now=float(k))
        eb.record_admitted()
        assert eb.fruitless_count == 0
        assert eb.exhausted(now=1000.0) is False
        assert eb.cooldown() == ESCAPE_COOLDOWN_BASE_S

    def test_admission_before_exhaustion_also_resets(self):
        eb = EscapeBackoff()
        eb.record_fruitless(now=1.0)
        eb.record_fruitless(now=2.0)
        eb.record_admitted()
        assert eb.fruitless_count == 0
        assert eb.cooldown() == ESCAPE_COOLDOWN_BASE_S

    def test_manual_reset_clears_a_pending_warning(self):
        eb = EscapeBackoff()
        for k in range(ESCAPE_EXHAUSTION_THRESHOLD):
            eb.record_fruitless(now=float(k))
        eb.reset()
        assert eb.should_warn() is False
        assert eb.exhausted(now=1000.0) is False


class TestHoursSpent:

    def test_matches_the_worked_example(self):
        eb = EscapeBackoff()
        assert eb.hours_spent() == (10 + 20 + 40 + 80) / 60.0
