# Camera Disconnect Recovery Improvements

> Windows only. There is no USB reset on macOS or Linux, by decision — see
> [ZWO_SDK_PLATFORMS.md](ZWO_SDK_PLATFORMS.md#usb-recovery-off-windows).

## Problem Statement

After 12-18 hours of continuous operation, ZWO ASI676MC cameras can fail with `ASI_EXP_FAILED` error. After this failure:

1. SDK's `close()` method completes successfully
2. Camera becomes invisible to SDK detection (`get_num_cameras()` doesn't find it)
3. Only secondary cameras (like ASI462MM) remain visible
4. Physical USB disconnect/reconnect was required to recover

**Root Cause**: When the camera fails during a long exposure, the USB device enters a bad state. The ZWO SDK's `close()` method releases the software handle but doesn't reset the USB device at the hardware level. Windows driver remains confused about device state.

## Solution

### Enhanced SDK Reset + Optional USB Reset

**Primary Fix**: Improved disconnect/reconnect sequence with proper delays and factory reset

**Experimental Feature**: Windows USB device reset capability (may require specific privileges)

#### New File: `services/usb_reset_win.py`

Implements USB device enumeration and reset via Windows Device Management:

- **`reset_zwo_camera_usb(camera_name, logger)`**: Main entry point
  - Finds ZWO USB devices by Vendor ID (VID_03C3)
  - Matches camera by name (e.g., "ZWO ASI676MC")
  - Calls `CM_Reenumerate_DevNode()` to reset USB device
  - Falls back to `CM_Setup_DevNode()` if re-enumeration fails
  
- **`_find_zwo_usb_devices(logger)`**: Enumerates USB devices
  - Uses `SetupDiGetClassDevsW()` to get device information set
  - Filters by ZWO Vendor ID (VID_03C3)
  - Returns device instance handles and descriptions
  
- **`_reset_device(devinst, logger)`**: Low-level reset
  - Requests device re-enumeration (soft reset)
  - Falls back to device restart (hard reset) if needed
  
**No additional dependencies required** - uses built-in `ctypes` to call Windows API.

### Integration with Camera Connection

#### Modified: `services/camera_connection.py`

**Added USB reset initialization** (`_init_usb_reset()`):
```python
# In __init__()
self._usb_reset_available = False
self._usb_reset_func = None
self._init_usb_reset()  # Try to load USB reset capability
```

**Enhanced `reconnect_safe()` method**:

Recovery sequence when camera not detected:

1. **USB Device Reset** (NEW): If Windows and camera name known
   - Calls `reset_zwo_camera_usb()` to reset device at hardware level
   - Waits 2 seconds for Windows to re-enumerate device
   - Re-detects cameras to see if device reappeared
   
2. **SDK Reset** (EXISTING): If USB reset unavailable or failed
   - Reinitializes ZWO SDK (`asi.init()`)
   - Re-detects cameras
   
3. **Connect**: Attempts connection with restored settings

**Enhanced `disconnect()` method**:

- Added **0.5 second delay** after `camera.close()` to ensure SDK fully releases USB device
- If close fails, attempts USB reset as recovery option
- Logs USB reset attempts for troubleshooting

### Enhanced Error Reporting

#### Modified: `services/zwo_camera.py`

Improved troubleshooting message when max reconnection attempts reached:

```
Troubleshooting steps:
  1. Check USB cable connection
  2. Check camera power supply
  3. Try: Physically disconnect USB, wait 5 seconds, reconnect
  4. Check Windows Device Manager for USB errors
  5. Restart application (automatic USB reset will be attempted)
  6. If persistent: Update ZWO drivers from astronomy-imaging-camera.com

Note: Camera may be stuck in bad USB state requiring physical disconnect.
```

## Testing

### Test Script: `scripts/dev/test_usb_reset.py`

Quick verification that USB reset works:

```powershell
python scripts\dev\test_usb_reset.py
```

Output shows:
- Whether USB reset capability is available
- List of detected ZWO USB devices with hardware IDs
- Option to test reset (requires confirmation)

### Manual Testing

1. **Run app normally**: Start capture, let run for several hours
2. **Simulate failure**: When `ASI_EXP_FAILED` occurs naturally
3. **Observe recovery**: Check logs for USB reset attempt:
   ```
   ✗ No cameras detected, attempting recovery...
   Attempting USB device reset...
   === Attempting USB Device Reset ===
   Looking for camera: ASI676MC
   ✓ Found matching device: ZWO ASI676MC
   Resetting: ZWO ASI676MC (DevInst: 0x00001234)
     ✓ Reset requested successfully
   ✓ USB reset completed for 1/1 device(s)
   ✓ Cameras detected after reset: 1 found
   ```

4. **Expected outcome**: Camera should reconnect without physical USB disconnect

## Fallback Behavior

If USB reset is not available (non-Windows, API load failure):

1. Logs: `⚠ USB reset not available (Windows API load failed)`
2. Falls back to SDK reset only
3. If that fails, provides instructions for manual physical disconnect

## Administrator Privileges

USB device reset may require Administrator privileges depending on Windows security settings.

**If reset fails**:
- Try running application as Administrator
- Check Windows Event Viewer for device management errors
- Verify ZWO driver is properly installed

## Benefits

1. **Automatic recovery**: No user intervention for USB-level camera hangs
2. **Faster recovery**: 2-3 seconds vs manual disconnect/reconnect
3. **24/7 operation**: Reduces need for manual monitoring
4. **No dependencies**: Uses Windows native APIs via ctypes

## Compatibility

- **OS**: Windows only (Linux/macOS would need platform-specific USB reset)
- **Python**: 3.6+ (uses ctypes, standard library)
- **Cameras**: All ZWO ASI cameras (Vendor ID 0x03C3)
- **Privileges**: May require Administrator for device reset API calls

## Future Improvements

1. **Linux support**: Use `libusb` or `/sys/bus/usb/devices/.../authorized` for USB reset
2. **Metrics**: Track USB reset success rate, time to recovery
3. **Preemptive reset**: Detect early signs of USB issues and reset before failure
4. **Smart retry**: Increase USB reset attempts for known-good hardware setups

## Related Files

- `services/usb_reset_win.py` - USB reset implementation (NEW)
- `services/camera_connection.py` - Integration with reconnect logic (MODIFIED)
- `services/zwo_camera.py` - Enhanced error messages (MODIFIED)
- `scripts/dev/test_usb_reset.py` - Test utility (NEW)
- `docs/dev/CAMERA_USB_RESET.md` - This documentation (NEW)
