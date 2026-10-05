#nullable enable
using NINA.Profile;
using NINA.Profile.Interfaces;
using System;
using System.Reflection;
using System.Runtime.InteropServices;

namespace PFRSentinel.NINA {

    /// <summary>
    /// Shared access to the plugin's profile-scoped settings.
    /// </summary>
    /// <remarks>
    /// <para>
    /// The options page and the dockable panel are separate MEF exports with no
    /// reference to each other, so both have to reach the same settings bucket
    /// independently. That bucket is keyed on the plugin GUID, and the setting on its
    /// name — get either wrong and the panel silently reads a different, always-empty
    /// value while the options page happily saves to the real one.
    /// </para>
    /// <para>
    /// <see cref="PluginId"/> resolves the GUID exactly the way NINA's own
    /// <c>PluginBase.Identifier</c> does — from the assembly's <see cref="GuidAttribute"/>
    /// — rather than repeating the literal, so the two can never drift.
    /// </para>
    /// </remarks>
    public static class SentinelPluginOptions {

        /// <summary>Setting name for the base URL override. Must match what the options page binds.</summary>
        public const string BaseUrlOverrideKey = "BaseUrlOverride";

        /// <summary>Setting name for the target push switch. Must match what the options page binds.</summary>
        public const string ReportTargetKey = "ReportTargetToSentinel";

        /// <summary>The target push is on until the operator turns it off.</summary>
        public const bool ReportTargetDefault = true;

        private static readonly Guid Id = ResolvePluginId();

        /// <summary>The plugin's identifier, as NINA derives it.</summary>
        public static Guid PluginId => Id;

        /// <summary>Opens this plugin's settings for the active profile.</summary>
        public static IPluginOptionsAccessor Accessor(IProfileService profileService) =>
            new PluginOptionsAccessor(profileService, Id);

        /// <summary>
        /// Reads the base URL override, or an empty string when unset.
        /// </summary>
        /// <remarks>
        /// Never throws: this is read from the poll thread on every cycle, and a
        /// settings read that faults must not be able to stop the panel updating.
        /// </remarks>
        public static string ReadBaseUrlOverride(IPluginOptionsAccessor? accessor) {
            if (accessor is null) {
                return string.Empty;
            }

            try {
                return accessor.GetValueString(BaseUrlOverrideKey, string.Empty) ?? string.Empty;
            } catch (Exception) {
                return string.Empty;
            }
        }

        /// <summary>Reads the target push switch, or its default when unset.</summary>
        /// <remarks>Never throws: the reporter reads this from its loop on every tick.</remarks>
        public static bool ReadReportTarget(IPluginOptionsAccessor? accessor) {
            if (accessor is null) {
                return ReportTargetDefault;
            }

            try {
                return accessor.GetValueBoolean(ReportTargetKey, ReportTargetDefault);
            } catch (Exception) {
                return ReportTargetDefault;
            }
        }

        /// <summary>Saves the target push switch. Returns false when the write failed.</summary>
        /// <remarks>Never throws: it runs from a WPF binding, where an exception is swallowed anyway.</remarks>
        public static bool WriteReportTarget(IPluginOptionsAccessor? accessor, bool value) {
            if (accessor is null) {
                return false;
            }

            try {
                accessor.SetValueBoolean(ReportTargetKey, value);
                return true;
            } catch (Exception) {
                return false;
            }
        }

        private static Guid ResolvePluginId() {
            string? value = typeof(SentinelPluginOptions).Assembly
                .GetCustomAttribute<GuidAttribute>()?.Value;

            return Guid.TryParse(value, out Guid parsed) ? parsed : Guid.Empty;
        }
    }
}
