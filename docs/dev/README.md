# docs/dev/

Developer-facing technical reference. End-user content lives on the project wiki.

## Feature & architecture references

| Doc | What it covers |
|-----|----------------|
| [ALLSKY_OVERLAY.md](ALLSKY_OVERLAY.md) | Constellation/planet/DSO overlay system for the all-sky camera |
| [Output-Filename-Patterns.md](Output-Filename-Patterns.md) | `{filename}`, `{session}`, `{timestamp}` token expansion rules |
| [SHARPENING.md](SHARPENING.md) | Cosmetic unsharp mask — design and tunables |
| [STAR_CATALOG.md](STAR_CATALOG.md) | BSC5/Messier/NGC data formats + Skyfield integration |
| [TIMELAPSE_FEATURE_DESIGN.md](TIMELAPSE_FEATURE_DESIGN.md) | ffmpeg pipe, fragmented MP4 flags, sun/fixed/always windows |
| [PER_CAMERA_SETTINGS.md](PER_CAMERA_SETTINGS.md) | `camera_profiles[clean_name]` design (referenced from `.claude/rules/services-camera.md`) |
| [posthog.md](posthog.md) | Analytics event names + helpers (referenced from `.claude/rules/python-general.md`) |
| [CAMERA_USB_RESET.md](CAMERA_USB_RESET.md) | Windows `CM_Reenumerate_DevNode` USB reset implementation |
| [CAMERA_LOGGING_REFERENCE.md](CAMERA_LOGGING_REFERENCE.md) | Log-search cheat sheet for diagnosing camera issues |
| [DIAGNOSTICS_BUNDLE.md](DIAGNOSTICS_BUNDLE.md) | Logs → Export Diagnostics: bundle contents, redaction, fresh raw-frame capture |

## Build & release tooling

| Doc | What it covers |
|-----|----------------|
| [BUILD.md](BUILD.md) | PyInstaller + Inno Setup build chain |
| [CODE_SIGNING.md](CODE_SIGNING.md) | Certum Open Source Developer cert — current signing process |
| [CODE_SIGNING_WITH_SIGNTOOL.md](CODE_SIGNING_WITH_SIGNTOOL.md) | Alternative signing path using Windows SignTool |
| [VIRUSTOTAL_SCANNING.md](VIRUSTOTAL_SCANNING.md) | Pre-release AV false-positive scanning workflow |
| [PRODUCTION_BUILD.md](PRODUCTION_BUILD.md) | `DEV_MODE_AVAILABLE` flag — what gets disabled in release builds |

## Vendor reference

- `ASICamera2 Software Development Kit.pdf` — ZWO ASI SDK manual

## Active plans (at `docs/` root, one level up)

- [`../ALLSKY_CALIBRATION_PLAN.md`](../ALLSKY_CALIBRATION_PLAN.md) — all-sky calibration design
- [`../CODE_QUALITY_PLAN.md`](../CODE_QUALITY_PLAN.md) — code quality + structure roadmap
