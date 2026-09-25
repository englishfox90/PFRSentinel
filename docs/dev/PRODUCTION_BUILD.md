# Production Build Configuration

## Dev Mode Control

### What is Dev Mode?

Development Mode enables features useful for debugging and ML training:
- **Raw debug file saving** - Saves FITS/TIFF files to `%LOCALAPPDATA%\PFRSentinel\raw_debug\`
- **Calibration JSON exports** - Detailed image analysis metadata
- **ML predictions in the calibration JSON export** - roof/sky results written into each `calibration_*.json`

**RAW16 camera mode is NOT part of dev mode** - it's a user-facing feature that remains available regardless.

**Per-frame ML inference is NOT part of dev mode either.** `services/ml_service.py` runs in every build when `ml_models.enabled` is on (the "ML Models (Beta)" setting), and the installer bundles both ONNX models. Of the ML code, only the calibration-export loader listed above and model *training* (`ml/train_*.py`, PyTorch) are dev-only.

### Production Build Process

1. **Before building release:** Set `DEV_MODE_AVAILABLE = False` in `services\dev_mode_config.py`
   ```python
   DEV_MODE_AVAILABLE = False  # Disable for production
   ```

2. **Build the executable:**
   ```powershell
   .\build_sentinel_installer.bat
   ```

3. **Verify dev mode disabled:**
   - Run `dist\PFRSentinel\PFRSentinel.exe`
   - Check UI: Developer Mode section should NOT appear in Image Processing page
   - Capture an image: No `raw_debug` folder should be created

4. **After release:** Re-enable for development
   ```python
   DEV_MODE_AVAILABLE = True  # Re-enable for development
   ```

### Why Disable in Production?

1. **Disk space** - Debug files can be 100+ MB per image
2. **Performance** - Skips unnecessary file I/O and analysis
3. **User confusion** - Most users don't need debug features
4. **Debug-only ML output** - the calibration JSON export duplicates ML work the production service already does

### Environment Variable Override

For testing without editing code:
```powershell
# Temporarily disable dev mode
$env:PFRSENTINEL_DEV_MODE = "0"
python main.py

# Normal (respects code setting)
Remove-Item Env:\PFRSENTINEL_DEV_MODE
python main.py
```

### Features Available in Production

These remain accessible regardless of dev mode:
- **RAW16 camera mode** - Full sensor bit depth capture (user feature)
- **Auto stretch** - MTF-based dynamic range optimization
- **Color balance** - Manual/auto white balance
- **All output modes** - File/Web/Discord
- **Overlays** - Full overlay system with tokens
- **Logging** - Normal application logs (not debug stats)

### Dev Mode Status Check

Check current build status:
```python
from services.dev_mode_config import is_dev_mode_available, get_dev_mode_status_message

print(get_dev_mode_status_message())
# Output: "Development features enabled (not a production build)"
# or:     "Development features disabled (production build)"
```

### Build Script Reminders

Both build scripts now show checklist reminders:
- `build_sentinel.bat` - Reminds to verify dev mode disabled
- `build_sentinel_installer.bat` - Shows VirusTotal upload instructions

### Common Mistakes

❌ **DON'T**: Build release with `DEV_MODE_AVAILABLE = True`  
✅ **DO**: Set to `False` before building release

❌ **DON'T**: Commit `DEV_MODE_AVAILABLE = False` to git  
✅ **DO**: Only change for local builds, keep `True` in repository

❌ **DON'T**: Disable RAW16 in production  
✅ **DO**: Keep RAW16 available (it's a user feature, not dev mode)

### Testing Matrix

| Mode | UI Section Visible? | Creates raw_debug? | RAW16 Available? |
|------|--------------------|--------------------|------------------|
| `DEV_MODE_AVAILABLE = True` | ✅ Yes | ✅ Yes (if enabled) | ✅ Yes |
| `DEV_MODE_AVAILABLE = False` | ❌ No | ❌ No | ✅ Yes |

### ML Integration Notes

Two ML code paths exist, and only one is gated by dev mode:

| Path | Gate | Purpose |
|------|------|---------|
| `services/ml_service.py` (`get_ml_service()`, `MLService.analyze_image`) | `ml_models.enabled` config only | Production per-frame roof/sky inference, overlay tokens, status strip. Runs in every build. |
| `ui/controllers/ml_prediction.py` | `DEV_MODE_AVAILABLE` | Second loader used by `dev_mode_utils.py` to write predictions into the calibration JSON export. |

`PFRSentinel.spec` bundles both `.onnx` files and lists `ml.roof_classifier` / `ml.sky_classifier` in `hiddenimports`
for the production path. Setting `DEV_MODE_AVAILABLE = False` does not turn ML off for users.

### File Locations

**Dev Mode Config:**
- `services\dev_mode_config.py` - Main flag file

**Dev Mode Features:**
- `ui\controllers\dev_mode_utils.py` - FITS/JSON saving
- `ui\controllers\ml_prediction.py` - ML predictions for the calibration JSON export
- `ui\panels\image_processing.py` - UI controls

**Always Available:**
- `services\ml_service.py` - per-frame ML inference (gated by `ml_models.enabled` only)
- `services\zwo_camera.py` - RAW16 mode (line 72: `self.use_raw16`)
- `ui\panels\capture_settings.py` - RAW16 toggle (line 618)
