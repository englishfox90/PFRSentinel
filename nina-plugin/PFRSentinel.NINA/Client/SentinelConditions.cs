#nullable enable
using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text.Json;

namespace PFRSentinel.Nina.Client;

/// <summary>
/// The reason codes Sentinel puts in <see cref="SentinelConditions.Reasons"/>.
/// </summary>
/// <remarks>
/// Mirrors the constants in <c>services/imaging_conditions/frame_rules.py</c> and
/// <c>conditions_store.py</c>. The list is open: Sentinel may add codes, and an unknown
/// one is still a reason imaging is not OK - it is shown in words derived from the code
/// rather than dropped.
/// </remarks>
public static class SentinelConditionReasons
{
    /// <summary>The roof classifier does not read Open.</summary>
    public const string RoofNotOpen = "roof_not_open";

    /// <summary>The sun is above Sentinel's darkness limit.</summary>
    public const string Daylight = "daylight";

    /// <summary>Estimated cloud cover is above Sentinel's limit.</summary>
    public const string CloudCoverHigh = "cloud_cover_high";

    /// <summary>Star count is low against this rig's clear-sky baseline.</summary>
    public const string StarsLow = "stars_low";

    /// <summary>Star count fell sharply against the recent frames.</summary>
    public const string StarsDropping = "stars_dropping";

    /// <summary>Star FWHM is well above the night's median.</summary>
    public const string FwhmHigh = "fwhm_high";

    /// <summary>The pier-camera frame is sensor noise, not sky.</summary>
    public const string FrameStatic = "frame_static";

    /// <summary>Sentinel could not classify the sky.</summary>
    public const string SkyUnknown = "sky_unknown";

    /// <summary>No frame has been judged recently: capture stalled or stopped.</summary>
    public const string Stale = "stale";

    /// <summary>Sentinel has not judged enough frames since it started.</summary>
    public const string WarmingUp = "warming_up";

    /// <summary>Frames are good again; Sentinel waits for enough good frames in a row before saying OK.</summary>
    public const string Recovering = "recovering";

    /// <summary>Imaging conditions are switched off in Sentinel's config.</summary>
    public const string Disabled = "disabled";

    /// <summary>
    /// Whether a code means "Sentinel has no verdict" rather than "the sky is bad".
    /// </summary>
    /// <remarks>
    /// Nothing about the sky has been measured. <see cref="Stale"/> and
    /// <see cref="Disabled"/> are governed by the operator's "treat no verdict as bad"
    /// setting, the same as an unreachable Sentinel; <see cref="WarmingUp"/> alone is
    /// allowed by the evaluator, because Sentinel has not yet said anything is wrong.
    /// <see cref="Recovering"/> is deliberately not one of them: the sky has been
    /// measured, and it is Sentinel's own hysteresis still saying "not yet".
    /// </remarks>
    public static bool IsNoVerdict(string code) =>
        code is Stale or WarmingUp or Disabled;
}

/// <summary>The <c>roof</c> block of <c>GET /conditions</c>.</summary>
public sealed class SentinelConditionsRoof
{
    /// <summary><c>Open</c>, <c>Closed</c> or <c>Unknown</c>. Never null.</summary>
    public string State { get; init; } = "Unknown";

    /// <summary>Classifier confidence, 0-1, or null.</summary>
    public double? Confidence { get; init; }

    /// <summary>Whether <see cref="State"/> reads Open, ignoring case.</summary>
    public bool IsOpen => string.Equals(State, "Open", StringComparison.OrdinalIgnoreCase);
}

/// <summary>The <c>sky</c> block of <c>GET /conditions</c>.</summary>
public sealed class SentinelConditionsSky
{
    /// <summary>Sky class from the ML model, e.g. <c>Clear</c>, or null.</summary>
    public string? Condition { get; init; }

    /// <summary>Classifier confidence, 0-1, or null.</summary>
    public double? Confidence { get; init; }

