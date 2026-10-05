#nullable enable
using NINA.Astrometry;
using NINA.Core.Enum;
using NINA.Core.Utility;
using NINA.Equipment.Interfaces.Mediator;
using NINA.Profile.Interfaces;
using NINA.Sequencer.Container;
using NINA.Sequencer.Interfaces.Mediator;
using PFRSentinel.Nina.Client;
using System;
using System.Collections.Generic;
using System.Runtime.ExceptionServices;

namespace PFRSentinel.NINA.SentinelTarget {

    /// <summary>
    /// Reads the target NINA's sequencer is imaging right now, plus the imaging
    /// camera's field of view, as a <see cref="SentinelTargetReport"/>.
    /// </summary>
    /// <remarks>
    /// <para>
    /// The only NINA-facing half of the target push; <see cref="SentinelTargetReporter"/>
    /// sees nothing but the report this produces, so the network side stays free of NINA
    /// types like the panel's poller.
    /// </para>
    /// <para>
    /// <b>Never throws.</b> It runs on the reporter's background thread and walks a
    /// sequence tree the UI thread may be editing, so a "collection was modified" is a
    /// routine event, not a bug. A failed read repeats the last good answer for a few
    /// ticks rather than reporting "no target", which would send Sentinel a clear — the
    /// marker would blink off because of a race.
    /// </para>
    /// <para>
    /// Three answers, not two: a target, no target (send one clear), or
    /// <see cref="SentinelTargetReading.Unknown"/> — the sequencer is not initialised yet,
    /// or NINA supplied no sequence mediator — which sends nothing at all.
    /// </para>
    /// <para>
    /// Member names were checked against NINA's public source (isbeorn/nina, develop);
    /// the Windows CI build compiles against <c>NINA.Plugin 3.2.0.9001</c>.
    /// </para>
    /// </remarks>
    internal sealed class NinaTargetSource {

        // Faults 1 and 2 repeat the last good answer; fault 3 reports "no target". At
        // the reporter's 10 s tick that is about 20 s of tolerance, well inside
        // Sentinel's 120 s stale-out.
        private const int FaultTolerance = 3;

        private readonly ISequenceMediator? sequenceMediator;
        private readonly ICameraMediator? cameraMediator;
        private readonly IProfileService? profileService;

        private SentinelTargetReport? lastGood;
        private int consecutiveFaults;
        private string? lastLogged;

        public NinaTargetSource(
            ISequenceMediator? sequenceMediator,
            ICameraMediator? cameraMediator,
            IProfileService? profileService) {

            this.sequenceMediator = sequenceMediator;
            this.cameraMediator = cameraMediator;
            this.profileService = profileService;
        }

        /// <summary>
        /// The running target, "no target" when no deep-sky container is running, or
        /// unknown while the sequencer cannot be asked.
        /// </summary>
        /// <remarks>Reporter thread only; not re-entrant.</remarks>
        public SentinelTargetReading TrySnapshot() {
            if (sequenceMediator is null) {
                LogOnce("PFR Sentinel: NINA supplied no sequence mediator, so the target is not sent to Sentinel.");
                return SentinelTargetReading.Unknown;
            }

            try {
                // Most mediator calls throw SequenceMediatorException until the
                // sequencer has registered; asking early is "unknown", not "no target".
                if (!sequenceMediator.Initialized) {
                    return SentinelTargetReading.Unknown;
                }

                SentinelTargetReport? report = ReadRunningTarget();
                lastGood = report;
                consecutiveFaults = 0;
                return SentinelTargetReading.Of(report);
            } catch (Exception ex) {
                LogOnce($"PFR Sentinel: could not read the sequencer target ({ex.GetType().Name}: {ex.Message}).");
                consecutiveFaults++;
                return SentinelTargetReading.Of(consecutiveFaults < FaultTolerance ? lastGood : null);
            }
        }

        /// <summary>
        /// Field of view in degrees across <paramref name="pixels"/> pixels, or null when
        /// any input is missing.
        /// </summary>
        /// <remarks>
        /// <c>2·atan(n·pixel/2/focal)</c>, pixel size in µm and focal length in mm.
        /// Binning does not change the field, so unbinned sensor size is the right input.
        /// </remarks>
        internal static double? FieldDegrees(int pixels, double pixelSizeUm, double focalLengthMm) {
            if (pixels <= 0 || !IsPositive(pixelSizeUm) || !IsPositive(focalLengthMm)) {
                return null;
            }

            double sensorMm = pixels * pixelSizeUm / 1000.0;
            return 2.0 * Math.Atan(sensorMm / 2.0 / focalLengthMm) * 180.0 / Math.PI;
        }

