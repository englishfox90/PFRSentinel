# Web Server

PFR Sentinel has a built-in HTTP server that serves the latest processed image, a JSON status report with capture health, and a self-contained API reference. Use it to show the live frame in OBS, NINA or a browser dashboard, to let a monitoring script check that capture is still running, or to view the camera from another device on your network.

The web server is set up in the **Web Server** card on the [Output Settings](Output-Settings) tab.

---

## Settings

| Setting | Default | Range | Description |
|---------|---------|-------|-------------|
| **Enable Web Server** | Off | — | Starts or stops the server. |
| **Host** | 127.0.0.1 | — | Address to listen on. See [Network Access](#network-access). |
| **Port** | 8080 | 1–65535 | TCP port. |
| **Image Path** | /latest | — | URL path for the latest image. |
| **API Docs** | — | — | **Open API Docs** opens the server's reference page in your browser. |
| **Enable Capture Control API** | Off | — | Adds authenticated start/stop endpoints. See [Capture Control API](Capture-Control-API). |
| **API Token** | — | — | Token the control endpoints require. See [Capture Control API](Capture-Control-API). |

**When the server runs:** if **Enable Web Server** is on, the server starts when PFR Sentinel launches, and again when you start capture if it isn't already running. It stays up after you stop capture. Turning **Enable Web Server** on or off takes effect straight away while capture is running or the server is already up. If you turn it on while idle with no server running, it starts the next time you start capture. Changes to **Host**, **Port** or **Image Path** take effect the next time the server starts. To apply them, turn the switch off and on again while capturing, or restart PFR Sentinel.

**If the server can't start** (the port is in use, or the address it should listen on isn't available yet), PFR Sentinel logs an error and tries again every 15 seconds for as long as the server is enabled. This covers a VPN or LAN address that only comes up a little after Windows logon.

---

## Endpoints

| Path | Returns |
|------|---------|
| `/latest` (your **Image Path**) | The latest processed image. |
| `/status` | JSON: server details, image age, the current frame's metadata, and capture health. |
| `/docs` | An HTML reference for every endpoint on this server. It needs no internet connection. |
| `/openapi.json` | The same reference as an OpenAPI 3.0 spec, for tools that read one. |
| `/library`, `/library/image` | Recent frames from the Image Library. Only served when **Expose Web API** is on. See [Image Library](Image-Library). |
| `/capture`, `/capture/start`, `/capture/stop` | Capture state and start/stop commands. Only served when the capture control API is enabled, and they need the API token. See [Capture Control API](Capture-Control-API). |

Query strings don't affect which endpoint you reach, so `/latest?t=1764384123178` still returns the latest image. That makes cache-busting easy. A request for an unknown path returns `404` and lists the paths that are available.

The `/docs` page and the OpenAPI spec only list the library and capture control endpoints while those features are switched on.

---

## Image Endpoint

| | |
|---|---|
| **Path** | `/latest` by default |
| **Method** | GET |
| **Content-Type** | `image/jpeg` or `image/png` |

- The image updates every time a frame is processed.
- Until the first frame arrives after the server starts, the endpoint returns `404` with the message "No image available yet".
- You get the processed frame with its text overlays. If you've set up [Output Framing](Image-Processing#output-framing) (new in the next release), the image is cut to your framing box. The [All-Sky Overlay](All-Sky-Overlay) is only included if you turn on **Web server (also Image Library)** under **Burn overlay into output** in the All-Sky settings.

**Image size and format.** The web copy is sized for viewers like a NINA panel or a dashboard, not for archiving:

- A frame whose longest side is more than 2048 pixels is resized so that side is 2048 pixels. It's served as a quality-90 JPEG whatever **Format** is set to.
- A smaller frame is served in your **Format**. JPEGs use your **JPG Quality**.
- A frame still bigger than 5 MB is shrunk further and served as JPEG.

Use the saved files in your output directory when you need full-resolution images.

### Response headers

