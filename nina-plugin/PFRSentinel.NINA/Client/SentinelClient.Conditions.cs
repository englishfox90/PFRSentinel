#nullable enable
using System.Net;
using System.Net.Http;
using System.Threading;
using System.Threading.Tasks;

namespace PFRSentinel.Nina.Client;

/// <summary>The imaging-conditions read: <c>GET /conditions</c>.</summary>
public sealed partial class SentinelClient
{
    /// <summary>
    /// Reads Sentinel's verdict on whether the sky is good enough to image.
    /// </summary>
    /// <param name="cancellationToken">Caller cancellation.</param>
    /// <returns>The parsed verdict. Unknown fields are ignored, missing ones read as null.</returns>
    /// <remarks>
    /// <para>
    /// Same authentication as Start/Stop - Host allow-list, bearer token, the one 401
    /// re-read-and-retry - because the route sits behind <c>web_control.authorize</c>.
    /// Bounded by <see cref="ReadTimeout"/> and serialised with the other reads, so a
    /// slow Sentinel can never stack requests from several sequence items.
    /// </para>
    /// <para>
    /// A Sentinel that predates the route answers a plain 404; that surfaces as a
    /// <see cref="SentinelErrorKind.Protocol"/> failure whose message says to update
    /// Sentinel, rather than the generic "check the paths" text a 404 gets elsewhere.
    /// </para>
    /// </remarks>
    /// <exception cref="SentinelConfigurationException">Control is not configured locally.</exception>
    /// <exception cref="SentinelUnauthorizedException">The token was rejected, twice.</exception>
    /// <exception cref="SentinelUnavailableException">Control is disabled on the server.</exception>
    /// <exception cref="SentinelUnreachableException">Sentinel did not answer.</exception>
    /// <exception cref="SentinelTimeoutException">The client's own budget expired.</exception>
    /// <exception cref="SentinelCommandAbandonedException">The caller cancelled.</exception>
    public async Task<SentinelConditions> GetConditionsAsync(CancellationToken cancellationToken = default)
    {
        SentinelConfig config = RequireControlConfig();

        await _readGate.WaitAsync(cancellationToken).ConfigureAwait(false);
        try
        {
            using HttpResponseMessage response = await SendWithTokenRefreshAsync(
                cfg => Authorised(new HttpRequestMessage(HttpMethod.Get, cfg.ConditionsUri), cfg),
                config, ReadTimeout, honourCallerCancellation: true, cancellationToken, command: "conditions")
                .ConfigureAwait(false);

            string body = await ReadBodyAsync(response).ConfigureAwait(false);
            if (response.StatusCode == HttpStatusCode.NotFound)
            {
                throw new SentinelException(
                    SentinelErrorKind.Protocol,
                    $"The PFR Sentinel at {config.BaseUrl} has no {SentinelConfig.ConditionsPath} endpoint (HTTP 404). Update Sentinel to a version with imaging conditions.",
                    statusCode: HttpStatusCode.NotFound);
            }

            if (!IsSuccess(response.StatusCode))
            {
                throw MapErrorResponse(response.StatusCode, body, config);
            }

            return SentinelConditions.Parse(body);
        }
        finally
        {
            _readGate.Release();
        }
    }
}
