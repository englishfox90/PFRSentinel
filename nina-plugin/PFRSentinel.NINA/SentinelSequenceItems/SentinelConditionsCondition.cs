#nullable enable
using Newtonsoft.Json;
using NINA.Core.Utility;
using NINA.Profile.Interfaces;
using NINA.Sequencer.Conditions;
using NINA.Sequencer.SequenceItem;
using NINA.Sequencer.Validations;
using System;
using System.Collections.Generic;
using System.ComponentModel.Composition;

namespace PFRSentinel.NINA.SentinelSequenceItems {

    /// <summary>
    /// Sequencer loop condition: the container keeps looping while PFR Sentinel says the sky
    /// is good enough to image, and ends once it is not.
    /// </summary>
    /// <remarks>
    /// <para>
    /// Namespace and type name are load-bearing forever; see
    /// <see cref="SentinelConditionsTrigger"/> for why the pair is named this way.
    /// </para>
    /// <para>
    /// Same limits and the same "no verdict" policy as the trigger, evaluated by the same
    /// <see cref="SentinelConditionsEvaluator"/>, so the two can never disagree about a sky.
    /// Like NINA's own loop conditions it is checked between items; it does not interrupt
    /// an exposure that is already running.
    /// </para>
    /// <para>
    /// Validation issues cover this item's settings only: <c>SequenceCondition.RunCheck</c>
    /// fails the condition on any issue, which would end the loop for a reason the
    /// operator's "no verdict" setting is meant to decide.
    /// </para>
    /// </remarks>
    [ExportMetadata("Name", "Sentinel: loop while conditions OK")]
    [ExportMetadata("Description", "Keeps the container looping while PFR Sentinel says the sky is good enough to image, with optional limits of your own. Ends the loop once it is not.")]
    [ExportMetadata("Icon", "PFRSentinel_LoopSVG")]
    [ExportMetadata("Category", "PFR Sentinel")]
    [Export(typeof(ISequenceCondition))]
    [JsonObject(MemberSerialization.OptIn)]
    public class SentinelConditionsCondition : SequenceCondition, IValidatable {

        private readonly IProfileService? profileService;
        private readonly IPluginOptionsAccessor? options;

        private IList<string> issues = new List<string>();
        private string conditionsSummary = "Sentinel: waiting for the first answer";
        private bool lastAllowed = true;
        private double? minStarRatio;
        private double? maxCloudCoverPct;
        private double? maxFwhmArcsec;
        private double? maxFwhmPx;
        private double? maxStarDropPct;
        private double? maxFwhmRisePct;
        private bool requireRoofOpen;
        private bool treatNoVerdictAsBad = true;

        /// <summary>Constructed by MEF for the condition factory.</summary>
        [ImportingConstructor]
        public SentinelConditionsCondition(IProfileService profileService) {
            this.profileService = profileService;
            options = profileService is null ? null : SentinelPluginOptions.Accessor(profileService);
        }

        /// <summary>Copy constructor backing <see cref="Clone"/>.</summary>
        public SentinelConditionsCondition(SentinelConditionsCondition copyMe) : this(copyMe.profileService!) {
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

        /// <summary>Largest star-count drop against recent frames, percent; null = not used.</summary>
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

        /// <summary>End the loop unless the roof reads Open.</summary>
        [JsonProperty]
        public bool RequireRoofOpen {
            get => requireRoofOpen;
            set { requireRoofOpen = value; RaisePropertyChanged(nameof(RequireRoofOpen)); }
        }

        /// <summary>End the loop when Sentinel gives no verdict. On by default.</summary>
        [JsonProperty]
        public bool TreatNoVerdictAsBad {
            get => treatNoVerdictAsBad;
            set { treatNoVerdictAsBad = value; RaisePropertyChanged(nameof(TreatNoVerdictAsBad)); }
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
            Issues = new List<string>();
            return true;
        }

        /// <summary>True while conditions allow imaging.</summary>
        /// <remarks>
        /// Called synchronously between items; blocks for one bounded refresh only when the
        /// cache is old, never on the UI thread.
        /// </remarks>
        public override bool Check(ISequenceItem previousItem, ISequenceItem nextItem) {
            bool allowed;
            string summary;
            try {
                ApplyOverride();
                SentinelConditionsReading reading = SentinelConditionsSource.Instance.GetFresh(
                    SentinelConditionsSettings.FreshFor,
                    SentinelConditionsSettings.CheckWaitBudget,
                    mayBlock: !SentinelConditionsSettings.OnUiThread());

                SentinelConditionsVerdict verdict = Evaluate(reading, DateTime.UtcNow);
                Show(verdict);
                allowed = verdict.AllowsImaging(TreatNoVerdictAsBad);
                summary = verdict.Summary;
            } catch (Exception ex) {
                allowed = !TreatNoVerdictAsBad;
                summary = $"the conditions check failed ({ex.GetType().Name}: {ex.Message})";
            }

            if (allowed != lastAllowed) {
                Logger.Info(allowed
                    ? $"PFR Sentinel: conditions OK, the loop continues - {summary}"
                    : $"PFR Sentinel: ending the loop - {summary}");
                lastAllowed = allowed;
            }

            return allowed;
        }

        /// <summary>Clones this condition, carrying every <c>[JsonProperty]</c>.</summary>
        public override object Clone() {
            return new SentinelConditionsCondition(this) {
                MinStarRatio = MinStarRatio,
                MaxCloudCoverPct = MaxCloudCoverPct,
                MaxFwhmArcsec = MaxFwhmArcsec,
                MaxFwhmPx = MaxFwhmPx,
                MaxStarDropPct = MaxStarDropPct,
                MaxFwhmRisePct = MaxFwhmRisePct,
                RequireRoofOpen = RequireRoofOpen,
                TreatNoVerdictAsBad = TreatNoVerdictAsBad,
            };
        }

        /// <summary>Line written to the sequence log.</summary>
        public override string ToString() =>
            $"Condition: {nameof(SentinelConditionsCondition)}, {Thresholds().Describe()}, TreatNoVerdictAsBad: {TreatNoVerdictAsBad}";

        private SentinelConditionsThresholds Thresholds() => new() {
            MinStarRatio = MinStarRatio,
            MaxCloudCoverPct = MaxCloudCoverPct,
            MaxFwhmArcsec = MaxFwhmArcsec,
            MaxFwhmPx = MaxFwhmPx,
            MaxStarDropPct = MaxStarDropPct,
            MaxFwhmRisePct = MaxFwhmRisePct,
            RequireRoofOpen = RequireRoofOpen,
        };

        private SentinelConditionsVerdict Evaluate(SentinelConditionsReading reading, DateTime nowUtc) =>
            SentinelConditionsEvaluator.Evaluate(reading, Thresholds(), nowUtc);

        private void Show(SentinelConditionsVerdict verdict) => ConditionsSummary = "Sentinel: " + verdict.Summary;

        private void ApplyOverride() =>
            SentinelSequenceLink.Instance.ApplyEndpointOverride(SentinelPluginOptions.ReadBaseUrlOverride(options));
    }
}
