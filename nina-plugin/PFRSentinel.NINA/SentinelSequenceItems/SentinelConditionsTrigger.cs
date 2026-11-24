#nullable enable
using Newtonsoft.Json;
using NINA.Core.Model;
using NINA.Core.Utility;
using NINA.Profile.Interfaces;
using NINA.Sequencer.Container;
using NINA.Sequencer.Interfaces;
using NINA.Sequencer.SequenceItem;
using NINA.Sequencer.Trigger;
using NINA.Sequencer.Validations;
using System;
using System.Collections.Generic;
using System.ComponentModel.Composition;
using System.Threading;
using System.Threading.Tasks;

namespace PFRSentinel.NINA.SentinelSequenceItems {

    /// <summary>
    /// Sequencer trigger: pauses before the next exposure while PFR Sentinel says the sky is
    /// not good enough to image, and resumes once it has stayed good for the settle time.
    /// </summary>
    /// <remarks>
    /// <para>
    /// Namespace and type name are load-bearing forever: a saved sequence serialises the
    /// fully qualified name. "Conditions" names what it reads (Sentinel's
    /// <c>/conditions</c>), and "Trigger" separates it from
    /// <see cref="SentinelConditionsCondition"/>, its loop-condition sibling, the way NINA's
    /// own pairs are named - so neither name will need to change if either grows.
    /// </para>
    /// <para>
    /// It only waits. It does not stop guiding or tracking, park, close or warm anything:
    /// those belong to NINA's own instructions and to the observatory's safety chain, and
    /// a plugin that moved hardware on a camera's opinion of the sky would be a hazard.
    /// </para>
    /// <para>
    /// <b>Validation issues cover this item's settings only.</b> NINA's
    /// <c>SequenceTrigger.Run</c> calls <c>Validate()</c> before <c>Execute</c> and fails
    /// the trigger on any issue, so an "unreachable Sentinel" issue would turn the safe
    /// default - pause when there is no verdict - into "fail the trigger and carry on
    /// imaging". Sentinel's state is shown on the status line instead.
    /// </para>
    /// </remarks>
    [ExportMetadata("Name", "Sentinel: pause on bad conditions")]
    [ExportMetadata("Description", "Before each exposure, asks PFR Sentinel whether the sky is good enough to image. If not, waits until it has been good again for the settle time. Only waits: it does not stop guiding, park or close anything.")]
    [ExportMetadata("Icon", "PFRSentinel_PauseSVG")]
    [ExportMetadata("Category", "PFR Sentinel")]
    [Export(typeof(ISequenceTrigger))]
    [JsonObject(MemberSerialization.OptIn)]
    public class SentinelConditionsTrigger : SequenceTrigger, IValidatable {

        private readonly IProfileService? profileService;
        private readonly IPluginOptionsAccessor? options;

        private IList<string> issues = new List<string>();
        private string conditionsSummary = "Sentinel: waiting for the first answer";
        private double? minStarRatio;
        private double? maxCloudCoverPct;
        private double? maxFwhmArcsec;
        private double? maxFwhmPx;
        private double? maxStarDropPct;
        private double? maxFwhmRisePct;
        private double? minMoonSeparationDeg;
        private bool requireRoofOpen;
        private bool treatNoVerdictAsBad = true;
        private bool onlyBeforeExposures = true;
        private int settleSeconds = SentinelConditionsSettings.DefaultSettleSeconds;
        private int maxWaitMinutes;
        private bool failOnTimeout;

        /// <summary>Constructed by MEF for the trigger factory.</summary>
        [ImportingConstructor]
        public SentinelConditionsTrigger(IProfileService profileService) {
            this.profileService = profileService;
            options = profileService is null ? null : SentinelPluginOptions.Accessor(profileService);
        }

        /// <summary>Copy constructor backing <see cref="Clone"/>.</summary>
        public SentinelConditionsTrigger(SentinelConditionsTrigger copyMe) : this(copyMe.profileService!) {
            CopyMetaData(copyMe);
        }

        /// <summary>Lowest star count / clear-sky baseline; null = not used.</summary>
        [JsonProperty]
        public double? MinStarRatio {
            get => minStarRatio;
            set { minStarRatio = SentinelConditionsSettings.Limit(value, 10.0); RaisePropertyChanged(nameof(MinStarRatio)); }
        }

