#nullable enable
using PFRSentinel.Nina.Client;
using System;
using System.Collections.Generic;
using System.Globalization;

namespace PFRSentinel.NINA.SentinelSequenceItems {

    /// <summary>The operator's own limits on top of Sentinel's verdict. Every one is optional.</summary>
    /// <remarks>
    /// Null means "not used": the item then follows Sentinel's <c>imaging_ok</c> alone.
    /// A limit can only make the verdict stricter; none can turn Sentinel's "not OK" into OK.
    /// </remarks>
    internal sealed class SentinelConditionsThresholds {

        /// <summary>Lowest acceptable star count / clear-sky baseline, e.g. 0.5.</summary>
        public double? MinStarRatio { get; init; }

        /// <summary>Highest acceptable cloud cover, percent.</summary>
        public double? MaxCloudCoverPct { get; init; }

        /// <summary>Highest acceptable FWHM on the pier camera, arcseconds.</summary>
        public double? MaxFwhmArcsec { get; init; }

        /// <summary>Highest acceptable FWHM on the pier camera, pixels.</summary>
        public double? MaxFwhmPx { get; init; }

        /// <summary>
        /// Largest acceptable drop in star count against recent frames, percent, as a
        /// positive number: 40 is crossed when <c>stars.change_pct</c> is −40 or lower.
        /// </summary>
        public double? MaxStarDropPct { get; init; }

        /// <summary>Largest acceptable FWHM rise against the night median, percent: crossed at +Y or higher.</summary>
        public double? MaxFwhmRisePct { get; init; }

        /// <summary>Require the roof to read Open, whatever Sentinel's own setting.</summary>
        public bool RequireRoofOpen { get; init; }

        /// <summary>The limits for a sequence-log line; "none" for an unset one.</summary>
        public string Describe() =>
            string.Format(CultureInfo.InvariantCulture,
                "MinStarRatio: {0}, MaxCloudCoverPct: {1}, MaxFwhmArcsec: {2}, MaxFwhmPx: {3}, MaxStarDropPct: {4}, MaxFwhmRisePct: {5}, RequireRoofOpen: {6}",
                Show(MinStarRatio), Show(MaxCloudCoverPct), Show(MaxFwhmArcsec), Show(MaxFwhmPx),
                Show(MaxStarDropPct), Show(MaxFwhmRisePct), RequireRoofOpen);

        private static string Show(double? value) =>
            value is double v ? v.ToString("0.###", CultureInfo.InvariantCulture) : "none";
    }

    /// <summary>What a verdict means for imaging.</summary>
    internal enum SentinelConditionsState {

        /// <summary>Sentinel says OK and every limit holds.</summary>
        Ok,

        /// <summary>Sentinel says not OK for a measured reason, or a limit is crossed.</summary>
        Bad,

        /// <summary>
        /// Nothing is known: Sentinel unreachable, rejected the request, stale, warming
        /// up or switched off. The item's "treat no verdict as bad" setting decides.
        /// </summary>
        NoVerdict,
    }

    /// <summary>An evaluated verdict, with every reason in plain words.</summary>
    internal sealed class SentinelConditionsVerdict {

        /// <summary>Creates a verdict.</summary>
        public SentinelConditionsVerdict(SentinelConditionsState state, IReadOnlyList<string> reasons, IReadOnlyList<string> notes) {
            State = state;
            Reasons = reasons;
            Notes = notes;
        }

        /// <summary>Ok, Bad or NoVerdict.</summary>
        public SentinelConditionsState State { get; }

        /// <summary>Why it is Bad or NoVerdict. Empty when Ok.</summary>
        public IReadOnlyList<string> Reasons { get; }

        /// <summary>Limits that could not be applied, e.g. while the baseline is learned. Informational.</summary>
        public IReadOnlyList<string> Notes { get; }

        /// <summary>Whether imaging may go ahead under the given policy for "no verdict".</summary>
        public bool AllowsImaging(bool treatNoVerdictAsBad) =>
            State == SentinelConditionsState.Ok || (State == SentinelConditionsState.NoVerdict && !treatNoVerdictAsBad);

        /// <summary>One line: "OK to image", "Not OK: …", "No verdict: …", plus notes.</summary>
        public string Summary {
            get {
                string head = State switch {
                    SentinelConditionsState.Ok => "OK to image",
                    SentinelConditionsState.Bad => "Not OK: " + string.Join("; ", Reasons),
                    _ => "No verdict: " + string.Join("; ", Reasons),
                };

                return Notes.Count > 0 ? head + ". " + string.Join(". ", Notes) : head;
            }
        }
    }

    /// <summary>
    /// Turns a <see cref="SentinelConditionsReading"/> and the operator's limits into a verdict.
    /// </summary>
    /// <remarks>
    /// Pure, NINA-free and clock-injected, so the whole decision can be unit-tested. Rules:
    /// <list type="number">
    /// <item><description>No reading, a failed read, or one older than <see cref="MaxReadingAge"/> is NoVerdict.</description></item>
    /// <item><description>Sentinel off, stale, or "not OK" only for warming_up / stale / disabled is NoVerdict.</description></item>
    /// <item><description>Sentinel "not OK" for any measured reason is Bad.</description></item>
    /// <item><description>Each limit set and crossed adds a Bad reason; a limit whose measurement is null is skipped with a note, never treated as crossed - except Require roof Open, where an Unknown roof is not Open.</description></item>
    /// <item><description>While <c>baseline_status</c> reads learning, the star-ratio and cloud-cover limits are skipped with a note; the change-% limits still apply.</description></item>
    /// </list>
    /// </remarks>
    internal static class SentinelConditionsEvaluator {