    /// <summary>Whether the star classifier sees stars, or null.</summary>
    public bool? StarsVisible { get; init; }
}

/// <summary>The <c>stars</c> block of <c>GET /conditions</c>.</summary>
public sealed class SentinelConditionsStars
{
    /// <summary>Stars detected on the latest frame, or null.</summary>
    public int? Count { get; init; }

    /// <summary>This rig's clear-sky star count for the moon phase, or null before it is learned.</summary>
    public double? Baseline { get; init; }

    /// <summary><see cref="Count"/> / <see cref="Baseline"/>, or null.</summary>
    public double? Ratio { get; init; }

    /// <summary>Fractional change against the median of recent frames (-0.5 = halved), or null.</summary>
    public double? Trend { get; init; }

    /// <summary>
    /// Signed percent change of the star count against the median of the last
    /// <see cref="WindowFrames"/> frames (-45 = 45 % fewer), or null.
    /// </summary>
    /// <remarks>Null on a Sentinel that predates the field.</remarks>
    public double? ChangePct { get; init; }

    /// <summary>How many recent frames <see cref="ChangePct"/> compares against, or null.</summary>
    public int? WindowFrames { get; init; }
}

/// <summary>The <c>baseline_status</c> block: whether this rig's clear-sky star baseline is learned yet.</summary>
/// <remarks>
/// Every installation learns its own baseline from its own clear frames, so a new rig
/// starts in <c>learning</c>. Until it is <c>ready</c>, the star ratio and the cloud
/// cover derived from it have nothing to compare against.
/// </remarks>
public sealed class SentinelBaselineStatus
{
    /// <summary><c>learning</c> or <c>ready</c>; other values are passed through.</summary>
    public string State { get; init; } = string.Empty;

    /// <summary>Clear frames collected so far, or null.</summary>
    public int? Samples { get; init; }

    /// <summary>Clear frames needed before the baseline is used, or null.</summary>
    public int? Needed { get; init; }

    /// <summary>Whether Sentinel is still learning this sky.</summary>
    public bool IsLearning => string.Equals(State, "learning", StringComparison.OrdinalIgnoreCase);
}

/// <summary>The <c>fwhm</c> block of <c>GET /conditions</c>.</summary>
public sealed class SentinelConditionsFwhm
{
    /// <summary>Median star FWHM on the pier camera, in pixels, or null.</summary>
    public double? Px { get; init; }

    /// <summary>The same in arcseconds when the plate scale is known, or null.</summary>
    public double? Arcsec { get; init; }

    /// <summary>The night's median FWHM in pixels, or null.</summary>
    public double? NightMedianPx { get; init; }

    /// <summary>
    /// Signed percent change of FWHM against the night median (+30 = 30 % larger), or null.
    /// </summary>
    /// <remarks>Null on a Sentinel that predates the field.</remarks>
    public double? ChangePct { get; init; }
}

/// <summary>The <c>moon</c> block of <c>GET /conditions</c>.</summary>
public sealed class SentinelConditionsMoon
{
    /// <summary>Whether the Moon is above the horizon, or null without a site in Sentinel.</summary>
    public bool? Up { get; init; }

    /// <summary>Illuminated fraction, 0-100, or null.</summary>
    public double? IlluminationPct { get; init; }

    /// <summary>Moon altitude in degrees, or null without a site.</summary>
    /// <remarks>Null on a Sentinel that predates the field.</remarks>
    public double? AltitudeDeg { get; init; }
}

/// <summary>
/// The <c>target</c> block of <c>GET /conditions</c>: the target this plugin reported
/// to Sentinel, and how far it is from the Moon.
/// </summary>
public sealed class SentinelConditionsTarget
{
    /// <summary>The target's name.</summary>
    public string Name { get; init; } = string.Empty;

    /// <summary>Angular distance between the target and the Moon, degrees, or null.</summary>
    public double? MoonSeparationDeg { get; init; }