        /// <summary>Highest cloud cover, percent; null = not used.</summary>
        [JsonProperty]
        public double? MaxCloudCoverPct {
            get => maxCloudCoverPct;
            set { maxCloudCoverPct = SentinelConditionsSettings.Percent(value, allowZero: true); RaisePropertyChanged(nameof(MaxCloudCoverPct)); }
        }

        /// <summary>Highest FWHM in arcseconds; null = not used.</summary>
        [JsonProperty]
        public double? MaxFwhmArcsec {
            get => maxFwhmArcsec;
            set { maxFwhmArcsec = SentinelConditionsSettings.Limit(value, 600.0); RaisePropertyChanged(nameof(MaxFwhmArcsec)); }
        }

        /// <summary>Highest FWHM in pier-camera pixels; null = not used.</summary>
        [JsonProperty]
        public double? MaxFwhmPx {
            get => maxFwhmPx;
            set { maxFwhmPx = SentinelConditionsSettings.Limit(value, 1000.0); RaisePropertyChanged(nameof(MaxFwhmPx)); }
        }

        /// <summary>Largest star-count drop against recent frames, percent (40 = fires at −40 %); null = not used.</summary>
        [JsonProperty]
        public double? MaxStarDropPct {
            get => maxStarDropPct;
            set { maxStarDropPct = SentinelConditionsSettings.Percent(value, allowZero: false); RaisePropertyChanged(nameof(MaxStarDropPct)); }
        }

        /// <summary>Largest FWHM rise against the night median, percent; null = not used.</summary>
        [JsonProperty]
        public double? MaxFwhmRisePct {
            get => maxFwhmRisePct;
            set { maxFwhmRisePct = SentinelConditionsSettings.Percent(value, allowZero: false); RaisePropertyChanged(nameof(MaxFwhmRisePct)); }
        }

        /// <summary>Smallest target-to-Moon distance, degrees, while the Moon is up; null = not used.</summary>
        [JsonProperty]
        public double? MinMoonSeparationDeg {
            get => minMoonSeparationDeg;
            set { minMoonSeparationDeg = SentinelConditionsSettings.Limit(value, 180.0); RaisePropertyChanged(nameof(MinMoonSeparationDeg)); }
        }

        /// <summary>Pause unless the roof reads Open, whatever Sentinel's own roof setting.</summary>
        [JsonProperty]
        public bool RequireRoofOpen {
            get => requireRoofOpen;
            set { requireRoofOpen = value; RaisePropertyChanged(nameof(RequireRoofOpen)); }
        }

        /// <summary>
        /// Pause when Sentinel gives no verdict (unreachable, token rejected, stale, switched
        /// off). On by default: no answer is not a yes. A Sentinel that answers but has not
        /// judged a frame yet is not this case; the evaluator allows it.
        /// </summary>
        [JsonProperty]
        public bool TreatNoVerdictAsBad {
            get => treatNoVerdictAsBad;
            set { treatNoVerdictAsBad = value; RaisePropertyChanged(nameof(TreatNoVerdictAsBad)); }
        }

        /// <summary>
        /// Check only before exposures. On by default, so a pause never holds back a park,
        /// warm-up or end-of-night instruction that comes after the imaging.
        /// </summary>
        [JsonProperty]
        public bool OnlyBeforeExposures {
            get => onlyBeforeExposures;
            set { onlyBeforeExposures = value; RaisePropertyChanged(nameof(OnlyBeforeExposures)); }
        }

        /// <summary>How long conditions must stay good before imaging resumes, seconds.</summary>
        [JsonProperty]
        public int SettleSeconds {
            get => settleSeconds;
            set { settleSeconds = Math.Clamp(value, 0, SentinelConditionsSettings.MaxSettleSeconds); RaisePropertyChanged(nameof(SettleSeconds)); }
        }

        /// <summary>Longest pause, minutes; 0 waits as long as it takes.</summary>
        [JsonProperty]
        public int MaxWaitMinutes {
            get => maxWaitMinutes;
            set { maxWaitMinutes = Math.Clamp(value, 0, SentinelConditionsSettings.MaxWaitLimitMinutes); RaisePropertyChanged(nameof(MaxWaitMinutes)); }
        }

