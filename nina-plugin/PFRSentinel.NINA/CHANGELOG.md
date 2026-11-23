# PFR Sentinel

## 1.3.0.0
- Messages about the control API point to Sentinel's new **NINA & ASCOM** page
  (the Output tab in Sentinel 3.8 and earlier).
- Advanced Sequencer trigger **Sentinel: pause on bad conditions**: before each
  exposure it reads Sentinel's `GET /conditions` verdict and, when the sky is not
  good enough, waits until it has stayed good for the settle time (default 60 s).
  Optional maximum wait (0 = no limit) that either continues the sequence or fails
  the trigger. Only waits: it does not stop guiding or tracking, park or close
  anything. **Only before exposures** (on by default) keeps a park or warm-up at
  the end of the sequence from being held back.
- Loop condition **Sentinel: loop while conditions OK**: loops a container while
  the sky stays good, ends it once it is not.
- Both follow Sentinel's `imaging_ok` and take optional limits of their own: min
  star ratio, max cloud cover %, star count drop % against recent frames, FWHM
  rise % against the night median, max FWHM in arcseconds or pixels, and require
  roof Open. While Sentinel is still learning this sky's clear-sky baseline, the
  star ratio and cloud cover limits are skipped with a note; the change % limits
  still apply. A limit whose measurement is missing is skipped, never treated as
  crossed.
- **No verdict = bad** (on by default): an unreachable Sentinel, a rejected token,
  a stale verdict, warming up, conditions switched off or a Sentinel without
  `/conditions` pauses the trigger and ends the loop. The item's status line names
  the reason; NINA validation reports settings problems only, because NINA fails a
  trigger on any validation issue.
- The verdict is Sentinel's `imaging_ok`, after its own bad/good-frame hysteresis,
  never "reasons is empty": during Sentinel's grace frames a bad latest frame is
  shown as a note while imaging continues. `recovering` (good frames, not enough in
  a row yet) counts as not OK; `warming_up`, `stale` and `disabled` are no verdict.
- One shared, throttled read of `/conditions` serves every item and the panel: at
  most one request in flight, an answer reused for 5 s.
- Imaging-tab panel: a **Conditions** column with the verdict, reasons in plain
  words, roof, sky, stars and ratio, star change %, cloud cover, FWHM and its
  change against the night median, sun altitude, baseline status and age.
- Needs a Sentinel newer than 3.8.0 (imaging conditions).

## 1.2.1.0
- Target push logging is visible at NINA's default Info level: the push switching on
  or off, "no deep-sky target is running" once while idle, each target sent (and
  whether it carried a field of view) or cleared, and the push recovering after a
  failure. Heartbeats stay silent.
- When no field of view can be worked out, one Info line names the missing setting
  (telescope focal length, camera pixel size or sensor size); Sentinel draws a
  reticle instead of a box until it is set.
- A sequencer read that fails is now a Warning, not Debug.
- Imaging-tab panel: an **All-sky target** column shows what was last sent to
  Sentinel (status, name, RA/Dec, field of view, rotation, time since Sentinel
  accepted it) and, in amber, why a send failed. It sits beside the statistics
  when the panel is wide enough and under them when it is not.
- Panel statistics trimmed: Frame age (the frame line already shows it), Images
  served and Uptime are gone.

## 1.2.0.0
- Target push: while a deep-sky target is running in the sequencer, the plugin
  sends its name, J2000 RA/Dec, the imaging camera's field of view (pixel size x
  sensor size / focal length) and the position angle to Sentinel's
  `POST /nina/target`, so the all-sky overlay can mark where the main scope is
  pointed. Sent on change and every 30 s; one clear when the target ends.
  Uses the capture-control token already used for Start/Stop.
- Options page: "Send the current sequencer target to Sentinel" (on by default).
- Needs a Sentinel that has the route; an older one is reported once in NINA's log
  ("HTTP 404 ... update Sentinel") and retried every 10 minutes.
- A rejected token, refused Host or disabled control API is retried with a
  back-off that doubles up to 10 minutes; changing any plugin option retries at once.

## 1.1.0.0
- Imaging-tab panel: live frame with staleness indication, Sentinel health line
  and reasons, read-only capture statistics, and Start/Stop.
- Advanced Sequencer instructions: Start / Stop Sentinel Capture, with a
  `Validate()` pre-flight so an unreachable or unconfigured Sentinel is reported
  when the sequence is built rather than when it runs.
- Optional base-URL override on the options page, applied live.
- Plugin and instruction icons drawn from the PFR Sentinel aperture mark.

## 1.0.0.1
- Initial scaffold (item 1a). Template surface only; Stage 1b/2 replace the bodies.