        /// <summary>A reading older than this says nothing about the sky now.</summary>
        public static readonly TimeSpan MaxReadingAge = TimeSpan.FromSeconds(60);

        private static readonly IReadOnlyList<string> None = Array.Empty<string>();

        /// <summary>Evaluates one reading.</summary>
        public static SentinelConditionsVerdict Evaluate(
            SentinelConditionsReading reading,
            SentinelConditionsThresholds thresholds,
            DateTime nowUtc) {

            switch (reading.Kind) {
                case SentinelConditionsReadingKind.NotYet:
                    return NoVerdict("waiting for the first answer from PFR Sentinel");
                case SentinelConditionsReadingKind.Failed:
                    return NoVerdict(reading.Problem);
            }

            TimeSpan age = nowUtc - reading.ReadAtUtc;
            if (age > MaxReadingAge) {
                return NoVerdict($"no answer from PFR Sentinel for {(int)age.TotalSeconds}s");
            }

            SentinelConditions c = reading.Conditions!;
            if (c.HasNoVerdict) {
                if (!c.Enabled) {
                    return NoVerdict(SentinelConditionsText.Reason(SentinelConditionReasons.Disabled, c));
                }

                if (c.Stale) {
                    return NoVerdict(SentinelConditionsText.Reason(SentinelConditionReasons.Stale, c));
                }

                return new SentinelConditionsVerdict(SentinelConditionsState.NoVerdict, Phrases(c), None);
            }

            var reasons = new List<string>();
            var notes = new List<string>();

            if (!c.ImagingOk) {
                foreach (string code in c.Reasons) {
                    if (!SentinelConditionReasons.IsNoVerdict(code)) {
                        reasons.Add(SentinelConditionsText.Reason(code, c));
                    }
                }

                if (reasons.Count == 0) {
                    reasons.Add("Sentinel says imaging is not OK");
                }
            }

            ApplyLimits(c, thresholds, reasons, notes);

            return new SentinelConditionsVerdict(
                reasons.Count > 0 ? SentinelConditionsState.Bad : SentinelConditionsState.Ok,
                reasons, notes);
        }

        private static void ApplyLimits(SentinelConditions c, SentinelConditionsThresholds t, List<string> reasons, List<string> notes) {
            bool sentinelSaysRoof = Contains(c, SentinelConditionReasons.RoofNotOpen);
            if (t.RequireRoofOpen && !c.Roof.IsOpen && !sentinelSaysRoof) {
                reasons.Add($"the roof reads {c.Roof.State}, and this item requires Open");
            }

            bool learning = c.BaselineLearning;
            if (learning && (t.MinStarRatio is not null || t.MaxCloudCoverPct is not null)) {
                notes.Add(SentinelConditionsText.Learning(c.BaselineStatus!));
            }

            if (!learning) {
                Below(t.MinStarRatio, c.Stars.Ratio, "star ratio", "0.00", "×", reasons, notes);
                Above(t.MaxCloudCoverPct, c.CloudCoverPct, "cloud cover", "0", "%", reasons, notes);
            }

            Above(t.MaxFwhmArcsec, c.Fwhm.Arcsec, "FWHM", "0.0", "″", reasons, notes);
            Above(t.MaxFwhmPx, c.Fwhm.Px, "FWHM", "0.0", " px", reasons, notes);

            if (t.MaxStarDropPct is double drop) {
                if (c.Stars.ChangePct is double change) {
                    if (change <= -drop) {
                        reasons.Add($"star count {SentinelConditionsText.SignedPct(change)} against recent frames, beyond your −{N(drop, "0")}% limit");
                    }
                } else {
                    notes.Add("star count change not measured; your drop limit is not used");
                }
            }

            if (t.MaxFwhmRisePct is double rise) {
                if (c.Fwhm.ChangePct is double change) {
                    if (change >= rise) {
                        reasons.Add($"FWHM {SentinelConditionsText.SignedPct(change)} against the night median, beyond your +{N(rise, "0")}% limit");
                    }
                } else {
                    notes.Add("FWHM change not measured; your rise limit is not used");
                }
            }
        }

        private static void Below(double? limit, double? value, string what, string format, string unit, List<string> reasons, List<string> notes) {
            if (limit is not double min) {
                return;
            }

            if (value is not double v) {
                notes.Add($"{what} not measured; your limit is not used");
            } else if (v < min) {
                reasons.Add($"{what} {N(v, format)}{unit} is below your {N(min, format)}{unit}");
            }
        }

        private static void Above(double? limit, double? value, string what, string format, string unit, List<string> reasons, List<string> notes) {
            if (limit is not double max) {
                return;
            }

            if (value is not double v) {
                notes.Add($"{what} not measured; your {N(max, format)}{unit} limit is not used");
            } else if (v > max) {
                reasons.Add($"{what} {N(v, format)}{unit} is above your {N(max, format)}{unit}");
            }
        }

        private static bool Contains(SentinelConditions c, string code) {
            foreach (string reason in c.Reasons) {
                if (reason == code) {
                    return true;
                }
            }

            return false;
        }

        private static IReadOnlyList<string> Phrases(SentinelConditions c) {
            var list = new List<string>(c.Reasons.Count);
            foreach (string code in c.Reasons) {
                list.Add(SentinelConditionsText.Reason(code, c));
            }

            return list;
        }

        private static SentinelConditionsVerdict NoVerdict(string reason) =>
            new(SentinelConditionsState.NoVerdict, new[] { reason }, None);

        private static string N(double value, string format) => value.ToString(format, CultureInfo.CurrentCulture);
    }
}
