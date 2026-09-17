# Capture Control API

The Capture Control API lets other programs on the observatory PC start and stop PFR Sentinel capture over HTTP — a NINA sequence, a PowerShell script, a home-automation tool, or plain `curl`. It rides on Sentinel's built-in [Web Server](Web-Server), is switched off by default, and every request needs a bearer token. This page is the reference for writing your own clients. If you just want NINA to control Sentinel, see [NINA Integration](NINA-Integration), which sets all of this up for you.

---

## Enabling It

Both switches are in the **Web Server** card of the [Output Settings](Output-Settings) tab:

1. Turn on **Enable Web Server**.
2. Turn on **Enable Capture Control API**.

The control routes only work while the web server is running. If you turn control on with the web server off, Sentinel warns: "Capture control needs the web server — enable it above." Changes take effect immediately on a running server; no restart is needed.

| Setting | Default | Description |
|---------|---------|-------------|
| **Enable Capture Control API** | Off | Turns the control routes on. With it off, every control request is refused with `control_disabled`. |
| **API Token** | (empty until first enabled) | The bearer token clients must send. Generated automatically the first time you enable the API. |

When control is on, the API reference served by Sentinel at `/docs` (the **Open API Docs** link in the same card) and the machine-readable spec at `/openapi.json` include the control routes. When it is off, they are left out.

---

## The API Token

The **API Token** row shows the token masked, with three buttons:

| Button | What it does |
|--------|--------------|
| **Show** / **Hide** | Reveals or masks the token in the field. |
| **Copy** | Copies the token to the clipboard ("API token copied to clipboard"). |
| **Regenerate** | Creates a new token. The old one stops working immediately ("New API token generated — any tool using the old one must be updated."). |

Notes:

- The token is a random 43-character string. It is created on first enable, not at install, so a Sentinel that never uses the API never holds a credential.
- Turning the API off blanks the field and makes the server refuse control requests at once. The token itself is kept, so turning the API back on restores the same token and existing clients keep working.
- The NINA plugin and the bundled NINA script helpers read the token from Sentinel's settings on their own. You only need **Copy** for other tools.
- Treat the token like a password. Sentinel never writes it to its logs.

---

## Security Model

Control requests are checked more strictly than the read-only endpoints (`/latest`, `/status`):

| Check | Behaviour |
|-------|-----------|
| Bearer token | Required on every control request, including from the same machine. Missing, malformed and wrong tokens all get the same `401` answer, so a client cannot tell which it was. |
| Host allow-list | The request's `Host` header must be an allowed name (below). Checked before the token. |
| Fail closed | With the API off there is no token, and every control request is refused. An empty token never means "open". |
| No CORS | Control responses carry no `Access-Control-Allow-Origin` header, and `POST` is not offered in CORS preflight, so a web page in a browser cannot drive capture. |

### Host allow-list

These `Host` values are always accepted: `localhost`, `127.0.0.1`, `::1` and `[::1]`. The port in the header is ignored and the comparison is case-insensitive.

The web server's **Host** setting is also accepted if it names a specific address. If **Host** is `0.0.0.0`, that isn't a real address, so it adds nothing to the list.

To accept further names (for example a LAN address or a DNS name), Sentinel 3.7.0 and later read an optional list, `webserver_control_allowed_hosts`, in the `output` section of Sentinel's `config.json` (in `%LOCALAPPDATA%\PFRSentinel\`). There is no control for it in the app; close Sentinel before editing the file. Requests with any other `Host` are refused with `403 host_not_allowed`.

### Binding and remote access

The web server's **Host** defaults to `127.0.0.1`, so only programs on the same PC can reach it. Capture control is designed for that setup. Exposing it to your network means changing **Host**, allow-listing names as above, and accepting that the token travels over plain, unencrypted HTTP — only do this on a network you trust.

---

## Endpoints