    /// <summary>The target's altitude in degrees, or null without a site.</summary>
    public double? AltitudeDeg { get; init; }
}

/// <summary>
/// The body of <c>GET /conditions</c>: Sentinel's verdict on whether the sky is good
/// enough to image, and the measurements behind it.
/// </summary>
/// <remarks>
/// <para>
/// Parsed by hand (<see cref="Parse"/>) rather than by the serializer, so one field of
/// an unexpected type becomes null instead of failing the whole read. A pause trigger
/// that stopped understanding Sentinel because a number arrived as a string would be
/// far worse than one that lost a single figure from its status line.
/// </para>
/// <para>
/// Fields may be added to the contract, never renamed. Every nested block is non-null
/// here even when Sentinel omitted it; its members are null instead.
/// </para>
/// </remarks>
public sealed class SentinelConditions
{
    /// <summary>Whether imaging conditions are switched on in Sentinel.</summary>
    public bool Enabled { get; init; }

    /// <summary>When Sentinel last judged a frame, UTC, or null.</summary>
    public DateTime? UpdatedAtUtc { get; init; }

    /// <summary>Seconds since that judgement, as Sentinel measured it, or null.</summary>
    public double? AgeSeconds { get; init; }

    /// <summary>Whether Sentinel considers its verdict out of date.</summary>
    public bool Stale { get; init; }

    /// <summary>
    /// Sentinel's verdict, after its own bad/good-frame hysteresis. False whenever it has
    /// no verdict at all.
    /// </summary>
    /// <remarks>
    /// This, not <see cref="Reasons"/>, is the verdict: during the grace frames before
    /// Sentinel turns "not OK", <see cref="Reasons"/> already lists the latest frame's
    /// codes while this is still true.
    /// </remarks>
    public bool ImagingOk { get; init; }

    /// <summary>Reason codes; see <see cref="SentinelConditionReasons"/>. Never null.</summary>
    public IReadOnlyList<string> Reasons { get; init; } = Array.Empty<string>();

    /// <summary>When <see cref="ImagingOk"/> last flipped, UTC, or null.</summary>
    public DateTime? ChangedAtUtc { get; init; }

    /// <summary>Frames Sentinel has judged since it started.</summary>
    public int FramesConsidered { get; init; }

    /// <summary>Roof reading.</summary>
    public SentinelConditionsRoof Roof { get; init; } = new();

    /// <summary>Sky reading.</summary>
    public SentinelConditionsSky Sky { get; init; } = new();

    /// <summary>Star evidence.</summary>
    public SentinelConditionsStars Stars { get; init; } = new();

    /// <summary>Estimated cloud cover, percent, or null.</summary>
    public double? CloudCoverPct { get; init; }

    /// <summary><c>star_baseline</c>, <c>ml_class</c>, or null.</summary>
    public string? CloudCoverSource { get; init; }

    /// <summary>Star size.</summary>
    public SentinelConditionsFwhm Fwhm { get; init; } = new();

    /// <summary>Sun altitude at the frame, degrees, or null.</summary>
    public double? SunAltitudeDeg { get; init; }

    /// <summary>The Moon. Every field null on a Sentinel that predates the block.</summary>
    public SentinelConditionsMoon Moon { get; init; } = new();

    /// <summary>
    /// The reported target and its distance from the Moon; null when Sentinel has no
    /// fresh target, and on a Sentinel that predates the block.
    /// </summary>
    public SentinelConditionsTarget? Target { get; init; }

    /// <summary>Clear-sky baseline learning state, or null on a Sentinel that predates it.</summary>
    public SentinelBaselineStatus? BaselineStatus { get; init; }

    /// <summary>Whether the star baseline is known to be still learning.</summary>
    public bool BaselineLearning => BaselineStatus?.IsLearning == true;

