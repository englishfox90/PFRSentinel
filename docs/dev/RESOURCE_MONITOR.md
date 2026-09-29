# Resource monitor

`services/resource_monitor.py` + `ui/controllers/resource_monitor_controller.py`.
Started by `_start_resource_monitor` in `ui/main_window/lifecycle.py` in **every
build**: a memory complaint from a production user arrives as a diagnostics
bundle, and this is what lets the bundle answer it. Only the tracemalloc
option below is dev-only. Cost: one counter read and a few attribute reads
every 5 min, and 10–30 INFO lines a night.

## Why

The operator judges health from Task Manager's "Memory" column. That column is
the private *working set*: resident private pages. Windows never trims a
process's working set while there is no memory pressure, so between trims the
number sits near the high-water mark of everything a frame touched — it is not
a measure of what the app is holding. `services/working_set.py` lowers it on
capture stop and every 100 frames without freeing a byte.

The number that tracks real retention is the process's committed private bytes
(`PagefileUsage` from `GetProcessMemoryInfo`), which a trim cannot touch. The
monitor logs both, so a climb can be read from the log:

| Working set | Private bytes | Reading |
|---|---|---|
| high, saw-tooths at trims | flat | cosmetic — resident-but-untouched pages |
| high | rising sample after sample | retention: something is holding frames |
| peak much higher than steady | flat | per-frame transient (stretch, ML, overlay copies) |

## What is logged

One `INFO` line, rate-limited: on startup, at capture start/stop, after the
post-stop working-set trim, whenever private bytes move by ≥ 64 MB since the
last line, and on a 30-min heartbeat otherwise. Sampled every
`diagnostics.resource_log_interval_s` (default 300).

```
Resources [capture started]: working set 1,412 MB (peak 2,105) · private 987 MB (+64 since last report) · CPU 9.8% · 23 threads · allsky_buffer=60 · allsky_ring=14 · meteor_stack=6 · meteor_stack_mb=31 · processor_queue=0 · overlay_cache=1 · timelapse_queue=0 · cached_frame_mb=75 · web_latest_mb=2
```

- `CPU` is process CPU time over wall time over all cores since the previous
  sample — Task Manager's definition. It counts every thread of this process
  but **not the ffmpeg child**.
- Gauges: all-sky calibration buffer and same-night ring (frame dicts —
  detections + catalogue, never images), meteor frame stack (bytes at detection
  scale), image-processor and timelapse queue depths, overlay image cache
  entries, the Calibrate-Now / reprocess cache (PIL image + raw Bayer bytes),
  the web server's latest encoded frame.

A `WARNING` fires when private bytes rise ≥ 256 MB within an hour, have held
for at least half of it and are still at the top — a spike that fell back, and
the expected step up at capture start (`new_regime=True` restarts the window),
never warn. A climb that never pauses warns again after each further 256 MB.

The latest sample is written to `summary.json` → `resources` by Export
Diagnostics.

## Python-heap hotspots (opt-in, dev builds only)

`diagnostics.memory_trace: true` in `config.json` (no UI) starts `tracemalloc`
in a dev build; a production build logs one line saying the flag is ignored and
carries on without hotspots. The trace runs from launch and appends the top-10
allocation sites by size, plus what grew since the previous line, after every
resource line. It costs CPU on every allocation
and the retained snapshot is not free — turn it on only while chasing a climb.
It sees Python-side allocations, numpy arrays included; **Pillow's own image
buffers are invisible to it**. A rise in `private_mb` with nothing growing in
the trace therefore points at PIL images (or a C library), which is itself a
finding.

## Reading a report from a rig

1. Find the `Resources [capture started]` line and the last heartbeat.
2. If `private` is flat and `working set` is high: nothing is leaking; the
   number is the per-frame peak. `peak` tells you how high the transient goes.
3. If `private` climbs: compare the gauges between the two lines — a growing
   `allsky_buffer`/`allsky_ring` saturates at 60/36, `meteor_stack` at its
   configured depth, queues should read 0 between frames. A climb with every
   gauge flat is a holder the gauges don't cover: turn on `memory_trace`.
