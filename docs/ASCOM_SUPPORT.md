# ASCOM Support Implementation

This document outlines the camera abstraction layer and ASCOM support implementation in PFR Sentinel.

## Overview

PFR Sentinel v3.2.2+ includes a **camera abstraction layer** that allows the application to work with multiple camera backends through a unified interface:

| Backend | Status | Description |
|---------|--------|-------------|
| **ZWO ASI** | ✅ Fully Implemented | Native SDK support for ZWO astronomy cameras |
| **File Watch** | ✅ Fully Implemented | Directory monitoring mode (no hardware) |
| **ASCOM** | 🔄 Stub Implementation | ASCOM-compatible cameras (in development) |

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                      PFR Sentinel UI                            │
│  (CaptureController, OutputController, MonitoringPanel)         │
└─────────────────────────┬───────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────────┐
│                    CameraInterface (ABC)                        │
│  - initialize(), detect_cameras(), connect(), disconnect()      │
│  - configure(), capture_frame(), start_capture(), stop_capture()|
│  - CameraCapabilities, CameraInfo, CameraState, CaptureResult   │
└─────────────────────────┬───────────────────────────────────────┘
                          │
         ┌────────────────┼────────────────┐
         ▼                ▼                ▼
┌─────────────┐   ┌─────────────┐   ┌─────────────┐
│ ZWOAdapter  │   │ FileAdapter │   │ ASCOMAdapter│
│ (zwoasi SDK)│   │ (watchdog)  │   │ (alpyca)    │
└─────────────┘   └─────────────┘   └─────────────┘
```

## Usage

### Creating a Camera

```python
from services.camera import create_camera, create_camera_from_config

# Option 1: Explicit backend
camera = create_camera('zwo', config=app_config, logger=log_fn)

# Option 2: From application config (uses capture_mode)
camera = create_camera_from_config(app_config, logger=log_fn)

# Initialize and detect
camera.initialize()
cameras = camera.detect_cameras()

# Connect and capture
camera.connect(camera_index=0)
result = camera.capture_frame()
if result.success:
    image = result.image  # PIL Image
    metadata = result.metadata  # Dict with CAMERA, EXPOSURE, etc.
```

### Checking Available Backends

```python
from services.camera import get_available_backends, get_backend_info

# Simple list
backends = get_available_backends()  # ['ascom', 'file', 'zwo']

# Detailed info
info = get_backend_info()
# {
#     'zwo': {'name': 'ZWO ASI', 'available': True, ...},
#     'file': {'name': 'Directory Watch', 'available': True, ...},
#     'ascom': {'name': 'ASCOM', 'available': False, 'error': '...'},
# }
```

### Querying Capabilities

```python
caps = camera.capabilities

# Check before using features
if caps.supports_exposure_control:
    camera.configure({'exposure': 1000})  # 1 second

if caps.supports_cooling:
    camera.configure({'cooler_on': True, 'cooler_setpoint': -10})

# Get feature ranges
print(f"Exposure: {caps.min_exposure_ms} - {caps.max_exposure_ms} ms")
print(f"Gain: {caps.min_gain} - {caps.max_gain}")
```

## CameraInterface API

### Properties

| Property | Type | Description |
|----------|------|-------------|
| `state` | `CameraState` | Current state (DISCONNECTED, CONNECTING, CONNECTED, etc.) |
| `capabilities` | `CameraCapabilities` | Backend capabilities and feature flags |
| `camera_info` | `CameraInfo` | Connected camera details (name, device_id, etc.) |
| `is_connected` | `bool` | Whether camera is connected |
| `is_capturing` | `bool` | Whether continuous capture is running |

### Core Methods

| Method | Returns | Description |
|--------|---------|-------------|
| `initialize()` | `bool` | Initialize backend (load SDK, etc.) |
| `detect_cameras()` | `List[CameraInfo]` | Find available cameras |
| `connect(index, settings)` | `bool` | Connect to camera |
| `disconnect()` | `None` | Disconnect from camera |
| `reconnect()` | `bool` | Attempt reconnection |
| `configure(settings)` | `None` | Apply camera settings |
| `get_current_settings()` | `Dict` | Get current settings |
| `capture_frame()` | `CaptureResult` | Capture single image |
| `start_capture(callback)` | `bool` | Start continuous capture |
| `stop_capture()` | `None` | Stop continuous capture |

### CameraCapabilities Fields

```python
@dataclass
class CameraCapabilities:
    # Backend identification
    backend_name: str      # 'ZWO ASI', 'ASCOM', 'File'
    backend_version: str   # SDK/adapter version
    
    # Connection features
    supports_hot_plug: bool
    supports_multiple_cameras: bool
    requires_sdk_path: bool
    
    # Exposure control
    supports_exposure_control: bool
    min_exposure_ms: float
    max_exposure_ms: float
    supports_auto_exposure: bool
    
    # Gain control
    supports_gain_control: bool
    min_gain: int
    max_gain: int
    supports_auto_gain: bool
    
    # White balance
    supports_white_balance: bool
    supports_auto_white_balance: bool
    
    # Image settings
    supports_binning: bool
    max_binning: int
    supports_roi: bool
    supports_flip: bool
    supports_offset: bool
    
    # Bit depth and color
    supports_raw8: bool
    supports_raw16: bool
    native_bit_depth: int
    is_color_camera: bool
    bayer_pattern: Optional[str]
    supports_debayering: bool
    
    # Temperature/cooling
    supports_temperature_reading: bool
    supports_cooling: bool
    supports_cooler_control: bool
    
    # Metadata
    provides_metadata: bool
    metadata_fields: List[str]
    
    # Performance
    supports_streaming: bool
    max_fps: float
