#nullable enable
using PFRSentinel.Nina.Client;
using System;

namespace PFRSentinel.NINA.SentinelTarget {

    /// <summary>What the target push is doing, as the panel shows it.</summary>
    internal enum SentinelTargetState {
        /// <summary>The reporter has not finished its first read yet.</summary>
        Starting,

        /// <summary>Switched off on the plugin's options page.</summary>
        Off,

        /// <summary>No deep-sky target is running, so nothing is sent.</summary>
        Idle,

        /// <summary>Sentinel accepted the last send of <see cref="SentinelTargetStatusSnapshot.Report"/>.</summary>
        Sending,

        /// <summary>The last send failed; <see cref="SentinelTargetStatusSnapshot.Problem"/> says why.</summary>
        Failing,
    }

    /// <summary>One immutable reading of the push, safe to hand across threads.</summary>
    /// <param name="State">What the push is doing.</param>
    /// <param name="Report">The target sent (or being retried), or null.</param>
    /// <param name="SentAtUtc">When Sentinel last accepted it, or null.</param>
    /// <param name="Problem">Why the last send failed, for <see cref="SentinelTargetState.Failing"/>.</param>
    internal sealed record SentinelTargetStatusSnapshot(
        SentinelTargetState State,
        SentinelTargetReport? Report,
        DateTime? SentAtUtc,
        string? Problem);

    /// <summary>
    /// The target push's latest state, written by <see cref="SentinelTargetReporter"/> and
    /// read by the imaging-tab panel.
    /// </summary>
    /// <remarks>
    /// Static because the reporter is hosted by the plugin manifest and the panel is a
    /// separate MEF export: neither holds a reference to the other, and the reporter runs
    /// whether or not the panel is open. The panel reads it on its own poll tick; nothing
    /// here raises events, so a closed panel costs nothing.
    /// </remarks>
    internal static class SentinelTargetStatus {

        private static readonly object Gate = new();
        private static SentinelTargetStatusSnapshot current =
            new(SentinelTargetState.Starting, null, null, null);

        /// <summary>The latest reading.</summary>
        public static SentinelTargetStatusSnapshot Current {
            get {
                lock (Gate) {
                    return current;
                }
            }
        }

        /// <summary>Records a state with no target attached (off, idle, starting).</summary>
        public static void Set(SentinelTargetState state) =>
            Publish(new SentinelTargetStatusSnapshot(state, null, null, null));

        /// <summary>Sentinel accepted <paramref name="report"/> just now.</summary>
        public static void Sent(SentinelTargetReport report) =>
            Publish(new SentinelTargetStatusSnapshot(SentinelTargetState.Sending, report, DateTime.UtcNow, null));

        /// <summary>
        /// A send of <paramref name="report"/> failed. Keeps the time of the last accepted
        /// send, so the panel can say how long Sentinel has gone without an update.
        /// </summary>
        public static void Failed(SentinelTargetReport? report, string problem) {
            lock (Gate) {
                current = new SentinelTargetStatusSnapshot(
                    SentinelTargetState.Failing, report ?? current.Report, current.SentAtUtc, problem);
            }
        }

        private static void Publish(SentinelTargetStatusSnapshot snapshot) {
            lock (Gate) {
                current = snapshot;
            }
        }
    }
}
