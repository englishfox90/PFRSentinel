#nullable enable
using System;

namespace PFRSentinel.NINA.SentinelSequenceItems {

    /// <summary>What the pause should do after one look at the conditions.</summary>
    internal enum SentinelWaitDecision {

        /// <summary>Keep waiting.</summary>
        KeepWaiting,

        /// <summary>Conditions have held for the settle time: resume imaging.</summary>
        Resume,

        /// <summary>The maximum wait ran out before conditions settled.</summary>
        TimedOut,
    }

    /// <summary>
    /// The pause's clock: settle time and maximum wait, fed one observation at a time.
    /// </summary>
    /// <remarks>
    /// Pure and clock-injected (the caller passes "now"), so the timing rules can be
    /// tested without NINA or a real wait. Any bad look restarts the settle time: the
    /// point is to resume on a sky that has stayed good, not one that flickered good.
    /// </remarks>
    internal sealed class SentinelConditionsWait {

        private readonly DateTime startedUtc;
        private readonly TimeSpan settle;
        private readonly TimeSpan? maxWait;

        /// <summary>Starts the clock.</summary>
        /// <param name="startedUtc">When the pause began.</param>
        /// <param name="settle">How long conditions must stay good before resuming. Zero resumes at once.</param>
        /// <param name="maxWait">Longest pause; null waits as long as it takes.</param>
        public SentinelConditionsWait(DateTime startedUtc, TimeSpan settle, TimeSpan? maxWait) {
            this.startedUtc = startedUtc;
            this.settle = settle < TimeSpan.Zero ? TimeSpan.Zero : settle;
            this.maxWait = maxWait is TimeSpan m && m > TimeSpan.Zero ? m : null;
        }

        /// <summary>Since when conditions have been good without a break, or null.</summary>
        public DateTime? GoodSinceUtc { get; private set; }

        /// <summary>How long the pause has lasted.</summary>
        public TimeSpan Elapsed(DateTime nowUtc) => nowUtc - startedUtc;

        /// <summary>Settle time still to run, or null while conditions are not good.</summary>
        public TimeSpan? SettleRemaining(DateTime nowUtc) {
            if (GoodSinceUtc is not DateTime since) {
                return null;
            }

            TimeSpan left = settle - (nowUtc - since);
            return left > TimeSpan.Zero ? left : TimeSpan.Zero;
        }

        /// <summary>Feeds one observation.</summary>
        /// <param name="nowUtc">The time of the observation.</param>
        /// <param name="allowsImaging">Whether conditions allow imaging under the item's policy.</param>
        public SentinelWaitDecision Step(DateTime nowUtc, bool allowsImaging) {
            if (allowsImaging) {
                GoodSinceUtc ??= nowUtc;
                if (nowUtc - GoodSinceUtc.Value >= settle) {
                    return SentinelWaitDecision.Resume;
                }
            } else {
                GoodSinceUtc = null;
            }

            return maxWait is TimeSpan limit && Elapsed(nowUtc) >= limit
                ? SentinelWaitDecision.TimedOut
                : SentinelWaitDecision.KeepWaiting;
        }
    }
}
