#nullable enable
using NINA.Core.Utility;
using PFRSentinel.Nina.Client;
using PFRSentinel.NINA.SentinelSequenceItems;
using System;
using System.Net;
using System.Threading;
using System.Threading.Tasks;

namespace PFRSentinel.NINA.SentinelTarget {

    /// <summary>
    /// Pushes the sequencer's current target to Sentinel's <c>POST /nina/target</c>, so
    /// the all-sky overlay can mark where the main scope is pointed.
    /// </summary>
    /// <remarks>
    /// <para>
    /// Every <see cref="DefaultInterval"/> it reads the target and sends it when it
    /// changed, or as a <see cref="DefaultHeartbeat"/> while one is set — Sentinel stops
    /// drawing a target it has not heard about for two minutes, which is what makes a
    /// closed or crashed NINA leave no marker behind. When the target goes away, or
    /// reporting is switched off, it sends one clear and then stays quiet.
    /// </para>
    /// <para>
    /// Transport is the sequence items' shared <see cref="SentinelSequenceLink"/>, so the
    /// push honours the same base URL override and token as Start/Stop.
    /// </para>
    /// <para>
    /// The loop never throws, and an unreachable Sentinel is routine — NINA runs on
    /// nights Sentinel does not. Failures log one <c>Warning</c> per distinct message and
    /// back off; nothing is logged at <c>Error</c>, and the token is never logged.
    /// </para>
    /// </remarks>
    internal sealed class SentinelTargetReporter : IDisposable {

        /// <summary>How often the target is read.</summary>
        public static readonly TimeSpan DefaultInterval = TimeSpan.FromSeconds(10);

        /// <summary>Resend period for an unchanged target. Well inside Sentinel's 120 s stale-out.</summary>
        public static readonly TimeSpan DefaultHeartbeat = TimeSpan.FromSeconds(30);

        // After a failure, wait this long before trying again.
        private static readonly TimeSpan FailureBackoff = TimeSpan.FromSeconds(30);

        // A Sentinel without the route will not grow one until it is updated.
        private static readonly TimeSpan MissingRouteBackoff = TimeSpan.FromMinutes(10);

        private readonly Func<SentinelTargetReport?> source;
        private readonly Func<bool> enabled;
        private readonly Func<string?> baseUrlOverrideSource;
        private readonly CancellationTokenSource cts = new();
        private readonly SemaphoreSlim wake = new(0, 1);

        private Task? loop;
        private volatile bool disposed;

        // Loop thread only from here down.
        private string? lastSentJson;
        private long lastSentAtMs;
        private long retryAfterMs;
        private bool reloadConfiguration = true;
        private bool lastEnabled = true;
        private string? lastWarning;

        /// <summary>Creates a reporter. Nothing is sent until <see cref="Start"/>.</summary>
        /// <param name="source">The current target, or null for none. Expected never to throw.</param>
        /// <param name="enabled">The options-page switch, re-read every tick.</param>
        /// <param name="baseUrlOverrideSource">The base URL override, re-read before every send.</param>
        public SentinelTargetReporter(
            Func<SentinelTargetReport?> source,
            Func<bool> enabled,
            Func<string?> baseUrlOverrideSource) {

            this.source = source ?? throw new ArgumentNullException(nameof(source));
            this.enabled = enabled ?? throw new ArgumentNullException(nameof(enabled));
            this.baseUrlOverrideSource = baseUrlOverrideSource ?? throw new ArgumentNullException(nameof(baseUrlOverrideSource));
        }

        /// <summary>Tick period.</summary>
        public TimeSpan Interval { get; set; } = DefaultInterval;

        /// <summary>Resend period for an unchanged target.</summary>
        public TimeSpan Heartbeat { get; set; } = DefaultHeartbeat;

        /// <summary>Starts the loop. Idempotent.</summary>
        public void Start() {
            if (disposed || loop is not null) {
                return;
            }

            loop = Task.Run(() => RunAsync(cts.Token));
        }

        /// <summary>Runs the next tick now, e.g. after the options switch was flipped.</summary>
        public void RequestSendNow() {
            if (disposed) {
                return;
            }

            try {
                if (wake.CurrentCount == 0) {
                    wake.Release();
                }
            } catch (SemaphoreFullException) {
                // Raced with another waker; the loop is already about to run.
            } catch (ObjectDisposedException) {
            }
        }

        /// <summary>Stops the loop without blocking the caller.</summary>
        /// <remarks>
        /// Sends no final clear: shutdown must not wait on the network, and Sentinel
        /// drops the marker by itself once the heartbeat stops.
        /// </remarks>
        public void Dispose() {
            if (disposed) {
                return;
            }

            disposed = true;

            try {
                cts.Cancel();
            } catch (ObjectDisposedException) {
            }

            Task? running = loop;
            if (running is null) {
                ReleaseResources();
            } else {
                running.ContinueWith(_ => ReleaseResources(), TaskScheduler.Default);
            }
        }