        private SentinelTargetReport? ReadRunningTarget() {
            IDeepSkyObjectContainer? container = FindRunningContainer();
            if (container is null) {
                return null;
            }

            var target = container.Target;
            if (target is null) {
                return null;
            }

            // NINA keeps targets in J2000 already; the transform makes the epoch explicit
            // rather than assumed.
            var coordinates = target.InputCoordinates?.Coordinates;
            if (coordinates is null) {
                return null;
            }

            var j2000 = coordinates.Transform(Epoch.J2000);
            double ra = j2000.RADegrees;
            double dec = j2000.Dec;

            string name = (target.TargetName ?? string.Empty).Trim();

            // A freshly dropped container with nothing filled in has an empty name at
            // RA 0 / Dec 0. That is not a target anyone is imaging.
            if (name.Length == 0 && ra == 0.0 && dec == 0.0) {
                return null;
            }

            // Standard astronomical position angle, degrees east of north, in [0, 360).
            // NINA 3 migrated the 2.x Rotation as 360 - Rotation, so this is not that.
            double positionAngle = target.PositionAngle;

            (double? fovWidth, double? fovHeight) = ReadFieldOfView();

            return new SentinelTargetReport(
                name,
                ra,
                dec,
                fovWidth,
                fovHeight,
                double.IsFinite(positionAngle) ? (double?)positionAngle : null);
        }

        private IDeepSkyObjectContainer? FindRunningContainer() {
            if (sequenceMediator is null) {
                return null;
            }

            // Advanced first: it is what a modern NINA night runs.
            IDeepSkyObjectContainer? running = null;
            Exception? advancedFault = null;
            try {
                running = LastRunning(sequenceMediator.GetAllTargetsInAdvancedSequence());
            } catch (Exception ex) {
                advancedFault = ex;
            }

            if (running is not null) {
                return running;
            }

            running = LastRunning(sequenceMediator.GetAllTargetsInSimpleSequence());

            // A faulted advanced read with nothing running in the simple sequence is
            // "unknown", not "no target": rethrow so TrySnapshot keeps the last answer
            // instead of sending Sentinel a clear.
            if (running is null && advancedFault is not null) {
                ExceptionDispatchInfo.Capture(advancedFault).Throw();
            }

            return running;
        }

        /// <summary>
        /// The last running container in tree order — the innermost when targets are nested.
        /// </summary>
        private static IDeepSkyObjectContainer? LastRunning(IEnumerable<IDeepSkyObjectContainer>? containers) {
            if (containers is null) {
                return null;
            }

            IDeepSkyObjectContainer? found = null;
            foreach (IDeepSkyObjectContainer? container in containers) {
                if (container is not null && container.Status == SequenceEntityStatus.RUNNING) {
                    found = container;
                }
            }

            return found;
        }

        /// <summary>
        /// The imaging camera's field of view, width and height, or (null, null).
        /// </summary>
        /// <remarks>
        /// Live camera first — it knows the real sensor — then the profile's pixel size
        /// and the framing assistant's camera dimensions, so a target still gets a box
        /// before the camera connects. Both or neither: Sentinel rejects one alone.
        /// A failure here costs the box, never the target.
        /// </remarks>
        private (double? Width, double? Height) ReadFieldOfView() {
            try {
                var profile = profileService?.ActiveProfile;
                if (profile is null) {
                    return (null, null);
                }

                // Millimetres; NaN or 0 when unset.
                double focal = profile.TelescopeSettings.FocalLength;

                int width = 0;
                int height = 0;
                double pixel = 0.0;

                // XSize / YSize are unbinned pixels, PixelSize is µm.
                var info = cameraMediator?.GetInfo();
                if (info is not null && info.Connected && info.XSize > 0 && info.YSize > 0 && IsPositive(info.PixelSize)) {
                    width = info.XSize;
                    height = info.YSize;
                    pixel = info.PixelSize;
                } else {
                    width = profile.FramingAssistantSettings.CameraWidth;
                    height = profile.FramingAssistantSettings.CameraHeight;
                    pixel = profile.CameraSettings.PixelSize;
                }

                double? fovWidth = FieldDegrees(width, pixel, focal);
                double? fovHeight = FieldDegrees(height, pixel, focal);
                if (fovWidth is null || fovHeight is null) {
                    return (null, null);
                }

                return (fovWidth, fovHeight);
            } catch (Exception ex) {
                LogOnce($"PFR Sentinel: could not work out the camera field of view ({ex.GetType().Name}: {ex.Message}).");
                return (null, null);
            }
        }

        private static bool IsPositive(double value) => double.IsFinite(value) && value > 0.0;

        private void LogOnce(string message) {
            if (string.Equals(message, lastLogged, StringComparison.Ordinal)) {
                return;
            }

            lastLogged = message;
            Logger.Debug(message);
        }
    }
}
