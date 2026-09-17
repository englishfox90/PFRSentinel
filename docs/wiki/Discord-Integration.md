# Discord Integration

PFR Sentinel can post alerts, periodic status updates with the latest image, and finished timelapse videos to a Discord channel through a webhook. Discord is set up in the **Discord Integration** card on the [Output Settings](Output-Settings) tab. It works alongside [Hermes Notifications](Hermes-Notifications): you can turn on either one, both or neither.

---

## Setup

1. In Discord, open **Server Settings > Integrations > Webhooks**.
2. Create a webhook and choose the channel it posts to.
3. Copy the webhook URL.
4. In PFR Sentinel, open the **Output** tab and expand **Discord Integration**.
5. Turn on **Enable Discord Alerts** and paste the URL into **Webhook URL**.
6. Click **Test Webhook**. A test message should appear in the channel, and the result shows next to the button.
7. Turn on the notifications you want (see below).

Treat the webhook URL like a password: anyone who has it can post to your channel. PFR Sentinel hides it in the field until you click **Show**, and removes it from any error it writes to the log.

---

## Settings Reference

| Setting | Default | Range | Description |
|---------|---------|-------|-------------|
| **Enable Discord Alerts** | Off | — | Master switch. When it's off, nothing is posted. |
| **Webhook URL** | — | — | Your Discord webhook URL. Required. |
| **Post Errors** | Off | — | Posts capture and camera errors. |
| **Post Start/Stop** | Off | — | Posts when PFR Sentinel starts, when capture starts, and when you exit PFR Sentinel. |
| **Post Timelapse Video** | Off | — | Posts each finished timelapse, with the video attached if it's 8 MB or smaller. |
| **Post Roof Changes** | Off | — | Posts when the roof classifier confirms the roof has opened or closed. Needs **Enable ML Analysis** on. |
| **Periodic Updates** | Off | — | Posts a status update at a regular interval. Turning it on shows the next two settings. |
| **Interval** | 60 min | 30–1440 min | Time between periodic updates. |
| **Include Latest Image** | On | — | Attaches the latest saved image to periodic updates and roof-change posts. The switch is only shown while **Periodic Updates** is on, but it also governs roof-change posts when **Periodic Updates** is off. |
| **Embed Color** | #0EA5E9 (cyan) | — | Colour of the stripe down the side of each message. Click to pick a colour. |

---

## What Gets Posted