    /// <summary>
    /// Whether Sentinel's "not OK" is only "no verdict": imaging conditions off, still
    /// warming up, or stale - with no measured reason among them.
    /// </summary>
    public bool HasNoVerdict
    {
        get
        {
            if (!Enabled || Stale)
            {
                return true;
            }

            if (ImagingOk || Reasons.Count == 0)
            {
                return false;
            }

            foreach (string code in Reasons)
            {
                if (!SentinelConditionReasons.IsNoVerdict(code))
                {
                    return false;
                }
            }

            return true;
        }
    }

    /// <summary>Parses a <c>GET /conditions</c> body.</summary>
    /// <exception cref="SentinelException">
    /// The body is not a JSON object at all. A single malformed field never throws.
    /// </exception>
    public static SentinelConditions Parse(string body)
    {
        JsonDocument document;
        try
        {
            document = JsonDocument.Parse(body ?? string.Empty);
        }
        catch (JsonException ex)
        {
            throw new SentinelException(SentinelErrorKind.Protocol,
                "Sentinel returned a conditions response that could not be parsed.", innerException: ex);
        }

        using (document)
        {
            JsonElement root = document.RootElement;
            if (root.ValueKind != JsonValueKind.Object)
            {
                throw new SentinelException(SentinelErrorKind.Protocol,
                    "Sentinel returned a conditions response that is not a JSON object.");
            }

            JsonElement roof = Child(root, "roof");
            JsonElement sky = Child(root, "sky");
            JsonElement stars = Child(root, "stars");
            JsonElement fwhm = Child(root, "fwhm");
            JsonElement baseline = Child(root, "baseline_status");
            JsonElement moon = Child(root, "moon");
            JsonElement target = Child(root, "target");


            return new SentinelConditions
            {
                // Absent reads as on: an older Sentinel that serves the route at all
                // has the feature, and "off" would park every trigger on a guess.
                Enabled = Bool(root, "enabled") ?? true,
                UpdatedAtUtc = Time(root, "updated_at"),
                AgeSeconds = Number(root, "age_s"),
                Stale = Bool(root, "stale") ?? false,
                // Absent reads as not OK: a verdict that cannot be read is not a yes.
                ImagingOk = Bool(root, "imaging_ok") ?? false,
                Reasons = Strings(root, "reasons"),
                ChangedAtUtc = Time(root, "changed_at"),
                FramesConsidered = (int)Math.Clamp(Number(root, "frames_considered") ?? 0, 0, int.MaxValue),
                Roof = new SentinelConditionsRoof
                {
                    State = Text(roof, "state") is { Length: > 0 } state ? state : "Unknown",
                    Confidence = Number(roof, "confidence"),
                },
                Sky = new SentinelConditionsSky
                {
                    Condition = Text(sky, "condition"),
                    Confidence = Number(sky, "confidence"),
                    StarsVisible = Bool(sky, "stars_visible"),
                },
                Stars = new SentinelConditionsStars
                {
                    Count = Integer(stars, "count"),
                    Baseline = Number(stars, "baseline"),
                    Ratio = Number(stars, "ratio"),
                    Trend = Number(stars, "trend"),
                    ChangePct = Number(stars, "change_pct"),
                    WindowFrames = Integer(stars, "window_frames"),
                },
                CloudCoverPct = Number(root, "cloud_cover_pct"),
                CloudCoverSource = Text(root, "cloud_cover_source"),
                Fwhm = new SentinelConditionsFwhm
                {
                    Px = Number(fwhm, "px"),
                    Arcsec = Number(fwhm, "arcsec"),
                    NightMedianPx = Number(fwhm, "night_median_px"),
                    ChangePct = Number(fwhm, "change_pct"),
                },
                SunAltitudeDeg = Number(root, "sun_altitude_deg"),
                Moon = new SentinelConditionsMoon
                {
                    Up = Bool(moon, "up"),
                    IlluminationPct = Number(moon, "illumination_pct"),
                    AltitudeDeg = Number(moon, "altitude_deg"),
                },
                Target = target.ValueKind == JsonValueKind.Object
                    ? new SentinelConditionsTarget
                    {
                        Name = Text(target, "name") ?? string.Empty,
                        MoonSeparationDeg = Number(target, "moon_separation_deg"),
                        AltitudeDeg = Number(target, "altitude_deg"),
                    }
                    : null,
                BaselineStatus = baseline.ValueKind == JsonValueKind.Object
                    ? new SentinelBaselineStatus
                    {
                        State = Text(baseline, "state") ?? string.Empty,
                        Samples = Integer(baseline, "samples"),
                        Needed = Integer(baseline, "needed"),
                    }
                    : null,
            };
        }
    }

