#nullable enable
using PFRSentinel.Nina.Client;
using PFRSentinel.NINA.SentinelSequenceItems;
using System;
using System.Collections.Generic;

namespace PFRSentinel.NINA.SentinelDockables {

    /// <summary>The panel's "Imaging conditions" column, formatted for a person.</summary>
    internal sealed class SentinelConditionsView {

        /// <summary>The verdict shown in the column header.</summary>
        public SentinelConditionsState State { get; init; } = SentinelConditionsState.NoVerdict;

        /// <summary>"OK to image", "NOT OK" or "NO VERDICT".</summary>
        public string Headline { get; init; } = "NO VERDICT";

        /// <summary>Why, in plain words; empty when OK.</summary>
        public string Reasons { get; init; } = string.Empty;

        /// <summary>The measurements behind it.</summary>
        public IReadOnlyList<SentinelStat> Rows { get; init; } = Array.Empty<SentinelStat>();

        /// <summary>The baseline-learning note, or empty.</summary>
        public string Note { get; init; } = string.Empty;
    }

    /// <summary>
    /// Builds the panel's conditions column from the shared <see cref="SentinelConditionsSource"/>.
    /// </summary>
    /// <remarks>
    /// The verdict is Sentinel's alone: the per-item limits belong to each trigger or
    /// condition and are shown on that item. Every figure is Sentinel's; a null shows as
    /// <see cref="SentinelStat.Unknown"/>, never as a derived number.
    /// </remarks>
    internal static class SentinelConditionsRows {

        private static readonly SentinelConditionsThresholds NoLimits = new();

        /// <summary>Formats one reading.</summary>
        public static SentinelConditionsView Build(SentinelConditionsReading reading, DateTime nowUtc) {
            SentinelConditionsVerdict verdict = SentinelConditionsEvaluator.Evaluate(reading, NoLimits, nowUtc);
            string headline = verdict.State switch {
                SentinelConditionsState.Ok => "OK TO IMAGE",
                SentinelConditionsState.Bad => "NOT OK",
                _ => "NO VERDICT",
            };

            SentinelConditions? c = reading.Kind == SentinelConditionsReadingKind.Answered ? reading.Conditions : null;
            string reasons = Capitalise(string.Join("; ", verdict.Reasons));

            if (c is null) {
                return new SentinelConditionsView { State = verdict.State, Headline = headline, Reasons = reasons };
            }

            var rows = new List<SentinelStat>(10) {
                new("Roof", Roof(c.Roof)),
                new("Sky", Sky(c.Sky)),
                new("Stars", Stars(c.Stars)),
                new("Star change", StarChange(c.Stars)),
                new("Cloud cover", c.CloudCoverPct is double pct
                    ? $"{SentinelConditionsText.Num(pct, "0")}%{Source(c.CloudCoverSource)}"
                    : SentinelStat.Unknown),
                new("FWHM", Fwhm(c.Fwhm)),
                new("Sun", c.SunAltitudeDeg is double alt ? $"{SentinelConditionsText.Num(alt, "0.0")}°" : SentinelStat.Unknown),
                new("Baseline", Baseline(c.BaselineStatus)),
                new("Judged", c.AgeSeconds is double age
                    ? $"{SentinelPanelText.Duration((int)Math.Max(0, age))} ago{(c.Stale ? " (stale)" : string.Empty)}"
                    : "no frame yet"),
            };

            return new SentinelConditionsView {
                State = verdict.State,
                Headline = headline,
                Reasons = reasons,
                Rows = rows,
                Note = c.BaselineLearning ? SentinelConditionsText.Learning(c.BaselineStatus!) + "." : string.Empty,
            };
        }

        private static string Roof(SentinelConditionsRoof roof) =>
            roof.State + Confidence(roof.Confidence);

        private static string Sky(SentinelConditionsSky sky) {
            string text = sky.Condition is { Length: > 0 } condition
                ? condition + Confidence(sky.Confidence)
                : SentinelStat.Unknown;
            return sky.StarsVisible switch {
                true => text + " · stars visible",
                false => text + " · no stars",
                _ => text,
            };
        }

        private static string Stars(SentinelConditionsStars stars) {
            if (stars.Count is not int count) {
                return SentinelStat.Unknown;
            }

            return stars.Ratio is double ratio
                ? $"{count} · {SentinelConditionsText.Num(ratio, "0.00")}× clear sky"
                : count.ToString(System.Globalization.CultureInfo.CurrentCulture);
        }

        private static string StarChange(SentinelConditionsStars stars) {
            double? pct = stars.ChangePct ?? (stars.Trend is double trend ? trend * 100.0 : null);
            if (pct is not double change) {
                return SentinelStat.Unknown;
            }

            string window = stars.WindowFrames is int n ? $"last {n} frames" : "recent frames";
            return $"{SentinelConditionsText.SignedPct(change)} vs {window}";
        }

        private static string Fwhm(SentinelConditionsFwhm fwhm) {
            if (fwhm.Px is not double px) {
                return SentinelStat.Unknown;
            }

            string text = $"{SentinelConditionsText.Num(px, "0.0")} px";
            if (fwhm.Arcsec is double arcsec) {
                text += $" · {SentinelConditionsText.Num(arcsec, "0.0")}″";
            }

            if (fwhm.ChangePct is double change) {
                text += $" · {SentinelConditionsText.SignedPct(change)} vs night";
            } else if (fwhm.NightMedianPx is double median) {
                text += $" · night {SentinelConditionsText.Num(median, "0.0")} px";
            }

            return text;
        }

        private static string Baseline(SentinelBaselineStatus? status) {
            if (status is null || status.State.Length == 0) {
                return SentinelStat.Unknown;
            }

            if (status.IsLearning) {
                return status.Samples is int s && status.Needed is int n ? $"learning ({s}/{n} clear frames)" : "learning";
            }

            return status.State;
        }

        private static string Source(string? source) => source switch {
            "star_baseline" => " (from stars)",
            "ml_class" => " (from sky class)",
            _ => string.Empty,
        };

        private static string Confidence(double? confidence) =>
            confidence is double c ? $" ({SentinelConditionsText.Num(c * 100.0, "0")}%)" : string.Empty;

        private static string Capitalise(string text) =>
            text.Length > 0 ? char.ToUpperInvariant(text[0]) + text.Substring(1) : text;
    }
}
