#nullable enable
using PFRSentinel.Nina.Client;
using PFRSentinel.NINA.SentinelTarget;
using System;
using System.Collections.Generic;
using System.Globalization;

namespace PFRSentinel.NINA.SentinelDockables {

    /// <summary>
    /// The panel's "All-sky target" column: what the plugin last sent to Sentinel.
    /// </summary>
    /// <remarks>
    /// Read from <see cref="SentinelTargetStatus"/>, which the reporter writes, so the
    /// column shows what actually went out rather than a fresh read of the sequencer
    /// that might not have been sent yet.
    /// </remarks>
    internal static class SentinelTargetRows {

        /// <summary>The rows, and the reason the last send failed (empty when it did not).</summary>
        public static (IReadOnlyList<SentinelStat> Rows, string Problem) Build(
            SentinelTargetStatusSnapshot status, DateTime nowUtc) {

            var rows = new List<SentinelStat>(6) { new("All-sky target", State(status)) };
            SentinelTargetReport? report = status.Report;
            if (report is not null && status.State is SentinelTargetState.Sending or SentinelTargetState.Failing) {
                string name = SentinelTargetPayload.CleanName(report.Name);
                rows.Add(new SentinelStat("Target", name.Length > 0 ? name : SentinelStat.Unknown));
                rows.Add(new SentinelStat("RA / Dec", $"{Ra(report.RaDeg)}  {Dec(report.DecDeg)}"));
                rows.Add(new SentinelStat("Field", Field(report)));
                rows.Add(new SentinelStat("Rotation", report.RotationDeg is double pa
                    ? string.Format(CultureInfo.CurrentCulture, "{0:0.0}°", pa)
                    : SentinelStat.Unknown));
                rows.Add(new SentinelStat("Sent", status.SentAtUtc is DateTime at
                    ? $"{SentinelPanelText.Duration((int)Math.Max(0, (nowUtc - at).TotalSeconds))} ago"
                    : "not yet"));
            }

            string problem = status.State == SentinelTargetState.Failing ? status.Problem ?? string.Empty : string.Empty;
            return (rows, problem.Length > 0 ? char.ToUpperInvariant(problem[0]) + problem.Substring(1) : problem);
        }

        private static string State(SentinelTargetStatusSnapshot status) => status.State switch {
            SentinelTargetState.Starting => "starting…",
            SentinelTargetState.Off => "off (plugin options)",
            SentinelTargetState.Idle => "no target running",
            SentinelTargetState.Sending => "sending",
            SentinelTargetState.Failing => "not reaching Sentinel",
            _ => SentinelStat.Unknown,
        };

        // No field of view means Sentinel draws a reticle; say so, it is the visible effect.
        private static string Field(SentinelTargetReport report) =>
            report.FovWidthDeg is double w && report.FovHeightDeg is double h
                ? string.Format(CultureInfo.CurrentCulture, "{0:0.00}° × {1:0.00}°", w, h)
                : "none (reticle)";

        internal static string Ra(double raDeg) {
            double hours = ((raDeg % 360.0) + 360.0) % 360.0 / 15.0;
            int totalSeconds = (int)Math.Round(hours * 3600.0) % (24 * 3600);
            return string.Format(CultureInfo.InvariantCulture, "{0:00}h {1:00}m {2:00}s",
                totalSeconds / 3600, totalSeconds / 60 % 60, totalSeconds % 60);
        }

        internal static string Dec(double decDeg) {
            int totalSeconds = (int)Math.Round(Math.Abs(decDeg) * 3600.0);
            return string.Format(CultureInfo.InvariantCulture, "{0}{1:00}° {2:00}′ {3:00}″",
                decDeg < 0 ? "−" : "+", totalSeconds / 3600, totalSeconds / 60 % 60, totalSeconds % 60);
        }
    }
}