    /// <summary>One line for NINA's log.</summary>
    public override string ToString() =>
        string.Format(CultureInfo.InvariantCulture,
            "imaging_ok={0}, reasons=[{1}], stale={2}, enabled={3}, age_s={4}",
            ImagingOk, string.Join(",", Reasons), Stale, Enabled,
            AgeSeconds?.ToString("0.#", CultureInfo.InvariantCulture) ?? "null");

    private static bool TryGet(JsonElement obj, string name, out JsonElement value)
    {
        value = default;
        if (obj.ValueKind != JsonValueKind.Object)
        {
            return false;
        }

        if (obj.TryGetProperty(name, out value))
        {
            return true;
        }

        foreach (JsonProperty property in obj.EnumerateObject())
        {
            if (string.Equals(property.Name, name, StringComparison.OrdinalIgnoreCase))
            {
                value = property.Value;
                return true;
            }
        }

        return false;
    }

    private static JsonElement Child(JsonElement obj, string name) =>
        TryGet(obj, name, out JsonElement value) && value.ValueKind == JsonValueKind.Object ? value : default;

    private static bool? Bool(JsonElement obj, string name)
    {
        if (!TryGet(obj, name, out JsonElement value))
        {
            return null;
        }

        return value.ValueKind switch
        {
            JsonValueKind.True => true,
            JsonValueKind.False => false,
            JsonValueKind.String when bool.TryParse(value.GetString(), out bool parsed) => parsed,
            _ => null,
        };
    }

    private static double? Number(JsonElement obj, string name)
    {
        if (!TryGet(obj, name, out JsonElement value))
        {
            return null;
        }

        double parsed;
        if (value.ValueKind == JsonValueKind.Number && value.TryGetDouble(out parsed))
        {
            return double.IsFinite(parsed) ? parsed : null;
        }

        if (value.ValueKind == JsonValueKind.String &&
            double.TryParse(value.GetString(), NumberStyles.Float, CultureInfo.InvariantCulture, out parsed))
        {
            return double.IsFinite(parsed) ? parsed : null;
        }

        return null;
    }

    private static int? Integer(JsonElement obj, string name) =>
        Number(obj, name) is double value ? (int)Math.Clamp(Math.Round(value), 0, int.MaxValue) : null;

    private static string? Text(JsonElement obj, string name) =>
        TryGet(obj, name, out JsonElement value) && value.ValueKind == JsonValueKind.String
            ? value.GetString()
            : null;

    private static DateTime? Time(JsonElement obj, string name)
    {
        string? text = Text(obj, name);
        if (text is null ||
            !DateTimeOffset.TryParse(text, CultureInfo.InvariantCulture,
                DateTimeStyles.AssumeUniversal | DateTimeStyles.AdjustToUniversal, out DateTimeOffset parsed))
        {
            return null;
        }

        return parsed.UtcDateTime;
    }

    private static IReadOnlyList<string> Strings(JsonElement obj, string name)
    {
        if (!TryGet(obj, name, out JsonElement value) || value.ValueKind != JsonValueKind.Array)
        {
            return Array.Empty<string>();
        }

        var list = new List<string>();
        foreach (JsonElement item in value.EnumerateArray())
        {
            if (item.ValueKind == JsonValueKind.String && item.GetString() is { Length: > 0 } code)
            {
                list.Add(code);
            }
        }

        return list;
    }
}