        /// <summary>When the maximum wait runs out: true fails the trigger, false carries on imaging.</summary>
        [JsonProperty]
        public bool FailOnTimeout {
            get => failOnTimeout;
            set { failOnTimeout = value; RaisePropertyChanged(nameof(FailOnTimeout)); }
        }

        /// <summary>Sentinel's current verdict, in words, for the sequencer view. Not saved.</summary>
        public string ConditionsSummary {
            get => conditionsSummary;
            private set {
                if (conditionsSummary == value) {
                    return;
                }

                conditionsSummary = value;
                RaisePropertyChanged(nameof(ConditionsSummary));
            }
        }

        /// <summary>Validation problems with this item's settings. Notifying, see <see cref="StartSentinelCapture.Issues"/>.</summary>
        public IList<string> Issues {
            get => issues;
            set {
                issues = value;
                RaisePropertyChanged(nameof(Issues));
            }
        }

        /// <summary>Reads only the cached verdict; never makes or waits for a request.</summary>
        public bool Validate() {
            ApplyOverride();
            Show(Evaluate(SentinelConditionsSource.Instance.GetCached(), DateTime.UtcNow));

            var found = new List<string>();
            if (MaxWaitMinutes > 0 && SettleSeconds >= MaxWaitMinutes * 60) {
                found.Add("The settle time is as long as the maximum wait, so every pause would time out. Shorten the settle time or raise the maximum wait.");
            }

            Issues = found;
            return found.Count == 0;
        }

        /// <summary>Whether to pause before <paramref name="nextItem"/>.</summary>
        /// <remarks>
        /// Called synchronously between items. Reads the shared cache; when it is older than
        /// <see cref="SentinelConditionsSettings.FreshFor"/> (an exposure has run since), it
        /// blocks for one bounded refresh - never on the UI thread.
        /// </remarks>
        public override bool ShouldTrigger(ISequenceItem previousItem, ISequenceItem nextItem) {
            if (nextItem is null || (OnlyBeforeExposures && nextItem is not IExposureItem)) {
                return false;
            }

            try {
                ApplyOverride();
                SentinelConditionsReading reading = SentinelConditionsSource.Instance.GetFresh(
                    SentinelConditionsSettings.FreshFor,
                    SentinelConditionsSettings.CheckWaitBudget,
                    mayBlock: !SentinelConditionsSettings.OnUiThread());

                SentinelConditionsVerdict verdict = Evaluate(reading, DateTime.UtcNow);
                Show(verdict);

                bool pause = !verdict.AllowsImaging(TreatNoVerdictAsBad);
                if (pause) {
                    Logger.Info($"PFR Sentinel: pausing before {nextItem.Name} - {verdict.Summary}");
                }

                return pause;
            } catch (Exception ex) {
                Logger.Error($"PFR Sentinel: the conditions check failed ({ex.GetType().Name}: {ex.Message}).");
                return TreatNoVerdictAsBad;
            }
        }

