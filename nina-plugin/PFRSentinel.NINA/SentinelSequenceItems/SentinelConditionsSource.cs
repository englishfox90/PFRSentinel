#nullable enable
using PFRSentinel.Nina.Client;
using System;
using System.Threading;
using System.Threading.Tasks;

namespace PFRSentinel.NINA.SentinelSequenceItems {

    /// <summary>What kind of answer a <see cref="SentinelConditionsReading"/> holds.</summary>
    internal enum SentinelConditionsReadingKind {

        /// <summary>Nothing has been read yet, or the endpoint just changed.</summary>
        NotYet,

        /// <summary>Sentinel answered with a conditions body.</summary>
        Answered,

        /// <summary>The read failed: unreachable, rejected, too old a Sentinel, ...</summary>
        Failed,
    }

    /// <summary>One read of <c>GET /conditions</c>, immutable.</summary>
    internal sealed class SentinelConditionsReading {

        /// <summary>Nothing read yet.</summary>
        public static readonly SentinelConditionsReading NotYet = new();

        private SentinelConditionsReading() {
        }

        /// <summary>What happened.</summary>
        public SentinelConditionsReadingKind Kind { get; private init; }

        /// <summary>When the read finished, UTC.</summary>
        public DateTime ReadAtUtc { get; private init; }

        /// <summary>The body, when <see cref="Kind"/> is <see cref="SentinelConditionsReadingKind.Answered"/>.</summary>
        public SentinelConditions? Conditions { get; private init; }

        /// <summary>Why the read failed, phrased for the operator. Empty otherwise.</summary>
        public string Problem { get; private init; } = string.Empty;

        /// <summary>A successful read.</summary>
        public static SentinelConditionsReading Answered(SentinelConditions conditions, DateTime nowUtc) =>
            new() { Kind = SentinelConditionsReadingKind.Answered, ReadAtUtc = nowUtc, Conditions = conditions };

        /// <summary>A failed read.</summary>
        public static SentinelConditionsReading Failed(string problem, DateTime nowUtc) =>
            new() { Kind = SentinelConditionsReadingKind.Failed, ReadAtUtc = nowUtc, Problem = problem };
    }

    /// <summary>
    /// The one throttled, cached source of Sentinel's imaging-conditions verdict, shared
    /// by every Sentinel trigger, condition and the dockable panel.
    /// </summary>
    /// <remarks>
    /// <para>
    /// NINA validates every sequence item from a background timer and evaluates triggers
    /// and conditions between every item. Without a shared cache each of them would hit
    /// Sentinel separately; here at most one request is ever in flight and a fresh answer
    /// is reused for <see cref="MinInterval"/>.
    /// </para>
    /// <para>
    /// Failures are cached as failures, never as the last good answer: a trigger that kept
    /// reading "OK" from a Sentinel that went away an hour ago would be worse than none.
    /// </para>
    /// <para>NINA-free, like <see cref="SentinelSequenceLink"/>, so it can be driven from a harness.</para>
    /// </remarks>
    internal sealed class SentinelConditionsSource {

        /// <summary>A reading younger than this is reused rather than re-read.</summary>
        public static readonly TimeSpan MinInterval = TimeSpan.FromSeconds(5);

        private static readonly Lazy<SentinelConditionsSource> LazyInstance =
            new(() => new SentinelConditionsSource(SentinelSequenceLink.Instance), LazyThreadSafetyMode.ExecutionAndPublication);

        private readonly Func<bool, CancellationToken, Task<SentinelConditions>> fetch;
        private readonly Func<DateTime> clock;
        private readonly object gate = new();

        private volatile SentinelConditionsReading current = SentinelConditionsReading.NotYet;
        private Task<SentinelConditionsReading>? inFlight;
        private bool lastFailed;
        private int generation;

        /// <summary>Creates a source over an arbitrary fetch, for a harness.</summary>
        /// <param name="fetch">Reads the conditions; the flag asks for a config.json re-read first.</param>
        /// <param name="clock">UTC clock; null uses <see cref="DateTime.UtcNow"/>.</param>
        public SentinelConditionsSource(
            Func<bool, CancellationToken, Task<SentinelConditions>> fetch,
            Func<DateTime>? clock = null) {
            this.fetch = fetch ?? throw new ArgumentNullException(nameof(fetch));
            this.clock = clock ?? (() => DateTime.UtcNow);
        }

        private SentinelConditionsSource(SentinelSequenceLink link)
            : this(link.GetConditionsAsync) {
            link.EndpointChanged += Forget;
        }

