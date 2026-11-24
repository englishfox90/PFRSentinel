# scripts/dev/

Archaeological / experimental scripts — **not shipped, not called at runtime**. These live here so `scripts/` root stays clean and reflects only the build/release/admin tools that are actively used.

Subdirectories:
- `allsky/` — fisheye calibration experiments
- `image/` — colorize / stretch / noise-model experiments

Also here:
- `test_usb_reset.py` — interactive USB reset test, requires a physical camera. Not part of the pytest suite.
- `alpaca_camera_sim.py` (+ `alpaca_sim/`) — an ASCOM Alpaca camera simulator: a real HTTP Alpaca server, the Management API and UDP discovery on 32227, serving a night-sky scene whose brightness follows Duration × Gain, in ImageBytes or JSON. Use it to try Alpaca Camera mode (issue #43) without hardware. See below.
- `render_conditions_views.py` — screenshots of the imaging-conditions views from synthetic data (a bright Moon high up, NINA's target close to it): the Live Monitoring conditions panel with a learned baseline, while learning and with the target far from the Moon, and the NINA & ASCOM Imaging Conditions card expanded with Advanced open. Offscreen, 2x; never touches the real app-data folder. `python scripts/dev/render_conditions_views.py --out <dir>`
- `render_live_monitoring.py` — the same views IN CONTEXT: the real main window on Live Monitoring with the conditions panel beside the preview card (no frame judged yet, with the pointer resting on the panel, OK, paused; expanded and collapsed; 1440 px and 1000 px wide) and the NINA & ASCOM page with the Imaging Conditions card collapsed and expanded. Fed through the real controller from stand-in data; starts no capture, sends no analytics, never touches the real app-data folder. `python scripts/dev/render_live_monitoring.py --out <dir>`
- `render_library_grade.py` — screenshots of the Library's imaging-conditions views: builds a synthetic night (clear → cloudy → clear, a few ungraded frames, drawn thumbnails) in a temporary library, opens the real Library panel with the app theme, and writes `night_clear.png`, `night_cloudy.png`, `night_ungraded.png` and `session_list.png` at 2x. Offscreen; never touches the real app-data folder. `python scripts/dev/render_library_grade.py --out <dir>`

## Testing Alpaca Camera mode with the simulator

```bash
python scripts/dev/alpaca_camera_sim.py --port 11112            # one colour camera on 127.0.0.1:11112
python scripts/dev/alpaca_camera_sim.py --host 0.0.0.0 --port 11112   # reachable from the LAN
python scripts/dev/alpaca_camera_sim.py --sensor-type 2 --bayer-offset-x 1 --bayer-offset-y 1 \
    --width 1920 --height 1080 --max-adu 4095                   # a different Bayer phase and bit depth
python scripts/dev/alpaca_camera_sim.py --help                  # every option
```

Then in the app: **Capture** tab → **Alpaca Camera** → **Discover** (or Host `127.0.0.1`, Port `11112`, Device `0`) → **Test Connection** → **Start Capture**.

- Don't use port 11111 when PFR's own Alpaca SafetyMonitor is on: it is that server's default too. `--port 0` takes a free port and logs it as `ALPACA_SIM_PORT=<n>`.
- Useful flags: `--sensor-type` (0 mono, 1 RGB planes, 2 RGGB Bayer, 3–5 CMYG / CMYG2 / LRGB), `--bayer-offset-x/-y`, `--max-adu`, `--gain-mode` / `--offset-mode` (`range`, `list`, `none`), `--no-temperature`, `--no-imagebytes` (JSON only), `--interface-version 3|4`, `--no-discovery`, `--readout-s`, `--scene`, `--lenient-params`.
- The simulator also backs `tests/test_alpaca_sim.py` (our client, ASCOM's `alpyca` when installed, discovery, CLI) and `tests/test_alpaca_session_live.py` (a full `AlpacaCamera` session with auto-exposure), both `requires_network`. In-process tests use the same device model through `tests/alpaca_fake.py`, with no sockets.

For the production entry points for any of the above, see `services/` or `ui/controllers/`.