| Message | Setting | When it's posted |
|---------|---------|------------------|
| PFR Sentinel Started | **Post Start/Stop** | About a second after PFR Sentinel opens. Shows the capture mode and output folder. |
| Capture Started | **Post Start/Stop** | When capture starts. Shows the capture mode and output folder. |
| PFR Sentinel Stopped | **Post Start/Stop** | When you exit PFR Sentinel. With **Enable System Tray** on (see [Settings](Settings)), closing the window only hides it to the tray, so nothing is posted until you exit from the tray menu. |
| Error Detected | **Post Errors** | When capture fails to start, when the camera reports an error, or when writing the ASCOM roof safety file fails. Long messages are cut off at 1000 characters. |
| Roof Open / Roof Closed | **Post Roof Changes** | When the roof classifier reports the same new roof state on two frames in a row. Needs **Enable ML Analysis** turned on and the roof model installed. There's no separate switch per classifier. See [ML Models](ML-Models). The first reading after PFR Sentinel starts only sets the starting state, so it isn't posted. |
| Timelapse Complete | **Post Timelapse Video** | When a timelapse video finishes. Shows the frame count, session length and file name. Timelapse recording only works in ZWO camera mode. |
| PFR Sentinel - Status Update | **Periodic Updates** | At your chosen interval. See [Periodic Updates](#periodic-updates). |

Stopping capture doesn't post a message. A post only goes out when **Enable Discord Alerts** and the matching setting are both on.

**Camera errors during recovery:** when the camera drops out, PFR Sentinel tries to recover it automatically. Camera errors are posted for the first three recovery attempts. After that, further camera errors are held back so the channel isn't flooded with retry messages. Posting starts again when you stop and start capture, or when frames arrive again after an outage of more than five minutes. If PFR Sentinel judges the camera can't be recovered, it posts one final error.

---

## Periodic Updates

With **Periodic Updates** on, the first status update goes out with the first image processed after PFR Sentinel starts. After that, one goes out every **Interval** minutes.

- PFR Sentinel only checks whether an update is due when a new image is processed, so updates line up with your capture interval. If nothing is being captured, nothing is posted.
- To spread out network traffic, each cycle is shortened by a random amount of up to 5 minutes.
- Each update is titled "PFR Sentinel - Status Update" and shows the number of images processed, the current exposure and gain while the camera is capturing, and the time.
- If **Include Latest Image** is on, the latest saved image is attached. With [Output Framing](Image-Processing#output-framing) set up (new in the next release), that's the cropped frame.

For an 8-hour night, a 60-minute interval gives about eight updates. That's enough to follow the session without flooding the channel.

---

## Image Handling

Before PFR Sentinel attaches an image to a post:

1. It loads the latest saved image from your output directory.
2. If the image is taller than 750 pixels, it's scaled down to 750 pixels tall, keeping its proportions.
3. It's converted to a quality-85 JPEG.
4. If the result is still bigger than 1 MB, **the post isn't sent at all**, and a warning is written to the log.

The image appears directly in the message, not as a separate download. It's your saved image, so the [All-Sky Overlay](All-Sky-Overlay) only appears if you turn on **Saved image (also Discord)** under **Burn overlay into output** in the All-Sky settings.

---

## Timelapse Videos

When **Post Timelapse Video** is on and a timelapse finishes:

- A video of **8 MB or less** is attached. Discord shows it as a player right in the channel. The upload has up to 120 seconds to finish.
- For a video **larger than 8 MB**, a text-only message is posted instead, noting the file size. The video stays in your timelapse folder for you to share another way.

To keep videos under 8 MB, choose a lower output resolution or a smaller quality setting on the [Timelapse](Timelapse) tab. To publish full-size videos, see [YouTube Uploads](YouTube-Uploads).

---

## Message Format

Every message is a Discord embed with:

| Element | Content |
|---------|---------|
| Title | What happened, for example "Roof Closed" or "Timelapse Complete". |
| Description | The details. |
| Colour | Your **Embed Color**. |
| Footer | The message level with a matching icon: INFO, WARNING, ERROR or SUCCESS. |
| Timestamp | When it was sent. |

Messages are posted under the name **PFR Sentinel**.

---

## Delivery and Rate Limits

- Posts are queued and sent in the background, one at a time, so a slow connection never holds up capture. A slow Hermes endpoint doesn't delay Discord posts either.
- If Discord replies that you're sending too fast (HTTP 429), PFR Sentinel waits as long as Discord asks, then tries again.
- If a post times out or can't connect, it's retried after 1 second and again after 4 seconds.
- A post that still fails is dropped, and the reason is written to the [Logs](Logs).

---

## Tips

- **Post Errors** is the most useful setting for remote monitoring: if the camera stops responding overnight, you'll know without logging in.
- If a periodic update never shows up, check the [Logs](Logs) for these messages:
  - "Discord image too large": the image was over the 1 MB limit even after shrinking.
  - "Discord webhook failed": Discord rejected the post. The message includes Discord's reply.
  - "Discord webhook timeout": Discord didn't answer in time.
  - "Discord webhook connection error: …": PFR Sentinel couldn't reach Discord, for example because the internet connection is down.
- **Test Webhook** always sends to the URL in the field, even while **Enable Discord Alerts** is off. You can check the webhook before turning anything on.
- There's no Discord switch for all-sky calibration notifications. That event is available through [Hermes Notifications](Hermes-Notifications).
