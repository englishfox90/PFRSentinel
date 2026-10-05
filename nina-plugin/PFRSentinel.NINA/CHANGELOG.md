# PFR Sentinel

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