        /// <summary>The shared source.</summary>
        public static SentinelConditionsSource Instance => LazyInstance.Value;

        /// <summary>The last reading, without starting anything.</summary>
        public SentinelConditionsReading Current => current;

        /// <summary>
        /// The last reading, starting a refresh in the background when it is older than
        /// <see cref="MinInterval"/>. Never blocks; safe from <c>Validate()</c> and the UI thread.
        /// </summary>
        public SentinelConditionsReading GetCached() {
            SentinelConditionsReading reading = current;
            if (IsDue(reading)) {
                _ = RefreshAsync(CancellationToken.None);
            }

            return reading;
        }

        /// <summary>
        /// A reading no older than <paramref name="maxAge"/> if one can be had within
        /// <paramref name="waitBudget"/>; otherwise the newest there is.
        /// </summary>
        /// <param name="maxAge">How old a cached reading may be and still be returned at once.</param>
        /// <param name="waitBudget">How long to block for a refresh. Bounded; never infinite.</param>
        /// <param name="mayBlock">False on the UI thread: return the cache and refresh in the background.</param>
        /// <remarks>
        /// For a trigger's <c>ShouldTrigger</c> or a condition's <c>Check</c>, which NINA calls
        /// synchronously between items: an exposure may have taken many minutes since the
        /// last read, and answering from that would be deciding on old news. The wait is
        /// only paid when the cache is old, and never beyond the budget.
        /// </remarks>
        public SentinelConditionsReading GetFresh(TimeSpan maxAge, TimeSpan waitBudget, bool mayBlock) {
            SentinelConditionsReading reading = current;
            if (reading.Kind != SentinelConditionsReadingKind.NotYet && clock() - reading.ReadAtUtc < maxAge) {
                return GetCached();
            }

            Task<SentinelConditionsReading> refresh = RefreshAsync(CancellationToken.None);
            if (!mayBlock) {
                return reading;
            }

            try {
                if (refresh.Wait(waitBudget)) {
                    return refresh.Result;
                }
            } catch (Exception) {
                // RefreshAsync turns every failure into a reading; this is belt and braces.
            }

            return current;
        }

        /// <summary>
        /// A reading at most <see cref="MinInterval"/> old, reading Sentinel if needed.
        /// Joins a read already in flight rather than starting a second.
        /// </summary>
        /// <param name="cancellationToken">Abandons the wait, not the shared read.</param>
        public Task<SentinelConditionsReading> RefreshAsync(CancellationToken cancellationToken) {
            Task<SentinelConditionsReading> task;
            lock (gate) {
                SentinelConditionsReading reading = current;
                if (!IsDue(reading)) {
                    return Task.FromResult(reading);
                }

                if (inFlight is null) {
                    int gen = generation;
                    bool reload = lastFailed;
                    inFlight = Task.Run(() => FetchAsync(gen, reload));
                }

                task = inFlight;
            }

            return cancellationToken.CanBeCanceled ? task.WaitAsync(cancellationToken) : task;
        }

        /// <summary>Drops the cache, e.g. because the endpoint changed.</summary>
        public void Forget() {
            lock (gate) {
                generation++;
                inFlight = null;
                lastFailed = false;
                current = SentinelConditionsReading.NotYet;
            }
        }

        private bool IsDue(SentinelConditionsReading reading) =>
            reading.Kind == SentinelConditionsReadingKind.NotYet || clock() - reading.ReadAtUtc >= MinInterval;

        private async Task<SentinelConditionsReading> FetchAsync(int gen, bool reloadConfiguration) {
            SentinelConditionsReading reading;
            try {
                SentinelConditions conditions = await fetch(reloadConfiguration, CancellationToken.None).ConfigureAwait(false);
                reading = SentinelConditionsReading.Answered(conditions, clock());
            } catch (SentinelException ex) {
                reading = SentinelConditionsReading.Failed(SentinelReadiness.Describe(ex), clock());
            } catch (Exception ex) {
                reading = SentinelConditionsReading.Failed(
                    $"PFR Sentinel's imaging conditions could not be read ({ex.GetType().Name}: {ex.Message}).", clock());
            }

            lock (gate) {
                if (gen == generation) {
                    current = reading;
                    // The fixes for most failures (enable control, regenerate the token)
                    // rewrite Sentinel's config.json, so the next read after one re-reads it.
                    lastFailed = reading.Kind == SentinelConditionsReadingKind.Failed;
                    inFlight = null;
                }
            }

            return reading;
        }
    }
}
