#nullable enable
using System;
using System.Globalization;
using System.IO;
using System.Text;
using System.Text.Json;
using System.Text.Json.Serialization;

namespace PFRSentinel.Nina.Client;

/// <summary>
/// The target NINA is imaging, as sent to Sentinel's <c>POST /nina/target</c>.
/// </summary>
/// <param name="Name">Target name as typed in the sequencer.</param>
/// <param name="RaDeg">J2000 right ascension, decimal degrees.</param>
/// <param name="DecDeg">J2000 declination, decimal degrees.</param>
/// <param name="FovWidthDeg">Imaging camera field of view along the sensor width, or null when unknown.</param>
/// <param name="FovHeightDeg">Field of view along the sensor height, or null when unknown.</param>
/// <param name="RotationDeg">
/// Sky position angle, degrees east of north, of the camera's "up" (height) axis —
/// NINA's <c>InputTarget.PositionAngle</c>, unchanged. Null when unknown.
/// </param>
public sealed record SentinelTargetReport(
    string Name,
    double RaDeg,
    double DecDeg,
    double? FovWidthDeg,
    double? FovHeightDeg,
    double? RotationDeg);

/// <summary>Sentinel's answer to <c>POST /nina/target</c>.</summary>
public sealed class SentinelTargetResult
{
    /// <summary>Always true on a 200: the request was valid and stored.</summary>
    [JsonPropertyName("accepted")]
    public bool Accepted { get; set; }

    /// <summary>Whether the stored target differs from what Sentinel held before (false for a heartbeat).</summary>
    [JsonPropertyName("changed")]
    public bool Changed { get; set; }

    /// <summary>Sentinel's one-line description, e.g. "Target stored.".</summary>
    [JsonPropertyName("message")]
    public string Message { get; set; } = string.Empty;
}

/// <summary>
/// Builds the request body for <c>POST /nina/target</c>.
/// </summary>
/// <remarks>
/// <para>
/// Written with <see cref="Utf8JsonWriter"/> rather than a serialised DTO so the
/// wire shape is exactly what <c>services/api_target.py</c> validates, in one
/// readable place: numbers are always plain invariant decimals (never an
/// exponent, never a comma), the FOV pair is both-or-neither, and a value the
/// server would reject with a 400 is dropped here instead of failing every
/// request of the night.
/// </para>
/// <para>Shape: <c>{"target":{...}}</c>, or <c>{"target":null}</c> to clear.</para>
/// </remarks>
internal static class SentinelTargetPayload
{
    /// <summary>Longest name Sentinel stores (<c>api_target.MAX_NAME_CHARS</c>).</summary>
    public const int MaxNameChars = 64;

    /// <summary>Widest field Sentinel accepts (<c>api_target.MAX_FOV_DEG</c>).</summary>
    public const double MaxFovDeg = 60.0;

    private const string FallbackName = "NINA target";

    /// <summary>Serialises a report, or the clear message for null.</summary>
    public static string Serialize(SentinelTargetReport? report)
    {
        using var stream = new MemoryStream();
        using (var writer = new Utf8JsonWriter(stream))
        {
            writer.WriteStartObject();
            if (report is null)
            {
                writer.WriteNull("target");
            }
            else
            {
                writer.WriteStartObject("target");
                writer.WriteString("name", CleanName(report.Name));
                WriteDecimal(writer, "ra_deg", NormaliseRa(report.RaDeg), 6);
                WriteDecimal(writer, "dec_deg", Math.Clamp(Finite(report.DecDeg), -90.0, 90.0), 6);
                writer.WriteString("epoch", "J2000");

                double? width = ValidFov(report.FovWidthDeg);
                double? height = ValidFov(report.FovHeightDeg);
                if (width is not null && height is not null)
                {
                    WriteDecimal(writer, "fov_w_deg", width.Value, 4);
                    WriteDecimal(writer, "fov_h_deg", height.Value, 4);
                }

                if (report.RotationDeg is double rotation && double.IsFinite(rotation))
                {
                    WriteDecimal(writer, "rotation_deg", NormaliseAngle(rotation, 3), 3);
                }

                writer.WriteString("source", "nina");
                writer.WriteEndObject();
            }

            writer.WriteEndObject();
        }

        return Encoding.UTF8.GetString(stream.ToArray());
    }