        /// <summary>Waits until conditions have been good for the settle time, the maximum wait runs out, or the sequence is stopped.</summary>
        /// <exception cref="SequenceEntityFailedException">The maximum wait ran out and <see cref="FailOnTimeout"/> is set.</exception>
        public override async Task Execute(ISequenceContainer context, IProgress<ApplicationStatus> progress, CancellationToken token) {
            DateTime started = DateTime.UtcNow;
            var wait = new SentinelConditionsWait(
                started,
                TimeSpan.FromSeconds(SettleSeconds),
                MaxWaitMinutes > 0 ? TimeSpan.FromMinutes(MaxWaitMinutes) : null);
            string logged = string.Empty;

            while (true) {
                token.ThrowIfCancellationRequested();
                ApplyOverride();

                SentinelConditionsReading reading = await SentinelConditionsSource.Instance
                    .RefreshAsync(token).ConfigureAwait(false);
                DateTime now = DateTime.UtcNow;
                SentinelConditionsVerdict verdict = Evaluate(reading, now);
                Show(verdict);

                SentinelWaitDecision decision = wait.Step(now, verdict.AllowsImaging(TreatNoVerdictAsBad));
                string elapsed = Clock(wait.Elapsed(now));

                if (decision == SentinelWaitDecision.Resume) {
                    string resumed = $"PFR Sentinel: conditions OK again after {elapsed}, resuming. {verdict.Summary}";
                    Logger.Info(resumed);
                    progress?.Report(new ApplicationStatus { Status = resumed });
                    return;
                }

                if (decision == SentinelWaitDecision.TimedOut) {
                    string timedOut = $"PFR Sentinel: conditions still not OK after the {MaxWaitMinutes} min maximum wait - {verdict.Summary}";
                    if (FailOnTimeout) {
                        Logger.Error(timedOut);
                        throw new SequenceEntityFailedException(timedOut);
                    }

                    Logger.Warning(timedOut + " Continuing the sequence.");
                    progress?.Report(new ApplicationStatus { Status = timedOut + " Continuing." });
                    return;
                }

                if (verdict.Summary != logged) {
                    Logger.Info($"PFR Sentinel: paused ({elapsed}) - {verdict.Summary}");
                    logged = verdict.Summary;
                }

                string status = wait.SettleRemaining(now) is TimeSpan left
                    ? $"PFR Sentinel: conditions OK, resuming in {Clock(left)} if they hold (paused {elapsed})"
                    : $"Paused by PFR Sentinel ({elapsed}): {verdict.Summary}";
                progress?.Report(new ApplicationStatus { Status = status });

                await Task.Delay(SentinelConditionsSettings.PollInterval, token).ConfigureAwait(false);
            }
        }

        /// <summary>Clones this trigger, carrying every <c>[JsonProperty]</c>.</summary>
        public override object Clone() {
            return new SentinelConditionsTrigger(this) {
                MinStarRatio = MinStarRatio,
                MaxCloudCoverPct = MaxCloudCoverPct,
                MaxFwhmArcsec = MaxFwhmArcsec,
                MaxFwhmPx = MaxFwhmPx,
                MaxStarDropPct = MaxStarDropPct,
                MaxFwhmRisePct = MaxFwhmRisePct,
                MinMoonSeparationDeg = MinMoonSeparationDeg,
                RequireRoofOpen = RequireRoofOpen,
                TreatNoVerdictAsBad = TreatNoVerdictAsBad,
                OnlyBeforeExposures = OnlyBeforeExposures,
                SettleSeconds = SettleSeconds,
                MaxWaitMinutes = MaxWaitMinutes,
                FailOnTimeout = FailOnTimeout,
            };
        }

        /// <summary>Line written to the sequence log.</summary>
        public override string ToString() =>
            $"Category: {Category}, Item: {nameof(SentinelConditionsTrigger)}, {Thresholds().Describe()}, " +
            $"TreatNoVerdictAsBad: {TreatNoVerdictAsBad}, OnlyBeforeExposures: {OnlyBeforeExposures}, " +
            $"SettleSeconds: {SettleSeconds}, MaxWaitMinutes: {MaxWaitMinutes}, FailOnTimeout: {FailOnTimeout}";

        private SentinelConditionsThresholds Thresholds() => new() {
            MinStarRatio = MinStarRatio,
            MaxCloudCoverPct = MaxCloudCoverPct,
            MaxFwhmArcsec = MaxFwhmArcsec,
            MaxFwhmPx = MaxFwhmPx,
            MaxStarDropPct = MaxStarDropPct,
            MaxFwhmRisePct = MaxFwhmRisePct,
            MinMoonSeparationDeg = MinMoonSeparationDeg,
            RequireRoofOpen = RequireRoofOpen,
        };

        private SentinelConditionsVerdict Evaluate(SentinelConditionsReading reading, DateTime nowUtc) =>
            SentinelConditionsEvaluator.Evaluate(reading, Thresholds(), nowUtc);

        private void Show(SentinelConditionsVerdict verdict) => ConditionsSummary = "Sentinel: " + verdict.Summary;

        private void ApplyOverride() =>
            SentinelSequenceLink.Instance.ApplyEndpointOverride(SentinelPluginOptions.ReadBaseUrlOverride(options));

        private static string Clock(TimeSpan span) =>
            span.TotalHours >= 1 ? $"{(int)span.TotalHours}h {span.Minutes:00}m" : $"{(int)span.TotalMinutes}m {span.Seconds:00}s";
    }
}
