#nullable enable
using System;
using System.Windows;

namespace PFRSentinel.NINA.SentinelSequenceItems {

    /// <summary>
    /// Ranges, defaults and shared plumbing for the conditions trigger and condition.
    /// </summary>
    /// <remarks>
    /// Values are clamped in the setters rather than reported as validation issues, the
    /// same choice <see cref="StartSentinelCapture.TimeoutSeconds"/> makes: a hand-edited
    /// sequence file must not turn into a failed trigger in the middle of the night. Zero
    /// or a negative number in a limit means "not used", like leaving the box empty.
    /// </remarks>
    internal static class SentinelConditionsSettings {

        /// <summary>A cached reading this young is used as is by ShouldTrigger / Check.</summary>
        public static readonly TimeSpan FreshFor = TimeSpan.FromSeconds(15);

        /// <summary>Longest ShouldTrigger / Check blocks for a refresh. Above the link's 8 s probe timeout.</summary>
        public static readonly TimeSpan CheckWaitBudget = TimeSpan.FromSeconds(10);

        /// <summary>How often a running pause looks again.</summary>
        public static readonly TimeSpan PollInterval = TimeSpan.FromSeconds(5);

        /// <summary>Default settle time, seconds.</summary>
        public const int DefaultSettleSeconds = 60;

        /// <summary>Longest settle time, seconds.</summary>
        public const int MaxSettleSeconds = 3600;

        /// <summary>Longest maximum wait, minutes (one day). Zero means no limit.</summary>
        public const int MaxWaitLimitMinutes = 1440;

        /// <summary>Clamps a limit to (0, max]; zero, negative, NaN or null mean "not used".</summary>
        public static double? Limit(double? value, double max) {
            if (value is not double v || double.IsNaN(v) || v <= 0) {
                return null;
            }

            return Math.Min(v, max);
        }

        /// <summary>Clamps a percent limit to (0, 100]; a cloud cover of 0 % stays usable.</summary>
        public static double? Percent(double? value, bool allowZero) {
            if (value is not double v || double.IsNaN(v) || v < 0 || (!allowZero && v == 0)) {
                return null;
            }

            return Math.Min(v, allowZero ? 100.0 : 1000.0);
        }

        /// <summary>
        /// Whether the caller is on NINA's UI thread, where a blocking refresh would freeze the window.
        /// </summary>
        public static bool OnUiThread() {
            try {
                return Application.Current?.Dispatcher?.CheckAccess() == true;
            } catch (Exception) {
                return false;
            }
        }
    }
}
