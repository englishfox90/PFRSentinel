"""
Basin-escape retry policy (#33).

On the reporter's rig automatic calibration triggered 148 times in one
night, failed 147 times, ran 41 basin escapes (~230s each, 6 candidates
per escape -- ~138 minutes of solid CPU), hit a peak of 220 consecutive
rejections, and never told the user anything. The escape cooldown was a
flat 600s, so a rig whose escapes can never be admitted re-ran the most
expensive fit every 10 minutes all night, indefinitely.

This module is a pure scheduling policy: no Qt, no I/O, no clock of its
own. Every method takes the current time (normally ``time.monotonic()``)
as an argument so it stays trivially unit-testable and composes with
whatever clock the caller already has.

Policy:
  * Each escape that completes without being admitted (rejected, or an
    outright worker failure) doubles the wait before the next one.
  * After ESCAPE_EXHAUSTION_THRESHOLD consecutive fruitless escapes,
    escapes stop being scheduled at all -- another bootstrap fit is not
    going to fix a seed four increasingly-patient attempts couldn't. The
    caller is expected to surface this to the user (Guided Calibration,
    a human-anchored pole, is the actual fix).
  * An admitted escape means the approach worked, so it resets the whole
    policy back to a clean slate.
  * Exhaustion is not permanent: it lifts after a long hold so a rig
    that is exhausted one night gets one more round on a later one,
    rather than being locked out until the app is restarted.
"""
from typing import Optional, Tuple

# Base wait before the *first* retry after a fruitless escape. Matches the
# previous flat ESCAPE_COOLDOWN_S so a rig that recovers after a single
# escape sees no behaviour change.
ESCAPE_COOLDOWN_BASE_S = 600  # 10 minutes

# Ceiling on the doubling. A couple of hours bounds any single wait to a
# small slice of a ~10h dark window even if ESCAPE_EXHAUSTION_THRESHOLD is
# ever raised; the #33 worked example (10+20+40+80 min) never reaches it.
ESCAPE_COOLDOWN_CAP_S = 2 * 3600  # 2 hours

# Consecutive fruitless escapes before giving up for a while. 4 -> waits of
# 10+20+40+80 = 150 minutes (~2.5h) of trying before pausing, which is
# generous enough to ride out a transient bad seed without chasing a
# genuinely wrong one all night.
ESCAPE_EXHAUSTION_THRESHOLD = 4

# How long an exhausted rig stays paused. Long enough to not burn CPU again
# the same night; short enough (well under a typical multi-night outage)
# that a one-off bad night doesn't need an app restart to recover.
ESCAPE_EXHAUSTION_HOLD_S = 12 * 3600  # 12 hours


class EscapeBackoff:
    """Owns the escape cooldown/exhaustion state described above."""

    def __init__(self,
                 base_cooldown_s: float = ESCAPE_COOLDOWN_BASE_S,
                 cap_s: float = ESCAPE_COOLDOWN_CAP_S,
                 threshold: int = ESCAPE_EXHAUSTION_THRESHOLD,
                 hold_s: float = ESCAPE_EXHAUSTION_HOLD_S) -> None:
        self._base = base_cooldown_s
        self._cap = cap_s
        self._threshold = threshold
        self._hold_s = hold_s
        self._fruitless = 0
        self._exhausted_at: Optional[float] = None
        self._warn_pending = False

    def cooldown(self) -> float:
        """Seconds to wait before the next escape attempt is allowed."""
        return min(self._base * (2 ** self._fruitless), self._cap)

    def exhausted(self, now: float) -> bool:
        """True while no further escape should be scheduled.

        Self-lifting: once the hold has elapsed this also resets the
        counters, so the caller gets a fresh run of ``threshold`` attempts
        rather than immediately re-exhausting on the next fruitless one.
        """
        if self._exhausted_at is None:
            return False
        if now - self._exhausted_at >= self._hold_s:
            self.reset()
            return False
        return True

    def record_fruitless(self, now: float) -> None:
        """An escape completed without being admitted, or failed outright."""
        self._fruitless += 1
        if self._fruitless >= self._threshold and self._exhausted_at is None:
            self._exhausted_at = now
            self._warn_pending = True

    def record_admitted(self) -> None:
        """An escape's result replaced the model -- the approach worked."""
        self.reset()

    def reset(self) -> None:
        """User reset / set_model / clear_model, or the hold lifting."""
        self._fruitless = 0
        self._exhausted_at = None
        self._warn_pending = False

    def should_warn(self) -> bool:
        """True exactly once, the first time exhaustion is reached."""
        if self._warn_pending:
            self._warn_pending = False
            return True
        return False

    def hours_spent(self) -> float:
        """Total wait, in hours, across ``threshold`` fruitless escapes.

        A derived diagnostic for the exhaustion message, not part of the
        scheduling decision -- kept here so the reported number always
        matches the constants actually in effect.
        """
        total_s = sum(min(self._base * (2 ** k), self._cap)
                      for k in range(self._threshold))
        return total_s / 3600.0

    @property
    def fruitless_count(self) -> int:
        return self._fruitless

    def exhaustion_messages(self) -> Tuple[str, str]:
        """(log line, status line) for the one-shot exhaustion warning."""
        log_msg = (
            f"CalibrationService: {self._threshold} consecutive basin escapes "
            f"rejected (~{self.hours_spent():.1f}h of attempts) — pausing "
            "automatic re-calibration. Run Guided Calibration (All-Sky "
            "settings) to anchor a good model.")
        status = (
            f"Auto-calibration paused: {self._threshold} re-calibrations "
            "rejected — run Guided Calibration (All-Sky settings)")
        return log_msg, status