    /// <summary>
    /// Cleans a name the way Sentinel's <c>api_target.clean_name</c> does: drops every
    /// character in a Unicode "C" category (control, format, private use, unassigned,
    /// unpaired surrogate), collapses whitespace runs to one space, trims, and caps at
    /// <see cref="MaxNameChars"/> code points.
    /// </summary>
    /// <remarks>
    /// Mirrored rather than left to the server so a name Sentinel would clean to
    /// nothing — say a lone zero-width space — becomes a placeholder here instead of a
    /// 400 on every request. Unpaired surrogates must go in any case:
    /// <see cref="Utf8JsonWriter"/> refuses to encode them.
    /// </remarks>
    public static string CleanName(string? raw)
    {
        string text = raw ?? string.Empty;
        var builder = new StringBuilder(Math.Min(text.Length, MaxNameChars * 2));
        bool pendingSpace = false;
        int codePoints = 0;

        for (int i = 0; i < text.Length && codePoints < MaxNameChars; i++)
        {
            char c = text[i];
            bool pair = char.IsHighSurrogate(c) && i + 1 < text.Length && char.IsLowSurrogate(text[i + 1]);

            if (!pair && char.IsWhiteSpace(c))
            {
                pendingSpace = builder.Length > 0;
                continue;
            }

            if (!pair && char.IsSurrogate(c))
            {
                continue;
            }

            if (IsDropped(CharUnicodeInfo.GetUnicodeCategory(text, i)))
            {
                if (pair)
                {
                    i++;
                }

                continue;
            }

            if (pendingSpace)
            {
                builder.Append(' ');
                pendingSpace = false;
                if (++codePoints >= MaxNameChars)
                {
                    break;
                }
            }

            builder.Append(c);
            if (pair)
            {
                builder.Append(text[++i]);
            }

            codePoints++;
        }

        string name = builder.ToString().TrimEnd();
        return name.Length == 0 ? FallbackName : name;
    }

    private static bool IsDropped(UnicodeCategory category) => category is
        UnicodeCategory.Control or
        UnicodeCategory.Format or
        UnicodeCategory.Surrogate or
        UnicodeCategory.PrivateUse or
        UnicodeCategory.OtherNotAssigned;

    // Judged after rounding to the 4 places that go on the wire: 0.00004 would be
    // sent as 0, which Sentinel rejects.
    private static double? ValidFov(double? value)
    {
        if (value is not double v || !double.IsFinite(v))
        {
            return null;
        }

        double rounded = Math.Round(v, 4, MidpointRounding.AwayFromZero);
        return rounded > 0.0 && rounded <= MaxFovDeg ? (double?)rounded : null;
    }

    private static double Finite(double value) => double.IsFinite(value) ? value : 0.0;

    // Rounding can turn 359.9999999 into 360, which the server rejects; wrap after rounding.
    private static double NormaliseRa(double ra) => NormaliseAngle(Finite(ra), 6);

    private static double NormaliseAngle(double degrees, int decimals)
    {
        double wrapped = degrees % 360.0;
        if (wrapped < 0.0)
        {
            wrapped += 360.0;
        }

        wrapped = Math.Round(wrapped, decimals, MidpointRounding.AwayFromZero);
        return wrapped >= 360.0 ? 0.0 : wrapped;
    }

    private static void WriteDecimal(Utf8JsonWriter writer, string name, double value, int decimals)
    {
        // "+ 0.0" folds -0 into 0 so nothing ever prints "-0".
        double rounded = Math.Round(value, decimals, MidpointRounding.AwayFromZero) + 0.0;
        string format = "0." + new string('#', decimals);

        writer.WritePropertyName(name);
        writer.WriteRawValue(rounded.ToString(format, CultureInfo.InvariantCulture));
    }
}
