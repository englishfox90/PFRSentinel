# YouTube Uploads

PFR Sentinel can upload your finished [Timelapse](Timelapse) videos straight to YouTube. Once it's set up, each timelapse is uploaded automatically when it finishes. You can also send the latest one at any time with a single click.

The YouTube controls are in the **YouTube Uploads** card on the [Timelapse](Timelapse) tab. The card starts collapsed, so click its header to open it.

---

## Before You Start

A few things to know before diving in:

- **Use a dedicated Google/YouTube account.** Create a separate account just for observatory uploads — don't use your main Google account or anything holding important personal data. This doesn't get around YouTube's rules; it just limits the fallout if Google ever flags the upload account.
- **Start with `Private` uploads.** Confirm that uploading works at all before switching to `Unlisted` or `Public`.
- **Setup takes about 10 minutes** in the Google Cloud Console. You only do it once.

You'll need:

- A Google account with a YouTube channel already created.
- Access to [Google Cloud Console](https://console.cloud.google.com/).
- PFR Sentinel running on the Windows machine that records timelapses.
- At least one finished `.mp4` timelapse to test with.

---

## Setup Overview

The setup has six steps. The first four happen in Google Cloud Console (a one-time process to get permission for the app to upload on your behalf). The last two happen inside PFR Sentinel.

1. Create a Google Cloud project
2. Enable the YouTube Data API
3. Configure the consent screen
4. Create and download a desktop OAuth client file
5. Connect that file to PFR Sentinel and sign in
6. Run a private test upload

---

## Step 1: Create The Google Cloud Project

1. Open [Google Cloud Console](https://console.cloud.google.com/).
2. Click the **project selector** at the top of the page.
3. Click **New Project**.
4. Give it a clear name, for example `PFR Sentinel YouTube Uploads`.
5. Click **Create**.
6. Make sure the new project is selected before continuing.

---

## Step 2: Enable The YouTube Data API

1. In Google Cloud Console, open **APIs & Services**.
2. Open **Library**.
3. Search for **YouTube Data API v3**.
4. Click it.
5. Click **Enable**.

This is the API PFR Sentinel uses to upload your videos. If you skip it, uploads fail with an "API not enabled" error.

---

## Step 3: Configure The Consent Screen

This is the screen Google shows when you sign in to authorise the app.

1. In Google Cloud Console, open **APIs & Services**.
2. Open **OAuth consent screen** (sometimes shown as **Google Auth Platform**).
3. For most users, choose **External**.
4. Fill in the app name, support email, and developer contact email.
5. Add this scope:

   ```text
   https://www.googleapis.com/auth/youtube.upload
   ```

6. If the app is left in **Testing** mode, you **must** add your dedicated upload account as a **test user** — look for the **Test users** section and click **Add users**, then enter that account's email address.

> If you don't add the account as a test user, sign-in in Step 5 will fail and Google will refuse to let you authenticate. This is the single most common reason the first authentication doesn't work.

### Testing vs. Production — Important

Google offers two modes, and the choice affects how often you'll have to re-authorise:

| Mode | What to expect |
|------|----------------|
| **Testing** | Easiest to set up for a first upload. **But Google's policy for Testing mode expires test-user authorisations 7 days after you consent.** After that, PFR Sentinel can no longer sign in and the card shows **YouTube authorization expired. Re-authenticate from the YouTube card.** until you re-authenticate. |
| **In production** | Avoids the 7-day expiry. However, the upload permission is "sensitive", so Google may ask you to verify the app before it looks fully trusted. |

Unverified apps can also show a warning screen during sign-in, may hit lower user caps, and may effectively limit uploads to `Private`. If you want hands-off, week-after-week uploads, plan to move the app to **In production**. For a quick trial, **Testing** is fine — just expect to re-authenticate weekly.

> Google's reference: [support.google.com/cloud/answer/15549945](https://support.google.com/cloud/answer/15549945)

---

## Step 4: Create A Desktop OAuth Client

This produces the JSON file that links PFR Sentinel to your Cloud project.

1. In Google Cloud Console, open **APIs & Services** then **Credentials**.
2. Click **Create Credentials**.
3. Choose **OAuth client ID**.
4. Choose application type **Desktop app**.
5. Name it `PFR Sentinel Desktop`.
6. Click **Create**.
7. Click **Download JSON**.
8. Save the JSON somewhere safe that you won't accidentally delete.

> **Treat this file like a password.** Don't paste its contents into Discord, GitHub issues, screenshots, or logs.

---

## Step 5: Connect PFR Sentinel

The top of the card always shows the next step, from **Step 0: Turn on uploads** to **Step 3: Upload a private test video**. **Show setup steps** opens a short version of these instructions inside the app.

1. Open PFR Sentinel.
2. Open the [Timelapse](Timelapse) page.
3. Find the **YouTube Uploads** card and click its header to expand it.
4. Turn on **Enable YouTube uploads**.
5. Click **Browse** next to **OAuth JSON**.
6. Select the desktop-client JSON file you downloaded in Step 4.
7. Click **Show advanced settings** and check that **Privacy** is **Private** (the default) for the first test.
8. Click **Authenticate**. The button only becomes available once uploads are enabled and a JSON file is selected.
9. A browser window opens. Sign in with your dedicated upload account.
10. You'll probably see a **"Google hasn't verified this app"** warning. That's expected for a personal app that hasn't been through Google's verification, and it doesn't mean anything is wrong. Click **Advanced** (or **Continue**) rather than **Back to safety**, then continue to the app.
11. Accept the YouTube upload permission.
12. Return to PFR Sentinel. The card should say **YouTube authentication complete.**, and the button now reads **Re-authenticate**.

> Authentication only happens when **you** click **Authenticate** or **Re-authenticate**. PFR Sentinel never opens a browser or asks Google for approval on its own. If an automatic upload finds that you aren't signed in, it skips the upload and the card shows **Authenticate YouTube before uploading.**

---

## Step 6: Test A Private Upload

1. Make sure at least one completed timelapse `.mp4` exists.
2. In the **YouTube Uploads** card, click **Upload latest video**. The button only becomes available once you're signed in.
3. Wait for the status text to show the result. On success, it shows **YouTube upload complete.** with a clickable link to the video.
4. Open **YouTube Studio** for the upload account.
5. Confirm the video is there and set to **Private**.

Once that works, decide whether `Unlisted` or `Public` is right for your channel. If Google keeps forcing uploads to `Private`, that's an account or app-verification issue on Google's side. Publish and verify the OAuth app before assuming PFR Sentinel is broken.

---

## Automatic Uploads

While **Enable YouTube uploads** is on and you're signed in, every timelapse is queued for upload as soon as it finishes. You don't need to click anything.

- **Upload latest video** uploads the newest finished `.mp4` in your timelapse folder. It skips a recording that's still in progress.
- Each video is uploaded only once. Clicking **Upload latest video** again for a video that's already on YouTube shows **This timelapse is already uploaded.**
- Uploads go out one at a time, in the background. Up to five can wait in the queue.
- If you close PFR Sentinel while an upload is running, it finishes the uploads already queued before it fully exits.
- A failed upload is not retried automatically. Once the problem has passed, click **Upload latest video** to send it.

---

## Advanced Settings

Click **Show advanced settings** in the card to change how videos appear on YouTube.

| Setting | Default | Description |
|---------|---------|-------------|
| **Privacy** | Private | **Private**, **Unlisted** or **Public**. Private is safest for first tests and for unverified apps. |
| **Title** | `PFR Sentinel Timelapse {date}` | Video title. Supports the placeholders below. |
| **Description** | `All-sky timelapse recorded by PFR Sentinel.` | Video description. Supports the same placeholders. The next release changes this default, see [Night statistics](#night-statistics). |
| **Tags** | `astronomy, allsky, timelapse` | Comma-separated tags. Duplicates are removed, ignoring upper and lower case. |

| Placeholder | Replaced with |
|-------------|---------------|
| `{date}` | The date the upload was queued (`YYYY-MM-DD`). |
| `{filename}` | The video's file name. |
| `{frame_count}` | Number of frames in the timelapse. |
| `{duration}` | Length of the recording session (`HH:MM:SS`). |
| `{recording}` | `N frames, HH:MM:SS of recording`, or nothing when the frame count isn't known (**Upload latest video**). New in the next release. |
| `{size_mb}` | File size in MB. |

Any other text in braces is left as written. If the braces don't balance (for example, a `{` with no closing `}`), no placeholders in that field are replaced and the whole text is used as typed. For videos sent with **Upload latest video**, PFR Sentinel doesn't know the frame count or session length, so `{frame_count}` becomes `0` and `{duration}` becomes `00:00:00`. Avoid those two placeholders if you upload by hand: use `{recording}` instead, which is left out when they aren't known. The [night statistics](#night-statistics) below don't have this problem either.

Videos are uploaded in YouTube's **People & Blogs** category.

### Night statistics

> **New in the next release** — not available in version 3.7.8 or earlier.

The title and description can also carry statistics for the night the video covers. They come from the [Image Library](Image-Library), so the library must be turned on and must still hold that night's frames (it keeps 7 days by default). For an automatic upload only the frames recorded while the timelapse was running count. For **Upload latest video**, PFR Sentinel takes the night up to the time the video file was last written.

Hover over the **Title** or **Description** box in the card to see the full list.

| Placeholder | Replaced with |
|-------------|---------------|
| `{night}` | The night, named after its evening (`YYYY-MM-DD`). A frame taken before midday belongs to the previous evening's night. |
| `{start_time}` | Time of the first library frame in the video's span (`HH:MM`). |
| `{end_time}` | Time of the last library frame in the video's span (`HH:MM`). |
| `{min_temp}` | Lowest camera sensor temperature, in the weather [Units](Weather-Setup#units) you chose (for example `-3.2°C` or `26.2°F`). |
| `{max_temp}` | Highest camera sensor temperature, in the same units. |
| `{clear_pct}` | Percent of frames with a clear sky, as a number only, so write `{clear_pct}% clear`. |
| `{max_stars}` | Most stars detected in a single frame. |
| `{best_seeing}` | Best seeing of the night, for example `Good (FWHM 2.3 px)`. |
| `{roof}` | `Open`, `Closed`, or `Open 80% of the night` when the roof did both. |
| `{gaps}` | Breaks of more than an hour between frames: `none`, or for example `2 (longest 1h 12m)`. |
| `{weather_summary}` | Cloud cover from the weather service across the night, for example `Cloud cover 5 to 40% (average 18%)`. |
| `{night_summary}` | All of the above that are known, on one line. |

The default description for a new installation is:

```text
All-sky timelapse recorded by PFR Sentinel on {date}.
{recording}

{night_summary}
```

For **Upload latest video** with no library data for the night, only the first line is posted.

An existing description is not changed. To use the night line, add `{night_summary}` (or any of the placeholders above) to yours.

Each statistic is left empty when PFR Sentinel doesn't have it: the library is off, the night's frames have been cleaned up, or the frames didn't record that value (for example, star counts need star detection). An empty statistic never stops the upload. `{weather_summary}` only uses the cloud cover saved with each library frame; PFR Sentinel doesn't contact the weather service when it uploads.

---

## Where Files Are Stored

PFR Sentinel keeps two YouTube files under `%LOCALAPPDATA%\PFRSentinel`:

| File | Purpose |
|------|---------|
| `youtube_token.json` | The sign-in token for your upload account. |
| `youtube_upload_state.json` | Tracks upload status so interrupted uploads can resume. |

These are kept separate from your main configuration, and they're written carefully so a power cut is unlikely to corrupt them.

---

## Troubleshooting

Upload and sign-in problems are written to the log with a short reason. Check the in-app [Logs](Logs) page, or `%LOCALAPPDATA%\PFRSentinel\logs\sentinel.log`, for a line like `YouTube upload failed ...` or `YouTube authentication failed ...`. In version 3.7.6 and earlier, the log file is `%APPDATA%\PFRSentinel\logs\sentinel.log`.

| Message | What to do |
|---------|------------|
| **Enable YouTube uploads first.** | Turn on **Enable YouTube uploads**. |
| **OAuth client JSON was not found.** | Select the downloaded desktop-client JSON again. Make sure it's the *Desktop app* file, not a service-account file. |
| **Authenticate YouTube before uploading.** | Click **Authenticate** and approve access in the browser. |
| **Could not load YouTube authorization. Re-authenticate from the YouTube card.** | The saved sign-in couldn't be read. Click **Re-authenticate**. |
| **This timelapse is already uploaded.** | That video is already on YouTube. Nothing more to do. |
| **This timelapse upload is already in progress.** | Wait for the current upload to finish. If PFR Sentinel closed unexpectedly during that upload, the same video can be retried after 6 hours. |
| **YouTube upload queue is full.** | Five uploads are already waiting. Try again once some have finished. |
| **YouTube upload hit a temporary service or quota limit.** | YouTube was busy or your daily quota ran out. Try again later with **Upload latest video**. |
| The card shows **YouTube authentication failed.** and the browser says access was denied or the app is being tested. | Your account isn't on the test-user list. Go back to Step 3 and add that exact email address under **Test users**, then try **Authenticate** again. |
| **YouTube authorization expired. Re-authenticate from the YouTube card.** | Click **Re-authenticate**. Google's Testing-mode policy causes this every 7 days while the Cloud project is still in **Testing** mode (see Step 3). |
| **YouTube rejected the upload...** | Check that the signed-in account owns or manages the channel, that the channel is allowed to upload, and that the project still has quota. |
| The log shows **YouTube Data API v3 has not been used...** | The YouTube Data API isn't enabled for your project. Complete Step 2 for that exact project, wait a few minutes, then retry. |
| **No completed timelapse video was found.** | Record and finish a timelapse first. PFR Sentinel ignores the file that's still being written, so a recording in progress won't be uploaded. |
| Uploads stop after about a week. | Your app is in **Testing** mode. Move it to **In production** (and complete any verification), or re-authenticate each week. |

---

## Quota Notes

YouTube limits how much each Google Cloud project can do per day — by default, 10,000 "units", and every request costs at least one unit even if it fails. Uploads are the expensive part, so PFR Sentinel uses resumable uploads to make the most of your quota. A failed upload isn't retried automatically, so a failure doesn't keep spending quota. For a typical one-timelapse-per-night setup, you won't get anywhere near the limit.

> Google's quota reference: [developers.google.com/youtube/v3/determine_quota_cost](https://developers.google.com/youtube/v3/determine_quota_cost)

---

## Tips

- Keep the first upload **Private** until you've confirmed the whole chain works end to end.
- If you want reliable, unattended nightly uploads, take the time to move your OAuth app to **In production**. The 7-day re-authentication in Testing mode is the most common surprise.
- The dedicated-account advice is worth following — if anything ever goes wrong with the upload account, you'll be glad it wasn't your main Google account.