```

## ASCOM Implementation Status

### Requirements

- **Windows OS** (ASCOM is Windows-only)
- **ASCOM Platform 6.6+** from https://ascom-standards.org
- **Python package**: `pip install alpyca` (for Alpaca) or `pip install comtypes` (for COM)

### Current Status (v3.2.2)

The ASCOM adapter is a **stub implementation** that defines the interface but does not yet connect to real devices. The following work remains:

#### Phase 1: Basic Connection (TODO)
- [ ] Implement ASCOM device enumeration
- [ ] Connect via Alpaca REST API
- [ ] Basic exposure capture

#### Phase 2: Full Feature Support (TODO)
- [ ] Read camera capabilities from ASCOM properties
- [ ] Implement cooling control
- [ ] ROI/subframe support
- [ ] Binning support

#### Phase 3: Testing & Polish (TODO)
- [ ] Test with popular ASCOM drivers (QHY, Atik, etc.)
- [ ] Handle driver quirks and error cases
- [ ] Performance optimization

### ASCOM vs Alpaca

| Feature | ASCOM COM | ASCOM Alpaca |
|---------|-----------|--------------|
| Protocol | Windows COM | REST/HTTP |
| OS | Windows only | Cross-platform |
| Library | comtypes | alpyca |
| Network | Local only | Local or remote |
| Preferred | Legacy | Modern ✓ |

PFR Sentinel prefers **Alpaca** but falls back to **COM** if alpyca is not available.

## Adding a New Backend

To add support for a new camera type:

1. **Create adapter file**: `services/camera/mybackend_adapter.py`

2. **Implement CameraInterface**:
   ```python
   from .interface import CameraInterface, CameraCapabilities, ...
   
   class MyBackendAdapter(CameraInterface):
       _CAPABILITIES = CameraCapabilities(
           backend_name="MyBackend",
           # ... fill in capabilities
       )
       
       def initialize(self) -> bool:
           # Load SDK, check dependencies
           pass
       
       def detect_cameras(self) -> List[CameraInfo]:
           # Enumerate available cameras
           pass
       
       # ... implement all abstract methods
   ```

3. **Register in factory**: Edit `services/camera/factory.py`:
   ```python
   def _register_backends():
       # ... existing code ...
       
       try:
           from .mybackend_adapter import MyBackendAdapter
           _BACKENDS['mybackend'] = MyBackendAdapter
       except ImportError:
           pass
   ```

4. **Export in __init__.py**: Add to exports if needed

5. **Document**: Add to this file and update capabilities table

## Configuration

Camera settings in `config.json`:

```json
{
    "capture_mode": "camera",      // 'watch', 'camera', or 'ascom'
    "camera_backend": "zwo",       // Override: 'zwo' or 'ascom'
    
    // ZWO-specific
    "sdk_path": "ASICamera2.dll",
    "camera_index": 0,
    "exposure": 50,               // milliseconds
    "gain": 200,
    "white_balance_r": 50,
    "white_balance_b": 50,
    
    // ASCOM-specific (when implemented)
    "ascom_device_id": "",        // ASCOM ProgID or Alpaca device
    "alpaca_host": "127.0.0.1",
    "alpaca_port": 11111,
    
    // File watch mode
    "watch_directory": "C:\\Captures",
    "watch_recursive": true
}
```

## Testing

Run camera abstraction tests:

```powershell
# From project root
python -m pytest tests/test_camera_interface.py -v
```

Manual testing checklist:
- [ ] ZWO camera connects and captures
- [ ] File watch mode detects new images
- [ ] ASCOM enumeration shows installed drivers
- [ ] Settings persist correctly
- [ ] State callbacks fire appropriately
- [ ] Error handling works for disconnects

## Changelog

### v3.2.2 (Current)
- Added camera abstraction layer (`services/camera/`)
- Implemented `CameraInterface` abstract base class
- Implemented `ZWOCameraAdapter` wrapping existing ZWOCamera
- Implemented `FileWatchAdapter` for directory monitoring
- Added `ASCOMCameraAdapter` stub for future ASCOM support
- Added camera factory with backend registration

### Future
- Full ASCOM implementation
- Additional backends (INDI, vendor SDKs)
- Enhanced capability querying
