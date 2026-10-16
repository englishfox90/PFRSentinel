#nullable enable
using System.Net.Http;
using System.Text;
using System.Threading;
using System.Threading.Tasks;

namespace PFRSentinel.Nina.Client;

/// <summary>The target push: <c>POST /nina/target</c>.</summary>
public sealed partial class SentinelClient
{
    /// <summary>
    /// Tells Sentinel which target NINA is imaging, or clears it.
    /// </summary>
    /// <param name="report">The current target, or null to clear Sentinel's copy.</param>
    /// <param name="cancellationToken">Caller cancellation.</param>
    /// <returns>Sentinel's acknowledgement.</returns>
    /// <remarks>
    /// <para>
    /// Same authentication as Start/Stop — Host allow-list, bearer token, and the
    /// one 401 re-read-and-retry — because the route sits behind the same
    /// <c>web_control.authorize</c>. Retrying is safe: the request is idempotent,
    /// a repeat only refreshes the target's receipt time.
    /// </para>
    /// <para>
    /// A Sentinel that predates the route answers a plain 404, which surfaces as a
    /// <see cref="SentinelException"/> whose <see cref="SentinelException.StatusCode"/>
    /// is <see cref="System.Net.HttpStatusCode.NotFound"/>. Callers should read that
    /// as "update Sentinel", not as a fault.
    /// </para>
    /// </remarks>
    /// <exception cref="SentinelConfigurationException">Control is not configured locally.</exception>
    /// <exception cref="SentinelUnauthorizedException">The token was rejected, twice.</exception>
    /// <exception cref="SentinelUnavailableException">Control is disabled on the server.</exception>
    /// <exception cref="SentinelUnreachableException">Sentinel did not answer.</exception>
    /// <exception cref="SentinelTimeoutException">The client's own budget expired.</exception>
    /// <exception cref="SentinelCommandAbandonedException">The caller cancelled.</exception>
    public async Task<SentinelTargetResult> PostTargetAsync(
        SentinelTargetReport? report,
        CancellationToken cancellationToken = default)
    {
        SentinelConfig config = RequireControlConfig();
        string json = SentinelTargetPayload.Serialize(report);

        using HttpResponseMessage response = await SendWithTokenRefreshAsync(
            cfg => BuildTargetRequest(cfg, json),
            config, ReadTimeout, honourCallerCancellation: true, cancellationToken, command: "target")
            .ConfigureAwait(false);

        string body = await ReadBodyAsync(response).ConfigureAwait(false);
        if (!IsSuccess(response.StatusCode))
        {
            throw MapErrorResponse(response.StatusCode, body, config);
        }

        return SentinelJson.Deserialize<SentinelTargetResult>(body, "target");
    }

    private static HttpRequestMessage BuildTargetRequest(SentinelConfig config, string json)
    {
        // A fresh message per attempt: the 401 retry cannot resend a disposed one.
        var request = new HttpRequestMessage(HttpMethod.Post, config.TargetUri)
        {
            Content = new StringContent(json, Encoding.UTF8, "application/json"),
        };

        return Authorised(request, config);
    }
}
