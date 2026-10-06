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
    /// reporting is switched off, it sends one clear and then stays quiet. An unknown
    /// reading (sequencer not initialised) sends nothing.
    /// </para>
    /// <para>
    /// Transport is the sequence items' shared <see cref="SentinelSequenceLink"/>, so the
    /// push honours the same base URL override and token as Start/Stop.
    /// </para>
    /// <para>
    /// The loop never throws, and an unreachable Sentinel is routine — NINA runs on
    /// nights Sentinel does not. Failures log one <c>Warning</c> per distinct message and
    /// back off; nothing is logged at <c>Error</c>, and the token is never logged. A
    /// rejected token or a disabled control API escalates its back-off, because each of
    /// those requests also writes a warning into Sentinel's own log.
    /// </para>
    /// <para>
    /// A working push is visible at NINA's default <c>Info</c> level too: the switch
    /// turning on or off, the first idle reading (no target running), each target sent
    /// or cleared, and the push recovering after a failure. Heartbeats stay silent.
    /// Before these were <c>Debug</c>, a working push and one that never ran left the
    /// same empty log.
    /// </para>
    /// </remarks>
    internal sealed class SentinelTargetReporter : IDisposable {

        /// <summary>How often the target is read.</summary>
        public static readonly TimeSpan DefaultInterval = TimeSpan.FromSeconds(10);

        /// <summary>Resend period for an unchanged target. Well inside Sentinel's 120 s stale-out.</summary>
        public static readonly TimeSpan DefaultHeartbeat = TimeSpan.FromSeconds(30);

        // NINA is still composing its view models right after plugin load; the first
        // read waits for that rather than collecting a round of start-up faults.
        private static readonly TimeSpan InitialDelay = TimeSpan.FromSeconds(15);

        private static readonly TimeSpan FailureBackoff = TimeSpan.FromSeconds(30);

        // Ceiling for an escalating back-off, and the flat wait for a Sentinel without
        // the route, which will not grow one until it is updated.
        private static readonly TimeSpan MaxBackoff = TimeSpan.FromMinutes(10);

        private readonly Func<SentinelTargetReading> source;
        private readonly Func<bool> enabled;
        private readonly Func<string?> baseUrlOverrideSource;
        private readonly CancellationTokenSource cts = new();
        private readonly SemaphoreSlim wake = new(0, 1);

        private Task? loop;
        private volatile bool disposed;
        private volatile bool resetRequested;

        // Loop thread only from here down.
        private string? lastSentJson;
        private string? rejectedJson;
        private long lastSentAtMs;
        private long retryAfterMs;
        private int escalation;
        private bool reloadConfiguration = true;
        private bool lastEnabled = true;
        private bool? announcedEnabled;
        private bool announcedIdle;
        private string? lastWarning;
        private SentinelTargetReport? sendingReport;

        /// <summary>Creates a reporter. Nothing is sent until <see cref="Start"/>.</summary>
        /// <param name="source">The current reading. Expected never to throw.</param>
        /// <param name="enabled">The options-page switch, re-read every tick.</param>
        /// <param name="baseUrlOverrideSource">The base URL override, re-read before every send.</param>
        public SentinelTargetReporter(
            Func<SentinelTargetReading> source,
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

        /// <summary>
        /// Runs the next tick now and cuts any back-off, e.g. after an option changed.
        /// </summary>
        /// <remarks>
        /// The operator flipping the switch or fixing the address is exactly when a
        /// ten-minute back-off must not apply, so this also re-reads config.json and
        /// forgets a body Sentinel rejected.
        /// </remarks>
        public void RequestSendNow() {
            if (disposed) {
                return;
            }

            resetRequested = true;

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
            try {
                await Task.Delay(InitialDelay, cancellationToken).ConfigureAwait(false);
            } catch (OperationCanceledException) {
                return;
            }

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
            if (resetRequested) {
                resetRequested = false;
                retryAfterMs = 0;
                rejectedJson = null;
                reloadConfiguration = true;
            }

            bool isEnabled = ReadEnabled();
            AnnounceEnabled(isEnabled);
            // With a clear still owed, the send below decides between Off and Failing.
            if (!isEnabled && lastSentJson is null) {
                SentinelTargetStatus.Set(SentinelTargetState.Off);
            }

            SentinelTargetReading reading = isEnabled ? ReadSource() : SentinelTargetReading.Of(null);
            if (!reading.Known) {
                return;
            }

            SentinelTargetReport? report = reading.Report;
            if (isEnabled && report is null && lastSentJson is null) {
                SentinelTargetStatus.Set(SentinelTargetState.Idle);
                if (!announcedIdle) {
                    announcedIdle = true;
                    Logger.Info("PFR Sentinel: no deep-sky target is running in the sequencer, so no target is sent " +
                        "to Sentinel. One is sent within about 10 s of a target container (or Target Scheduler) starting a target.");
                }
            }
            string? json = report is null ? null : SentinelTargetPayload.Serialize(report);

            long now = Environment.TickCount64;
            if (!ShouldSend(json, now) || now < retryAfterMs) {
                return;
            }

            // Sentinel refused this exact body with a 400; resending it cannot succeed.
            if (json is not null && string.Equals(json, rejectedJson, StringComparison.Ordinal)) {
                return;
            }

            sendingReport = report;
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
                escalation = 0;
                rejectedJson = null;
                reloadConfiguration = false;

                if (report is not null) {
                    SentinelTargetStatus.Sent(report);
                } else {
                    SentinelTargetStatus.Set(isEnabled ? SentinelTargetState.Idle : SentinelTargetState.Off);
                }

                if (lastWarning is not null) {
                    lastWarning = null;
                    Logger.Info("PFR Sentinel: target push reached Sentinel again.");
                }

                if (changed) {
                    // A clear already says so; the next idle stretch needs no second note.
                    announcedIdle = true;
                    Logger.Info(report is null
                        ? "PFR Sentinel: cleared the target on Sentinel."
                        : $"PFR Sentinel: sent target '{SentinelTargetPayload.CleanName(report.Name)}' to Sentinel" +
                          (report.FovWidthDeg is null ? " without a field of view." : "."));
                }
            } catch (SentinelException ex) when (ex.StatusCode == HttpStatusCode.NotFound) {
                Fail(now, MaxBackoff,
                    "PFR Sentinel: this Sentinel does not accept targets from NINA (HTTP 404). " +
                    "Update Sentinel to see the target on its all-sky overlay.");
            } catch (SentinelException ex) {
                if (ex.Kind == SentinelErrorKind.BadRequest) {
                    rejectedJson = json;
                }

                Fail(now, Escalates(ex.Kind) ? NextEscalation() : FailureBackoff,
                    "PFR Sentinel: could not send the NINA target. " + WithoutPrefix(SentinelReadiness.Describe(ex)));
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

        /// <summary>
        /// Failures that only the operator can fix. Each attempt also lands in
        /// Sentinel's log, so a wrong token must not write one every 30 s all night.
        /// </summary>
        private static bool Escalates(SentinelErrorKind kind) => kind is
            SentinelErrorKind.Unauthorized or
            SentinelErrorKind.HostNotAllowed or
            SentinelErrorKind.ControlDisabled or
            SentinelErrorKind.BadRequest;

        // 30 s, 60 s, 120 s ... capped at MaxBackoff.
        private TimeSpan NextEscalation() {
            double seconds = FailureBackoff.TotalSeconds * Math.Pow(2, Math.Min(escalation, 10));
            escalation++;
            return TimeSpan.FromSeconds(Math.Min(seconds, MaxBackoff.TotalSeconds));
        }

        private void Fail(long now, TimeSpan backoff, string message) {
            // The fix for most failures rewrites config.json; pick it up on the retry.
            reloadConfiguration = true;
            retryAfterMs = now + (long)backoff.TotalMilliseconds;
            WarnOnce(message);
            SentinelTargetStatus.Failed(sendingReport, WithoutPrefix(message));
        }

        // Describe() opens some messages with "PFR Sentinel: " already.
        private static string WithoutPrefix(string detail) {
            const string prefix = "PFR Sentinel: ";
            return detail.StartsWith(prefix, StringComparison.Ordinal) ? detail.Substring(prefix.Length) : detail;
        }

        private bool ReadEnabled() {
            try {
                lastEnabled = enabled();
            } catch (Exception) {
                // Keep the last answer; a faulting settings read must not flap the marker.
            }

            return lastEnabled;
        }

        private void AnnounceEnabled(bool isEnabled) {
            if (announcedEnabled == isEnabled) {
                return;
            }

            announcedEnabled = isEnabled;
            Logger.Info(isEnabled
                ? "PFR Sentinel: target reporting to Sentinel is on."
                : "PFR Sentinel: target reporting to Sentinel is off (plugin options).");
        }

        private SentinelTargetReading ReadSource() {
            try {
                return source();
            } catch (Exception ex) {
                WarnOnce($"PFR Sentinel: could not read the sequencer target ({ex.GetType().Name}: {ex.Message}).");
                return SentinelTargetReading.Unknown;
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