The control routes live under `/capture` by default. The base path can be changed only through the `webserver_control_path` key in the `output` section of `config.json`; the NINA plugin and script helpers follow that key automatically.

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/capture/start` | Start capture in Sentinel's configured mode (camera or directory watch). |
| `POST` | `/capture/stop` | Stop capture. |
| `GET` | `/capture` | Current capture state plus a readiness flag, without issuing a command. |

All three need the `Authorization: Bearer <token>` header. A query string on the path is ignored on all three routes. A trailing slash is ignored only on the two `POST` routes, so `POST /capture/start/` works but `GET /capture/` returns `404`. A `POST` to any other path returns `404`.

---

## POST /capture/start and /capture/stop

### Request

The body is optional. An empty body uses the defaults, so a bare `POST` works. If you send one, it must be a JSON object of at most 4096 bytes:

| Field | Type | Default | Range | Description |
|-------|------|---------|-------|-------------|
| `wait` | boolean | `true` | — | When true, the request blocks until capture reaches the target state, fails, or the timeout passes. When false, it returns as soon as the command has been handed to Sentinel. Must be a real JSON boolean, not a string. |
| `timeout` | number | `30` | 1–300 | Seconds to wait when `wait` is true. Decimals are allowed. Out-of-range values are rejected with `400`, not clamped. |

### What counts as "reached"

| Command | Target state |
|---------|--------------|
| start | Capture is enabled and its state is `capturing`, `waiting`, `outside_window` or `recovering` (see [Capture block](#capture-block) for when each is reported). A scheduled capture waiting for its window counts as started. |
| stop | Capture is neither enabled nor running. |

While waiting for a start, Sentinel also watches for failure: if capture enters the `error` state, the camera is marked unrecoverable, or a new capture error is recorded after the command, the request returns `failed` straight away instead of running out the timeout.

### Idempotency

If capture is already in the target state when the request arrives, Sentinel does nothing and answers `200` with `already_running` or `already_stopped`. A script or sequence can safely repeat Start, or send Stop twice while aborting.

### Response

| Field | Type | Description |
|-------|------|-------------|
| `command` | string | `start` or `stop`. |
| `result` | string | Outcome — see below. |
| `changed` | boolean | True only for `started` and `stopped`, when capture state actually changed. |
| `issued` | boolean | Whether the command was handed to Sentinel. False for the `already_*` no-ops. |
| `state` | string | Capture state at the time of the response (same values as `capture.state` below). |
| `running` | boolean | Whether capture is producing frames. |
| `enabled` | boolean | Whether capture is enabled in Sentinel. |
| `waited` | boolean | Whether the request blocked for the target state. |
| `wait_seconds` | number | Seconds spent waiting, to two decimals; `0` when it did not wait. |
| `message` | string | A plain-English outcome, safe to show a user. For `failed`, this is Sentinel's own error text when it has one (for example a camera error). |

| `result` | HTTP status | Meaning |
|----------|-------------|---------|
| `started` | 200 | Capture was started and reached the running state. |
| `stopped` | 200 | Capture was stopped and reached the stopped state. |
| `already_running` | 200 | Start requested, capture was already running. Nothing changed. |
| `already_stopped` | 200 | Stop requested, capture was already stopped. Nothing changed. |
| `pending` | 200 | The command was issued with `wait: false`; the final state is not confirmed. Poll `GET /capture`. |
| `timeout` | 504 | The command was issued, but the target state was not reached within `timeout`. The command still stands. |
| `failed` | 500 | Capture reported a failure. Read `message`. |

Example:

```json
{
  "command": "start",
  "result": "started",
  "changed": true,
  "issued": true,
  "state": "capturing",
  "running": true,
  "enabled": true,
  "waited": true,
  "wait_seconds": 1.52,
  "message": "Capture started."
}
```

### Cancelling does not undo a command

Sentinel acts on a command before it starts waiting. If your client gives up — you cancel the request, the connection drops, or your own HTTP timeout fires — the start or stop has usually already happened. Nothing is rolled back. Check `GET /capture` before retrying, and set your client's timeout comfortably above `timeout` (the bundled script uses `timeout` + 15 seconds).

---

## GET /capture

Returns the current state without changing anything. Use it to pre-flight a client — for example to validate a sequence before the night — and to poll after a `wait: false` command.

| Field | Type | Description |
|-------|------|-------------|
| `capture` | object | The capture block, identical to `capture` in `/status` (below). |
| `health` | object | The health block, identical to `health` in `/status` (below). |
| `control_ready` | boolean | Whether a start or stop would actually be accepted right now. `false` means control is on and the token is valid, but Sentinel's capture controls aren't connected to the server (this shouldn't happen; restart Sentinel) — a command would get `503 control_unavailable`. |
| `timestamp` | string | Server time, ISO 8601. |

`GET /capture` goes through the same Host and token checks as the commands, so it also tells you the token is good. It never returns `control_unavailable`; it reports that condition as `control_ready: false` instead.

---

## Status Blocks

These blocks appear in `GET /capture` and in the unauthenticated `GET /status` (see [Web Server](Web-Server) for the rest of `/status`).

### Capture block

| Field | Type | Description |
|-------|------|-------------|
| `mode` | string | `camera`, `watch`, or `idle` (not capturing). |
| `enabled` | boolean | Whether capture is enabled. Stays true while the camera is recovering. |
| `running` | boolean | Whether capture is producing frames right now. |
| `state` | string | `capturing`, `waiting`, `recovering`, `outside_window`, `stopped` or `error`. `waiting` is only reported in headless mode, between frames. `calibrating` appears in the API reference but is never reported. |
| `interval_seconds` | number | Configured seconds between captures (camera mode); `null` otherwise. |
| `effective_interval_seconds` | number | Interval actually in force now, honouring a variable-rate schedule; `null` otherwise. |
| `schedule` | object | Scheduled-capture window — see below. `null` when no schedule applies, such as in watch mode or while capture is stopped in the desktop app. |
| `last_capture_age_seconds` | integer | Seconds since the last successful capture; `null` if none yet. |
| `next_capture_in_seconds` | integer | Estimated seconds until the next capture (camera running); `null` if not predictable. |
| `next_capture_expected_epoch` | number | Unix time of the next expected capture; `null` if not predictable. |
| `recovery` | object | `{in_progress, attempts, unrecoverable}` — camera auto-recovery state. |
| `last_error` | string | Most recent capture error message, or `null`. |
| `last_error_epoch` | number | Unix time of that error, or `null`. Use it to tell a new failure from the same fault reported again — the message text alone cannot. |

### Schedule block

Reflects the scheduled-capture settings on the [Capture Settings](Capture-Settings) tab.

| Field | Type | Description |
|-------|------|-------------|
| `mode` | string | `always`, `gated` (capture only inside the window) or `variable` (faster interval inside the window). |
| `source` | string | `fixed` (the configured start and end times) or `timelapse` (the [Timelapse](Timelapse) recording window widened by the configured margin). |
| `start_time` | string | Window start, `HH:MM`. For `timelapse`, the start of the window currently in force. |
| `end_time` | string | Window end, `HH:MM`. |
| `window` | string | Human-readable label, e.g. `16:00 - 09:00`, `timelapse window ±15 min (17:45 - 06:15)` or `timelapse window (always on)`. |
| `in_window` | boolean | Whether now is inside the window. Always `true` in `always` mode. |
| `window_interval_seconds` | number | Seconds between captures inside the window in `variable` mode; `null` in other modes. |

### Health block

| Field | Type | Description |
|-------|------|-------------|
| `status` | string | One of the values below. |
| `reasons` | array of strings | Plain-English explanations; empty when `ok`. |

| `status` | Meaning |
|----------|---------|
| `ok` | Capture is running and producing fresh frames. |
| `idle` | Intentionally not capturing: capture is off, outside a gated schedule window, or enabled but not yet running. |
| `degraded` | Camera capture is running but no new frame has arrived for 3× the capture interval (at least 5 minutes). |
| `recovering` | Camera auto-recovery is in progress. |
| `error` | Capture failed, or the camera is unrecoverable and Sentinel needs restarting. |

---

## Errors

Rejected requests return JSON with a machine-readable `code`. Branch on `code`, not on the message text:

```json
{ "error": "Control API is not configured on this server.", "status": 503, "code": "control_disabled" }
```

| HTTP | `code` | Cause | Fix |
|------|--------|-------|-----|
| 400 | `bad_request` | Body is not valid UTF-8 JSON, not an object, `wait` is not a boolean, `timeout` is not a number between 1 and 300, or `Content-Length` is invalid. | Fix the request. |
| 413 | `body_too_large` | Body larger than 4096 bytes. | Send only `wait` and `timeout`. |
| 401 | `unauthorized` | Token missing, malformed (not `Bearer <token>`) or wrong. | Copy the current token from the Output tab. |
| 403 | `host_not_allowed` | `Host` header not on the allow-list. | Call from the same machine, or see [Host allow-list](#host-allow-list). |
| 503 | `control_disabled` | **Enable Capture Control API** is off. | Turn it on in the Output tab. |
| 503 | `control_unavailable` | Control is on and the token is valid, but Sentinel's capture controls aren't connected to the server (this shouldn't happen). POST only. | Restart Sentinel. |
| 500 | `internal_error` | Unexpected error inside Sentinel. | Check Sentinel's [Logs](Logs). |

The two `503` codes need opposite fixes — enable a setting versus restart Sentinel — which is why they are separate. Checks run in order: Host, then token, then body, so a request from a disallowed host gets `403` even if control is switched off.

The `500 failed` and `504 timeout` command outcomes are not in this table: they return the normal command response shown above, with `result` and `message`, and no `code`.

---

## Headless Mode

When Sentinel runs without its window (headless mode), the API behaves the same and uses the same settings. One difference: **stop pauses the capture loop** rather than closing Sentinel, so a later **start** resumes it. The desktop app stops and starts capture exactly as the Start/Stop button does.

---

## Polling the Image Alongside Control

If your client also shows the live frame from `/latest`, poll with `If-None-Match` to get `304 Not Modified` when nothing has changed. Sentinel's `ETag` is an **unquoted** MD5 hex string, not a standard quoted entity tag. Send it back exactly as received — adding quotes means it never matches. Some HTTP libraries reject or drop an unquoted ETag, so read the raw header if yours does. Responses also carry `X-PFR-Image-Age-Seconds`, and `X-PFR-Image-Stale: true` once the frame is 5 minutes old.

---

## Examples

Replace `YOUR_TOKEN` with the token from **Copy**, and `8080` if you changed the port.

### curl

In PowerShell, type `curl.exe` rather than `curl`. The body quoting below is for a Unix-style shell such as Git Bash.

```bash
# Pre-flight: is control ready?
curl -H "Authorization: Bearer YOUR_TOKEN" http://127.0.0.1:8080/capture

