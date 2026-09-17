# Hermes Notifications

PFR Sentinel can send its notifications to a **Hermes** agent webhook as well as, or instead of, [Discord](Discord-Integration). Hermes receives structured, signed JSON rather than a finished chat message. The agent on the Hermes side decides how to word the alert and where to deliver it, for example a phone notification that tells you the roof has closed and clouds are coming in.

Hermes is set up in the **Hermes Webhook** card on the [Output Settings](Output-Settings) tab. Discord and Hermes are independent: turning one on or off doesn't affect the other, and a slow or unreachable Hermes endpoint never delays Discord posts.

---

## Before You Start

You need a Hermes webhook route and its shared secret. Both are set up on the Hermes side. PFR Sentinel doesn't create them. You'll copy two values into PFR Sentinel:

- The **webhook URL** that Hermes gives you for the route.
- The **signing secret** for that route.

Every request is signed with that secret. Hermes rejects any request whose signature doesn't match, so the secret must be exactly the same in both places.

---

## Setup

1. Open the **Output** tab and expand **Hermes Webhook**.
2. Turn on **Enable Hermes Webhook**. The rest of the settings appear.
3. Paste the route's URL into **Webhook URL**, and its secret into **Secret**.
4. Click **Test Webhook**. PFR Sentinel sends a test event (`event` and `event_type` are both `test`, titled "Test Notification") and shows the result next to the button, for example `Success (HTTP 200)`.
5. Turn on the events you want Hermes to receive.

---

## Settings Reference

