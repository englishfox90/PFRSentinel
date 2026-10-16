# ZWO ASI SDK on Windows, macOS and Linux

`zwoasi` is a ctypes wrapper: it loads whichever shared library it is handed and
binds the same C functions on every OS. Nothing about the capture path
(`services/camera/zwo_camera.py`), the BGGR debayer, or the ms-to-seconds
exposure boundary is platform-specific. The only per-platform facts are the
library's **filename** and **where it is**, and both live in one module:
`services/zwo_sdk_library.py`.

## The rule

Never spell the library name, and never hand `zwoasi.init()` a bare filename.
Ask `resolve_library_path(configured)` and pass on the absolute path it returns.

A bare name only resolves through the OS loader's own search path. On Windows
`main.py` adds the source folder with `os.add_dll_directory()`; macOS and Linux
have no equivalent (`LD_LIBRARY_PATH` / `DYLD_LIBRARY_PATH` are read once at
process start, and macOS strips the latter for protected processes), so an
absolute path is the only thing that works everywhere.

| OS | Library |
|----|---------|
| Windows | `ASICamera2.dll` |
| macOS | `libASICamera2.dylib` (universal x86_64 + arm64 in current SDKs) |
| Linux | `libASICamera2.so` (ZWO ships x64, x86, armv6, armv7, armv8 builds) |

## Search order

`resolve_library_path()` uses the configured `zwo_sdk_path` if that file exists,
and otherwise the first hit from `search_dirs()`:

1. The bundled location — repo root from source, `_MEIPASS` when frozen.
2. Frozen builds only: the executable's folder and its `_internal/`.
3. `<app-data root>/sdk/` — a per-user drop folder. No admin rights, survives
   upgrades. This is what the "library not found" log message points people at.
4. System locations:
   - Windows: `%PROGRAMFILES(X86)%\PFRSentinel\_internal`, `%PROGRAMFILES%\PFRSentinel\_internal`
   - macOS: `/usr/local/lib`, `/opt/homebrew/lib`, `/opt/local/lib`
   - Linux: `/usr/local/lib/<multiarch>`, `/usr/lib/<multiarch>`, `/usr/local/lib`,
     `/usr/lib64`, `/usr/lib`. The multiarch folder is where INDI's `libasi`
     package installs, so a machine that already runs KStars/Ekos or
     indi-allsky needs no download at all.

Off Windows a versioned file (`libASICamera2.so.1.37`, `libASICamera2.dylib.1.37`)
is accepted when the bare name is missing, newest first. Files under 4 KB are
ignored: ZWO ships the bare name as a symlink, and copied off a git host or
unzipped on Windows it turns into a 20-byte text file that exists but cannot be
loaded.

A stale configured path is normal and is not an error: the search runs instead
and the log says which library was used. On load, `Config` rewrites
`zwo_sdk_path` only when it names *another platform's* library (a Windows config
carried to Linux, or a pre-port config written off Windows, when the default
named the DLL everywhere). A missing path with the right name is left alone — it
may be a drive that is not mounted yet.

## Why the macOS/Linux libraries are not in git

Decision for issue #39: **not committed, not fetched at build time.**

- ZWO's SDK licence (MIT-style, `license.txt` in the SDK archive) permits
  redistribution, so this is not a legal constraint.
- It is a per-CPU set — one dylib plus an `.so` per architecture — that must
  track the SDK version the Windows DLL is on. Six binaries, ~20 MB, re-committed
  on every SDK bump, for platforms that have no packaged build yet (issue #41).
- ZWO's download URLs are not stable and the archive is not checksummed by ZWO,
  so a build-time fetch would be a supply-chain hole or a flaky build.
- Third-party mirrors lag. The copy in `esrf-bliss/Lima-camera-zwo` is SDK 1.26,
  which predates the ASI676MC — this project's primary camera — so a
  convenient mirror is not necessarily a usable one. Always check the version.
- Many Linux astro machines already have it through INDI.

`PFRSentinel.spec` bundles the platform's library when the builder has dropped
it at the repo root (it is git-ignored) and warns otherwise, so #41 can decide
per-platform packaging without touching the loader.

## Linux host setup

Two host settings, both fixed by `installer/linux/asi.rules` (the same two
lines as the `asi.rules` in ZWO's SDK):

```bash
sudo install -m 644 installer/linux/asi.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules
# unplug and replug the camera, then:
cat /sys/module/usbcore/parameters/usbfs_memory_mb   # expect 200
```

- **Device permissions.** Without `MODE="0666"` on vendor `03c3` the SDK lists
  the camera but every open fails for a non-root user.
- **`usbfs_memory_mb`.** The kernel default is 16 MB; ZWO recommends 200. Too
  small shows up as exposure timeouts or broken frames on long or
  full-resolution exposures — it looks like a camera fault.

`services/camera/linux_usb_preflight.py` checks both after SDK init and logs a
warning with the fix, once per process. It never blocks a connect.

## USB recovery off Windows

`services/usb_reset_win.py` (SetupAPI + `CM_Reenumerate_DevNode`) has no macOS or
Linux counterpart, and **none is planned in this phase**. `CameraConnection`
leaves `_usb_reset_available` False off Windows, the recovery ladder skips its
USB steps, and the UI hides Revive (`MissingCameraNotice`). What remains is what
`.claude/rules/services-camera.md` requires to stay idempotent anyway:
`stop_capture()`, the plain reconnect path, and the app-restart policy.

A Linux reset is feasible later without new dependencies — `USBDEVFS_RESET`
ioctl on `/dev/bus/usb/BBB/DDD`, which the udev rule above already makes
writable — but it should be built against a rig that actually wedges, not
speculatively. macOS has no unprivileged equivalent.

## Verification status

Verified on Windows: the DLL resolves through the new locator and loads.
**Not yet verified on real hardware on macOS or Linux** — that includes
`services/camera/camera_identity.py`, whose ctypes structs wrap the SDK's own
`ASIGetSerialNumber` (not a Win32 API) and should work unchanged once the
library loads. The hardware tests in `tests/test_camera.py`
(`-m requires_camera`) use the locator, so they run as-is on either OS.