# Start, waiting up to 60 s for capture to run
curl -X POST http://127.0.0.1:8080/capture/start \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"wait": true, "timeout": 60}' \
  --max-time 75

# Stop with the defaults (wait up to 30 s)
curl -X POST http://127.0.0.1:8080/capture/stop -H "Authorization: Bearer YOUR_TOKEN"
```

### PowerShell

This reads the port and token from Sentinel's own settings, so nothing needs pasting (run it as the same Windows user as Sentinel). The error-body handling shown is for PowerShell 7.

```powershell
$cfg     = Get-Content "$env:LOCALAPPDATA\PFRSentinel\config.json" -Raw | ConvertFrom-Json
$base    = "http://127.0.0.1:$($cfg.output.webserver_port)"
$headers = @{ Authorization = "Bearer $($cfg.output.api_token)" }

# Pre-flight
$state = Invoke-RestMethod "$base/capture" -Headers $headers
"Ready: $($state.control_ready)  State: $($state.capture.state)  Health: $($state.health.status)"

# Start and wait up to 60 s
$body = @{ wait = $true; timeout = 60 } | ConvertTo-Json
try {
    $r = Invoke-RestMethod "$base/capture/start" -Method Post -Headers $headers `
         -ContentType 'application/json' -Body $body -TimeoutSec 75
    "$($r.result): $($r.message)"
} catch {
    # Non-2xx: the JSON body carries `code` (rejections) or `result` + `message` (failed / timeout)
    $_.ErrorDetails.Message
}
```

For a ready-made script with exit codes suited to NINA's External Script instruction, see [NINA Integration](NINA-Integration#alternative-external-script-helpers).