| Setting | Default | Description |
|---------|---------|-------------|
| **Enable Hermes Webhook** | Off | Master switch. When it's off, nothing is sent to Hermes. |
| **Webhook URL** | — | The base URL events are sent to. It's hidden until you click **Show**. |
| **Secret** | — | Shared secret used to sign every request. Always hidden. |
| **Post Errors** | Off | Sends `error` events. |
| **Post Startup/Shutdown** | Off | Sends `lifecycle` events when PFR Sentinel starts and when you exit it. |
| **Post Roof Changes** | Off | Sends `roof_changed` events when the roof classifier confirms the roof has opened or closed. Needs **Enable ML Analysis** on. |
| **Post Timelapse** | Off | Sends `timelapse_done` events when a timelapse video finishes. |
| **Post Calibration** | Off | Sends `calibration_done` events when **Calibrate Now** or a guided all-sky calibration succeeds. |
| **Periodic Image Updates** | Off | Sends `periodic_image` events at a regular interval. Needs the web server or Discord enabled. See [Periodic image updates](#periodic-image-updates). |
| **Route Events to Separate URLs** | Off | Shows one URL field per event type. The fields show their URLs in plain text, not hidden. See [Per-Event URLs](#per-event-urls). |

---

## Events

| Event (`event_type`) | Switch | When it's sent |
|----------------------|--------|----------------|
| `lifecycle` | **Post Startup/Shutdown** | About a second after PFR Sentinel opens (`phase` is `startup`), and when you exit it (`phase` is `shutdown`). With **Enable System Tray** on, closing the window only hides it to the tray, so no `shutdown` event is sent until you exit from the tray menu. |
| `error` | **Post Errors** | When capture fails to start, when the camera reports an error, or when writing the ASCOM roof safety file fails. See [Camera errors during recovery](#camera-errors-during-recovery). |
| `roof_changed` | **Post Roof Changes** | When the roof classifier reports the same new state on two frames in a row. Needs **Enable ML Analysis** turned on and the roof model installed; there's no separate switch per classifier. See [ML Models](ML-Models). |
| `timelapse_done` | **Post Timelapse** | When a timelapse video finishes. Only the details are sent, not the video. |
| `calibration_done` | **Post Calibration** | When **Calibrate Now** or a guided all-sky calibration completes successfully. Automatic background refinements and failed calibrations send nothing. See [All-Sky Overlay](All-Sky-Overlay). |
| `periodic_image` | **Periodic Image Updates** | At the periodic update interval. See below. |

Starting or stopping capture doesn't send an event to Hermes. The **Post Startup/Shutdown** switch covers starting and exiting PFR Sentinel only, even though its description mentions capture.

### Camera errors during recovery

When the camera drops out, PFR Sentinel tries to recover it automatically. Camera errors are sent for the first three recovery attempts. After that, further camera errors are held back so the agent isn't flooded with retry messages. Sending starts again when you stop and start capture, or when frames arrive again after an outage of more than five minutes. If PFR Sentinel judges the camera can't be recovered, it sends one final error. Discord follows the same rule.

### Periodic image updates

Hermes has no interval setting of its own. It uses the same schedule as Discord periodic updates:

- The first `periodic_image` event goes out with the first image processed after PFR Sentinel starts. After that, one goes out every **Interval** minutes, shortened by a random amount of up to 5 minutes. Updates are only checked when a new image is processed.
- **Interval** is the setting in the **Discord Integration** card (default 60 minutes, minimum 30). It only appears there while Discord's **Periodic Updates** switch is on. Turn that switch on to change the interval, then turn it off again if you only want Hermes updates.
- Periodic events are only produced while **Enable Web Server** or **Enable Discord Alerts** is on. For Hermes, enable the [Web Server](Web-Server): you'll want it anyway so the agent can fetch the image.

---

## Per-Event URLs

By default every event goes to the base **Webhook URL**. Turn on **Route Events to Separate URLs** to send each event type to its own webhook route. Hermes can then run a focused subscription per event, for example one agent for roof and error alerts and another for nightly timelapse summaries.

| Field | Event type |
|-------|-----------|
| **Error** | `error` |
| **Roof Changes** | `roof_changed` |
| **Periodic Image** | `periodic_image` |
| **Startup/Shutdown** | `lifecycle` |
| **Timelapse** | `timelapse_done` |
| **Calibration** | `calibration_done` |

- A blank field falls back to the base **Webhook URL**.
- The event switches still decide *whether* an event is sent. The URLs only decide *where* it goes.
- One **Secret** signs requests to every URL.
- You can leave the base URL blank if every event you've switched on has its own URL. Any event with neither URL is skipped, and a warning is logged.
- **Test Webhook** always uses the base **Webhook URL**.

Instead of separate URLs, you can use one route and let Hermes filter on the `event_type` field.

---

## What Hermes Receives

Every event is a JSON `POST` with these fields:

| Field | Description |
|-------|-------------|
| `event` | The event type, for example `roof_changed`. |
| `event_type` | The same value as `event`. Hermes subscription filters match on this field. |
| `level` | `info`, `warning`, `error` or `success`. |
| `title` | A short title, for example "Roof Closed". |
| `body` | A readable one-line summary. |
| `source` | `PFR Sentinel`. |
| `timestamp` | When the event was sent, in UTC, for example `2026-07-07T21:14:00Z`. |
| `image` | Optional. `{ "id": ..., "url": ... }` pointing at the most recently archived [Image Library](Image-Library) image. See below. |

Each event also carries one block of its own details:

| Event | Block | Fields |
|-------|-------|--------|
| `roof_changed` | `roof` | `open` (true/false), `confidence` (0–1) |
| `error` | `error` | `text` |
| `lifecycle` | `lifecycle` | `phase` (`startup` or `shutdown`), `mode` (`camera` or `watch`), `output_path` |
| `periodic_image` | `capture` | `exposure`, `gain`, `temp`, `resolution`. Each value is text as the overlay shows it, for example `"2.50s"`, or `"N/A"` when unknown. |
| `timelapse_done` | `timelapse` | `frame_count`, `elapsed_seconds`, `filename` |
| `calibration_done` | `calibration` | `rms_residual`, `n_matches`, `calibrated_at`, `a1`, `cx`, `cy` |

Example:

```json
{
  "event": "roof_changed",
  "event_type": "roof_changed",
  "level": "warning",
  "title": "Roof Closed",
  "body": "Roof is now Closed (confidence: 94%)",
  "source": "PFR Sentinel",
  "timestamp": "2026-07-07T21:14:00Z",
  "image": { "id": 1234, "url": "http://127.0.0.1:8080/library/image?id=1234" },
  "roof": { "open": false, "confidence": 0.94 }
}
```

The same schema is described in the API reference at the web server's `/docs` page.

### Images

No image data is ever sent to Hermes. When an image is available, the event includes a link the agent can fetch instead:

- The link points at the most recent frame stored in the [Image Library](Image-Library) when the event is sent. That isn't necessarily the frame that triggered the event. Until the library has stored at least one frame since PFR Sentinel started, events have no `image` block.
- The Image Library and its **Expose Web API** setting must both be on (they are by default).
- With [Output Framing](Image-Processing#output-framing) set up (new in the next release), library images, and so the linked image, are the cropped frame.
- The link is built from the web server's **Host** and **Port**. A **Host** of `0.0.0.0` becomes `127.0.0.1` in the link.
- The web server must be running for the link to work. If the Hermes agent runs on another machine, set **Host** to an address that machine can reach, not `127.0.0.1`. See [Web Server](Web-Server#network-access).

---

## Signing

Each request carries two headers that Hermes uses to check it really came from PFR Sentinel:

| Header | Value |
|--------|-------|
| `X-Webhook-Timestamp` | The time the request was sent, in Unix seconds. |
| `X-Webhook-Signature-V2` | An HMAC-SHA256 hex digest, made with your **Secret**, of the timestamp, a dot, and the exact request body. |

Hermes' documentation says it only accepts requests whose timestamp is close to its own clock, so keep the observatory PC's clock synchronised. If every request fails with HTTP 401, check the **Secret** first, then the PC's clock.

---

## Delivery

- Events are queued and sent in the background, in order, so capture is never held up.
- Each request has a 10-second timeout. After a timeout or connection error it's retried after 1 second and again after 4 seconds. If the endpoint replies HTTP 429 (too many requests), PFR Sentinel waits as long as it asks, then retries.
- A reply other than 2xx isn't retried. The status code and the start of the reply are written to the [Logs](Logs).
- Successful sends are logged as "Hermes notification sent".
- The URL and secret are removed from any error written to the log.

---

## Troubleshooting

| Result | What to check |
|--------|---------------|
| **Webhook URL required** | Enter a base **Webhook URL**. The test button needs one even when per-event URLs are set. |
| **Hermes URL or secret not configured** | Enter the **Secret** as well. The test won't send without one. |
| **Failed: HTTP 401** | The secret doesn't match the route's secret, or the PC's clock is off. |
| **Failed: HTTP 404** | The URL is wrong, or the route doesn't exist on the Hermes side. |
| **ConnectionError: …** or **ReadTimeout: …** | PFR Sentinel couldn't reach the URL, or Hermes didn't answer within 10 seconds. Check the address, that Hermes is running, and that this PC can reach it on the network. |
| Test works but no events arrive | Check that the event's switch is on. For periodic images, check that the web server or Discord is enabled. For roof changes, check that **Enable ML Analysis** is on and the roof model is installed. |
| The agent can't open the image link | Make sure the web server is running and its **Host** is reachable from the agent's machine, and that the Image Library's **Expose Web API** is on. |
