#nullable enable
using PFRSentinel.Nina.Client;
using System;
using System.Globalization;

namespace PFRSentinel.NINA.SentinelSequenceItems {

    /// <summary>
    /// Sentinel's condition reason codes and measurements in plain words.
    /// </summary>
    /// <remarks>
    /// Pure and NINA-free: the same sentences go to the sequencer status line, NINA's log
    /// and the dockable panel, and the mapping can be exercised without NINA. An unknown
    /// code is still shown - its underscores become spaces - because a reason Sentinel
    /// gave is a reason, even one this build has not heard of.
    /// </remarks>
    internal static class SentinelConditionsText {

        /// <summary>One reason code as a phrase, with the figure behind it where Sentinel sent one.</summary>
        public static string Reason(string code, SentinelConditions? c) {
            string detail = c is null ? string.Empty : Detail(code, c);
            string phrase = code switch {
                SentinelConditionReasons.RoofNotOpen => "the roof is not open",
                SentinelConditionReasons.Daylight => "the sun is too high (daylight or twilight)",
                SentinelConditionReasons.CloudCoverHigh => "cloud cover is high",
                SentinelConditionReasons.StarsLow => "too few stars for a clear sky",
                SentinelConditionReasons.StarsDropping => "the star count is dropping",
                SentinelConditionReasons.FwhmHigh => "stars are bloated (FWHM high)",
                SentinelConditionReasons.FrameStatic => "the pier camera frame is only noise",
                SentinelConditionReasons.SkyUnknown => "the sky could not be classified",
                SentinelConditionReasons.Stale => "Sentinel has not judged a frame recently (capture stalled or stopped)",
                SentinelConditionReasons.WarmingUp => "Sentinel is still gathering its first good frames",
                SentinelConditionReasons.Recovering => "the sky looks good again; Sentinel is waiting for a few good frames in a row",
                SentinelConditionReasons.Disabled => "imaging conditions are switched off in Sentinel",
                _ => code.Replace('_', ' '),
            };

            return detail.Length > 0 ? $"{phrase} ({detail})" : phrase;
        }

        /// <summary>
        /// "Sentinel is learning this sky (12/30 clear frames); star ratio and cloud cover not used yet".
        /// </summary>
        public static string Learning(SentinelBaselineStatus status) {
            string progress = status.Samples is int s && status.Needed is int n
                ? $" ({s}/{n} clear frames)"
                : string.Empty;
            return $"Sentinel is learning this sky{progress}; star ratio and cloud cover not used yet";
        }

        /// <summary>Signed percent, e.g. "−45%" or "+12%".</summary>
        public static string SignedPct(double pct) =>
            (pct < 0 ? "−" : "+") + Math.Abs(pct).ToString("0", CultureInfo.CurrentCulture) + "%";

        /// <summary>Plain number with the culture's separator.</summary>
        public static string Num(double value, string format) => value.ToString(format, CultureInfo.CurrentCulture);

        private static string Detail(string code, SentinelConditions c) => code switch {
            SentinelConditionReasons.RoofNotOpen => $"reads {c.Roof.State}",
            SentinelConditionReasons.Daylight when c.SunAltitudeDeg is double alt =>
                $"sun at {Num(alt, "0.0")}°",
            SentinelConditionReasons.CloudCoverHigh when c.CloudCoverPct is double pct =>
                $"{Num(pct, "0")}%",
            SentinelConditionReasons.StarsLow when c.Stars.Ratio is double ratio =>
                $"{Num(ratio, "0.00")}× the clear-sky count",
            SentinelConditionReasons.StarsDropping when c.Stars.ChangePct is double change =>
                $"{SignedPct(change)} against recent frames",
            SentinelConditionReasons.StarsDropping when c.Stars.Trend is double trend =>
                $"{SignedPct(trend * 100.0)} against recent frames",
            SentinelConditionReasons.FwhmHigh when c.Fwhm.Px is double px =>
                c.Fwhm.NightMedianPx is double median
                    ? $"{Num(px, "0.0")} px, night median {Num(median, "0.0")} px"
                    : $"{Num(px, "0.0")} px",
            _ => string.Empty,
        };
    }
}
