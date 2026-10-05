using NINA.Core.Utility;
using NINA.Equipment.Interfaces.Mediator;
using NINA.Plugin;
using NINA.Plugin.Interfaces;
using NINA.Profile;
using NINA.Profile.Interfaces;
using NINA.Sequencer.Interfaces.Mediator;
using PFRSentinel.NINA.SentinelTarget;
using System;
using System.ComponentModel;
using System.ComponentModel.Composition;
using System.Runtime.CompilerServices;
using System.Threading.Tasks;
using Settings = PFRSentinel.NINA.Properties.Settings;

namespace PFRSentinel.NINA {

    /// <summary>
    /// Exports IPluginManifest. PluginBase populates the manifest metadata from the
    /// AssemblyInfo attributes, so the plugin list entry is driven entirely by
    /// Properties/AssemblyInfo.cs.
    ///
    /// An instance of this class is the DataContext of the plugin's options page. The
    /// DataTemplate for that page must be keyed "&lt;AssemblyTitle&gt;_Options" -
    /// "PFR Sentinel_Options" - see Options.xaml.
    ///
    /// It also hosts the target push (<see cref="SentinelTargetReporter"/>), started from
    /// <see cref="Initialize"/> and disposed in <see cref="Teardown"/>, so the push runs
    /// whether or not the panel is open - unlike the dockable, which NINA may never
    /// construct for an operator who never opens it.
    /// </summary>
    [Export(typeof(IPluginManifest))]
    public class Sentinel : PluginBase, INotifyPropertyChanged {
        private readonly IPluginOptionsAccessor pluginSettings;
        private readonly IProfileService profileService;
        private readonly ISequenceMediator sequenceMediator;
        private readonly ICameraMediator cameraMediator;
        private SentinelTargetReporter targetReporter;

        // AllowDefault on the mediators: if MEF cannot satisfy a required import it drops
        // the whole manifest export silently - no options page, no plugin-list entry - for
        // the sake of an optional feature. A missing mediator arrives as null instead and
        // NinaTargetSource reports "unknown", so nothing is sent.
        [ImportingConstructor]
        public Sentinel(
            IProfileService profileService,
            [Import(AllowDefault = true)] ISequenceMediator sequenceMediator,
            [Import(AllowDefault = true)] ICameraMediator cameraMediator) {
            if (Settings.Default.UpdateSettings) {
                Settings.Default.Upgrade();
                Settings.Default.UpdateSettings = false;
                CoreUtil.SaveSettings(Settings.Default);
            }

            // Identifier comes from the assembly [Guid]. Profile-scoped plugin settings
            // are keyed off it, which is why that GUID must never change. Built through
            // the shared helper so the dockable panel, which has to reach the same
            // bucket from a separate MEF export, cannot end up reading a different one.
            this.pluginSettings = SentinelPluginOptions.Accessor(profileService);
            this.profileService = profileService;
            profileService.ProfileChanged += ProfileService_ProfileChanged;

            this.sequenceMediator = sequenceMediator;
            this.cameraMediator = cameraMediator;
        }

        /// <summary>Called by NINA's plugin loader right after composition.</summary>
        /// <remarks>
        /// The reporter starts here rather than in the constructor so nothing runs
        /// before NINA has finished building the plugin, and so a failure cannot reach
        /// MEF at all.
        /// </remarks>
        public override Task Initialize() {
            if (targetReporter is null) {
                targetReporter = StartTargetReporter();
            }

            return base.Initialize();
        }

        public override Task Teardown() {
            // Unhook or the plugin instance is never collected.
            profileService.ProfileChanged -= ProfileService_ProfileChanged;
            targetReporter?.Dispose();
            targetReporter = null;
            return base.Teardown();
        }

        private void ProfileService_ProfileChanged(object sender, EventArgs e) {
            RaisePropertyChanged(nameof(BaseUrlOverride));
            RaisePropertyChanged(nameof(ReportTargetToSentinel));
            targetReporter?.RequestSendNow();
        }

        /// <summary>
        /// Builds and starts the target push. Returns null when it could not start.
        /// </summary>
        /// <remarks>
        /// Guarded so an optional feature can never fault plugin start-up.
        /// </remarks>
        private SentinelTargetReporter StartTargetReporter() {
            try {
                var source = new NinaTargetSource(sequenceMediator, cameraMediator, profileService);
                var reporter = new SentinelTargetReporter(
                    source.TrySnapshot,
                    () => SentinelPluginOptions.ReadReportTarget(pluginSettings),
                    () => SentinelPluginOptions.ReadBaseUrlOverride(pluginSettings));
                reporter.Start();
                return reporter;
            } catch (Exception ex) {
                Logger.Warning($"PFR Sentinel: the target push could not start ({ex.GetType().Name}: {ex.Message}).");
                return null;
            }
        }

        /// <summary>
        /// Where the dockable panel looks for Sentinel. Empty means "discover host and
        /// port from Sentinel's own config.json", which is what a local install wants.
        /// </summary>
        /// <remarks>
        /// <para>
        /// The panel re-reads this on every poll, so an edit takes effect within a few
        /// seconds without restarting NINA. A value that is not a valid http(s) address
        /// is reported by the panel as a configuration problem naming the bad value; it
        /// is never sent to the network.
        /// </para>
        /// <para>
        /// This overrides the address only. The control token still comes from the local
        /// config.json, so pointing at Sentinel on another machine gives you its frames
        /// and health but not Start/Stop, unless that machine's token happens to match.
        /// </para>
        /// </remarks>
        public string BaseUrlOverride {
            get => pluginSettings.GetValueString(SentinelPluginOptions.BaseUrlOverrideKey, string.Empty);
            set {
                pluginSettings.SetValueString(SentinelPluginOptions.BaseUrlOverrideKey, value ?? string.Empty);
                RaisePropertyChanged();
                targetReporter?.RequestSendNow();
            }
        }

        /// <summary>
        /// Whether the sequencer's running target is sent to Sentinel for its all-sky
        /// overlay. On by default.
        /// </summary>
        /// <remarks>
        /// Profile-scoped like <see cref="BaseUrlOverride"/>. The reporter re-reads it every
        /// tick; turning it off sends Sentinel one clear and then nothing.
        /// </remarks>
        public bool ReportTargetToSentinel {
            get => SentinelPluginOptions.ReadReportTarget(pluginSettings);
            set {
                SentinelPluginOptions.WriteReportTarget(pluginSettings, value);
                RaisePropertyChanged();
                targetReporter?.RequestSendNow();
            }
        }

        public event PropertyChangedEventHandler PropertyChanged;

        protected void RaisePropertyChanged([CallerMemberName] string propertyName = null) {
            this.PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(propertyName));
        }
    }
}
