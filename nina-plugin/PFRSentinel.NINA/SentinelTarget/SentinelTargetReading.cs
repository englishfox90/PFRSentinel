#nullable enable
using PFRSentinel.Nina.Client;

namespace PFRSentinel.NINA.SentinelTarget {

    /// <summary>
    /// One read of the sequencer: a target, no target, or unknown.
    /// </summary>
    /// <remarks>
    /// "No target" and "unknown" need opposite handling. No target sends Sentinel one
    /// clear; unknown (the sequencer is not initialised yet, or NINA supplied no
    /// mediator) sends nothing, so a target Sentinel already holds is left to its own
    /// stale-out rather than wiped because NINA was still starting up.
    /// </remarks>
    internal readonly struct SentinelTargetReading {

        private SentinelTargetReading(bool known, SentinelTargetReport? report) {
            Known = known;
            Report = report;
        }

        /// <summary>The sequencer could not be asked; send nothing this tick.</summary>
        public static SentinelTargetReading Unknown => new(false, null);

        /// <summary>A definite answer: the running target, or null for none.</summary>
        public static SentinelTargetReading Of(SentinelTargetReport? report) => new(true, report);

        /// <summary>Whether this reading is a definite answer.</summary>
        public bool Known { get; }

        /// <summary>The running target, or null. Meaningful only when <see cref="Known"/>.</summary>
        public SentinelTargetReport? Report { get; }
    }
}