| Header | Meaning |
|--------|---------|
| `ETag` | A fingerprint of the current image. See [Caching](#caching-etag). |
| `Cache-Control` | `no-cache, must-revalidate`, which tells clients to check for a newer image each time. |
| `X-PFR-Image-Age-Seconds` | How many seconds ago this image arrived at the server. |
| `X-PFR-Image-Stale` | `true` once the image is 300 seconds (5 minutes) old or older. Missing while the image is fresh. |

The age headers let a viewer notice that capture has stalled without comparing images over time.

---

## Status Endpoint

| | |
|---|---|
| **Path** | `/status` |
| **Method** | GET |
| **Content-Type** | `application/json` |

| Field | Type | Description |
|-------|------|-------------|
| `server` | string | Always `PFR Sentinel HTTP Server`. |
| `status` | string | `running`. |
| `uptime_seconds` | number | Seconds since the server started. |
| `images_served` | number | Images the server has received since it started. |
| `latest_image` | string | File name of the latest image, without its folder. The text `"None"` before the first image. |
| `image_age_seconds` | number or null | Seconds since the latest image arrived. `null` before the first image. |
| `image_stale` | boolean | `true` when the image is at least `stale_threshold_seconds` old. |
| `stale_threshold_seconds` | number | The staleness limit, 300 seconds. |
| `metadata` | object | The current frame's metadata: the same values the [overlay tokens](Overlay-Tokens) use, such as exposure, gain and camera. Folder paths are replaced with `<redacted-path>`. |
| `timestamp` | string | This PC's local time, in ISO format. |
| `capture` | object | Capture mode, state, interval, schedule, last and next capture times, recovery state and last error. |
| `health` | object | Overall health (`ok`, `idle`, `degraded`, `recovering` or `error`) and the reasons behind it. |

The `capture` and `health` blocks are described field by field on the [Capture Control API](Capture-Control-API) page and on the server's own `/docs` page. For a simple "is it working?" check, look at `health.status`. It changes from `ok` when capture is off, outside its schedule, stalled, recovering or failed.

---

## Caching (ETag)

The image endpoint supports conditional requests, which saves bandwidth:

1. Each image response carries an `ETag` header, a fingerprint of the image data.
2. If a client sends that value back in an `If-None-Match` header and the image hasn't changed, the server replies `304 Not Modified` with no image data.
3. When a new image arrives, the full image comes back with a new `ETag`.

Send the `ETag` value back exactly as you received it. It's a plain hexadecimal string with no quotes, and the server compares it character for character.

This is ideal for dashboards that poll `/latest` every few seconds: while the frame hasn't changed, each poll costs only a few hundred bytes of headers.

---

## CORS

The read-only endpoints (`/latest`, `/status`, `/docs`, `/openapi.json` and the library endpoints) send `Access-Control-Allow-Origin: *`. A web page from any site can load the image and status, so custom dashboards work without cross-origin errors.

The capture control endpoints are deliberately different: they never send CORS headers, so a web page in a browser can't use them. Only real clients such as the NINA plugin, scripts and `curl` can, and they must present the API token.

---

## Network Access

| Host setting | Who can connect |
|--------------|-----------------|
| `127.0.0.1` (default) | Only programs on this PC. |
| A specific address (for example, a LAN or VPN address) | Devices that can reach that address. |
| `0.0.0.0` | Every network interface, so any device on your network. |

To reach the server from another device, set **Host** to `0.0.0.0` or the PC's address, and allow the port through Windows Firewall.

**Security:** the image, status, docs and library endpoints have no password. Anyone who can reach the host and port can see your latest image, status and library, so only open the server to networks you trust. Don't forward the port to the internet. The capture control endpoints are the exception: they always require the API token. See [Capture Control API](Capture-Control-API).

The server handles each request on its own thread and drops a connection that stalls for 30 seconds, so one slow or broken client can't freeze the feed for everyone else.

---

## Integration Examples

**OBS browser source:**
Add a browser source with the URL `http://localhost:8080/latest`. Set its refresh interval to match your capture interval.

**NINA:**
Install the PFR Sentinel plugin from the **Output** tab. It shows the live frame and capture health inside NINA. See [NINA Integration](NINA-Integration). Any other image viewer can simply point at `http://<ip>:8080/latest`.

**Custom dashboard:**
Poll `/status` for health and metadata, and show `/latest` for the image. Use ETags or a changing `?t=` value to control caching, and watch `image_stale` or `health.status` to raise an alert when capture stops.

**curl:**
```
curl http://localhost:8080/latest -o latest.jpg
curl http://localhost:8080/status
```