        private void ReleaseResources() {
            try {
                cts.Dispose();
            } catch (Exception) {
            }

            try {
                wake.Dispose();
            } catch (Exception) {
            }
        }

        private async Task RunAsync(CancellationToken cancellationToken) {
            while (!cancellationToken.IsCancellationRequested) {
                try {
                    await TickAsync(cancellationToken).ConfigureAwait(false);
                } catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested) {
                    break;
                } catch (Exception ex) {
                    // TickAsync absorbs every modelled failure; this is the backstop that
                    // keeps one surprise from ending the push for the rest of the night.
                    WarnOnce($"PFR Sentinel: the target push hit an unexpected error ({ex.GetType().Name}: {ex.Message}).");
                }

                try {
                    await wake.WaitAsync(Interval, cancellationToken).ConfigureAwait(false);
                } catch (OperationCanceledException) {
                    break;
                } catch (ObjectDisposedException) {
                    break;
                }
            }
        }

        private async Task TickAsync(CancellationToken cancellationToken) {
            SentinelTargetReport? report = ReadEnabled() ? ReadSource() : null;
            string? json = report is null ? null : SentinelTargetPayload.Serialize(report);

            long now = Environment.TickCount64;
            if (!ShouldSend(json, now) || now < retryAfterMs) {
                return;
            }

            try {
                SentinelSequenceLink link = SentinelSequenceLink.Instance;
                link.ApplyEndpointOverride(ReadOverride(link));

                SentinelTargetResult result = await link
                    .PostTargetAsync(report, reloadConfiguration, cancellationToken)
                    .ConfigureAwait(false);

                if (!result.Accepted) {
                    Fail(now, FailureBackoff, $"PFR Sentinel: Sentinel did not accept the NINA target. {result.Message}");
                    return;
                }

                bool changed = !string.Equals(json, lastSentJson, StringComparison.Ordinal);
                lastSentJson = json;
                lastSentAtMs = now;
                retryAfterMs = 0;
                reloadConfiguration = false;

                if (lastWarning is not null) {
                    lastWarning = null;
                    Logger.Debug("PFR Sentinel: target push reached Sentinel again.");
                }

                if (changed) {
                    Logger.Debug(report is null
                        ? "PFR Sentinel: cleared the target on Sentinel."
                        : $"PFR Sentinel: sent target '{report.Name}' to Sentinel.");
                }
            } catch (SentinelException ex) when (ex.StatusCode == HttpStatusCode.NotFound) {
                Fail(now, MissingRouteBackoff,
                    "PFR Sentinel: this Sentinel does not accept targets from NINA (HTTP 404). " +
                    "Update Sentinel to see the target on its all-sky overlay.");
            } catch (SentinelException ex) {
                Fail(now, FailureBackoff, "PFR Sentinel: could not send the NINA target. " + SentinelReadiness.Describe(ex));
            }
        }

        /// <summary>
        /// A target goes out when it changed or the heartbeat is due; a clear goes out
        /// once, and only when Sentinel holds a target from us.
        /// </summary>
        private bool ShouldSend(string? json, long now) {
            if (json is null) {
                return lastSentJson is not null;
            }

            return !string.Equals(json, lastSentJson, StringComparison.Ordinal)
                || now - lastSentAtMs >= (long)Heartbeat.TotalMilliseconds;
        }

        private void Fail(long now, TimeSpan backoff, string message) {
            // The fix for most failures rewrites config.json; pick it up on the retry.
            reloadConfiguration = true;
            retryAfterMs = now + (long)backoff.TotalMilliseconds;
            WarnOnce(message);
        }

        private bool ReadEnabled() {
            try {
                lastEnabled = enabled();
            } catch (Exception) {
                // Keep the last answer; a faulting settings read must not flap the marker.
            }

            return lastEnabled;
        }

        private SentinelTargetReport? ReadSource() {
            try {
                return source();
            } catch (Exception ex) {
                WarnOnce($"PFR Sentinel: could not read the sequencer target ({ex.GetType().Name}: {ex.Message}).");
                return null;
            }
        }

        private string? ReadOverride(SentinelSequenceLink link) {
            try {
                return baseUrlOverrideSource();
            } catch (Exception) {
                // Re-applying what is already in force is a no-op; null would drop a
                // working override because of one faulted settings read.
                return link.AppliedOverride;
            }
        }

        private void WarnOnce(string message) {
            if (string.Equals(message, lastWarning, StringComparison.Ordinal)) {
                return;
            }

            lastWarning = message;
            Logger.Warning(message);
        }
    }
}
