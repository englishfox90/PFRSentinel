"""
Tests for camera abstraction layer.

These tests verify:
1. Interface contracts are satisfied by all adapters
2. Factory functions work correctly
3. ZWO adapter maintains feature parity with legacy ZWOCamera
4. File adapter properly wraps FileWatcher
"""
import pytest
from unittest.mock import Mock, patch, MagicMock
from dataclasses import fields
import os
import sys

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.camera import (
    CameraInterface,
    CameraCapabilities,
    CameraInfo,
    CameraState,
    CaptureResult,
    create_camera,
    create_camera_from_config,
    get_available_backends,
    get_backend_info,
)
from services.camera.zwo_adapter import ZWOCameraAdapter
from services.camera.file_adapter import FileWatchAdapter
from services.camera.ascom_adapter import ASCOMCameraAdapter


# =============================================================================
# CameraCapabilities Tests
# =============================================================================

class TestCameraCapabilities:
    """Test CameraCapabilities dataclass"""
    
    def test_capabilities_has_required_fields(self):
        """Verify all expected fields exist in CameraCapabilities"""
        field_names = [f.name for f in fields(CameraCapabilities)]
        
        required = [
            'backend_name', 'backend_version',
            'supports_exposure_control', 'min_exposure_ms', 'max_exposure_ms',
            'supports_gain_control', 'min_gain', 'max_gain',
            'supports_temperature_reading', 'supports_cooling',
            'is_color_camera', 'native_bit_depth',
            'provides_metadata', 'metadata_fields',
        ]
        
        for field in required:
            assert field in field_names, f"Missing required field: {field}"
    
    def test_capabilities_immutable(self):
        """CameraCapabilities should be frozen dataclass"""
        caps = CameraCapabilities(
            backend_name="Test",
            backend_version="1.0",
            supports_hot_plug=False,
            supports_multiple_cameras=False,
            requires_sdk_path=False,
            supports_exposure_control=True,
            min_exposure_ms=1.0,
            max_exposure_ms=1000.0,
            supports_auto_exposure=False,
            supports_gain_control=True,
            min_gain=0,
            max_gain=100,
            supports_auto_gain=False,
            supports_white_balance=False,
            supports_auto_white_balance=False,
            min_wb_value=0,
            max_wb_value=0,
            supports_binning=False,
            max_binning=1,
            supports_roi=False,
            supports_flip=False,
            supports_offset=False,
            supports_raw8=True,
            supports_raw16=False,
            native_bit_depth=8,
            is_color_camera=True,
            bayer_pattern=None,
            supports_debayering=False,
            supports_temperature_reading=False,
            supports_cooling=False,
            supports_cooler_control=False,
            supports_scheduled_capture=False,
            provides_metadata=True,
            metadata_fields=['CAMERA', 'EXPOSURE'],
            supports_streaming=False,
            max_fps=0,
        )
        
        # Should raise FrozenInstanceError on modification attempt
        with pytest.raises(Exception):  # dataclass frozen=True raises FrozenInstanceError
            caps.backend_name = "Modified"


# =============================================================================
# CameraState Tests
# =============================================================================

class TestCameraState:
    """Test CameraState enum"""
    
    def test_all_states_defined(self):
        """Verify all expected states exist"""
        expected = [
            'DISCONNECTED', 'CONNECTING', 'CONNECTED',
            'CAPTURING', 'CALIBRATING', 'ERROR'
        ]
        
        for state_name in expected:
            assert hasattr(CameraState, state_name)


# =============================================================================
# CaptureResult Tests  
# =============================================================================

class TestCaptureResult:
    """Test CaptureResult dataclass"""
    
    def test_success_result(self):
        """Test successful capture result"""
        result = CaptureResult(
            success=True,
            image=Mock(),
            metadata={'EXPOSURE': 1000},
        )
        
        assert result.success is True
        assert result.error is None
        assert result.metadata['EXPOSURE'] == 1000
    
    def test_failure_result(self):
        """Test failed capture result"""
        result = CaptureResult(
            success=False,
            error="Camera disconnected"
        )
        
        assert result.success is False
        assert result.image is None
        assert "disconnected" in result.error.lower()


# =============================================================================
# Factory Function Tests
# =============================================================================

class TestCameraFactory:
    """Test camera factory functions"""
    
    def test_get_available_backends(self):
        """get_available_backends returns list of strings"""
        backends = get_available_backends()
        
        assert isinstance(backends, list)
        assert all(isinstance(b, str) for b in backends)
        # Should have at least file and zwo
        assert 'file' in backends or 'zwo' in backends
    
    def test_get_backend_info(self):
        """get_backend_info returns detailed info dict"""
        info = get_backend_info()
        
        assert isinstance(info, dict)
        
        for name, details in info.items():
            assert 'name' in details
            assert 'description' in details
            assert 'available' in details
    
    def test_create_camera_zwo(self):
        """create_camera('zwo') returns ZWOCameraAdapter"""
        try:
            camera = create_camera('zwo')
            assert isinstance(camera, ZWOCameraAdapter)
        except ValueError:
            pytest.skip("ZWO backend not available")
    
    def test_create_camera_file(self):
        """create_camera('file') returns FileWatchAdapter"""
        camera = create_camera('file')
        assert isinstance(camera, FileWatchAdapter)
    
    def test_create_camera_invalid_backend(self):
        """create_camera with invalid backend raises ValueError"""
        with pytest.raises(ValueError) as exc_info:
            create_camera('invalid_backend_xyz')
        
        assert 'Unknown camera backend' in str(exc_info.value)
    
    def test_create_camera_from_config_watch_mode(self):
        """create_camera_from_config with watch mode returns FileWatchAdapter"""
        config = {'capture_mode': 'watch'}
        camera = create_camera_from_config(config)
        assert isinstance(camera, FileWatchAdapter)
    
    def test_create_camera_from_config_camera_mode(self):
        """create_camera_from_config with camera mode returns ZWOCameraAdapter"""
        config = {'capture_mode': 'camera'}
        try:
            camera = create_camera_from_config(config)
            assert isinstance(camera, ZWOCameraAdapter)
        except ValueError:
            pytest.skip("ZWO backend not available")


# =============================================================================
# ZWOCameraAdapter Tests
# =============================================================================

class TestZWOCameraAdapter:
    """Test ZWO camera adapter"""
    
    @pytest.fixture
    def adapter(self):
        """Create adapter with mock logger"""
        return ZWOCameraAdapter(config={}, logger=Mock())
    
    def test_implements_interface(self, adapter):
        """ZWOCameraAdapter implements all CameraInterface methods"""
        # Check all abstract methods exist
        assert hasattr(adapter, 'initialize')
        assert hasattr(adapter, 'detect_cameras')
        assert hasattr(adapter, 'connect')
        assert hasattr(adapter, 'disconnect')
        assert hasattr(adapter, 'configure')
        assert hasattr(adapter, 'capture_frame')
        assert hasattr(adapter, 'start_capture')
        assert hasattr(adapter, 'stop_capture')
    
    def test_initial_state(self, adapter):
        """Adapter starts in DISCONNECTED state"""
        assert adapter.state == CameraState.DISCONNECTED
        assert adapter.is_connected is False
        assert adapter.is_capturing is False
    
    def test_capabilities_defined(self, adapter):
        """Adapter provides capabilities"""
        caps = adapter.capabilities
        
        assert isinstance(caps, CameraCapabilities)
        assert caps.backend_name == "ZWO ASI"
        assert caps.supports_exposure_control is True
        assert caps.supports_gain_control is True
        assert caps.is_color_camera is True
    
    def test_capabilities_exposure_range(self, adapter):
        """ZWO exposure range is reasonable"""
        caps = adapter.capabilities
        
        # ZWO cameras typically support 32μs to 2000s
        assert caps.min_exposure_ms < 1  # Less than 1ms min
        assert caps.max_exposure_ms >= 60000  # At least 60 seconds max
    
    def test_state_callback_fires(self, adapter):
        """State callback is invoked on state changes"""
        callback = Mock()
        adapter.set_state_callback(callback)
        
        # Trigger a state change (internally)
        adapter._set_state(CameraState.ERROR)
        
        callback.assert_called_once_with(CameraState.ERROR)
    
    def test_log_callback(self, adapter):
        """Log callback receives messages"""
        log_callback = Mock()
        adapter.set_log_callback(log_callback)
        
        adapter._log("Test message")
        
        log_callback.assert_called_once_with("Test message")


# =============================================================================
# FileWatchAdapter Tests
# =============================================================================

class TestFileWatchAdapter:
    """Test File watch adapter"""
    
    @pytest.fixture
    def adapter(self):
        """Create adapter with test config"""
        config = {
            'watch_directory': os.path.dirname(__file__),  # Use test dir
            'watch_recursive': True,
        }
        return FileWatchAdapter(config=config, logger=Mock())
    
    def test_implements_interface(self, adapter):
        """FileWatchAdapter implements all CameraInterface methods"""
        assert hasattr(adapter, 'initialize')
        assert hasattr(adapter, 'detect_cameras')
        assert hasattr(adapter, 'connect')
        assert hasattr(adapter, 'disconnect')
        assert hasattr(adapter, 'configure')
        assert hasattr(adapter, 'capture_frame')
        assert hasattr(adapter, 'start_capture')
        assert hasattr(adapter, 'stop_capture')
    
    def test_initial_state(self, adapter):
        """Adapter starts in DISCONNECTED state"""
        assert adapter.state == CameraState.DISCONNECTED
        assert adapter.is_connected is False
    
    def test_capabilities_no_hardware(self, adapter):
        """File adapter correctly reports no hardware control"""
        caps = adapter.capabilities
        
        assert caps.backend_name == "File"
        assert caps.supports_exposure_control is False
        assert caps.supports_gain_control is False
        assert caps.supports_cooling is False
        assert caps.provides_metadata is True
    
    def test_initialize_success(self, adapter):
        """Initialize succeeds even without directory"""
        result = adapter.initialize()
        assert result is True
    
    def test_detect_with_valid_directory(self, adapter):
        """detect_cameras returns CameraInfo for valid directory"""
        cameras = adapter.detect_cameras()
        
        assert len(cameras) == 1
        assert cameras[0].backend == "File"
        assert "Directory" in cameras[0].name
    
    def test_connect_success(self, adapter):
        """Connect succeeds with valid directory"""
        result = adapter.connect(0)
        
        assert result is True
        assert adapter.state == CameraState.CONNECTED
        assert adapter.is_connected is True
    
    def test_connect_fails_without_directory(self):
        """Connect fails when no directory configured"""
        adapter = FileWatchAdapter(config={}, logger=Mock())
        
        result = adapter.connect(0)
        
        assert result is False
    
    def test_unsupported_operations_return_false(self, adapter):
        """Unsupported operations return False gracefully"""
        assert adapter.set_auto_exposure(True) is False
        assert adapter.run_calibration() is False
        assert adapter.get_temperature() is None


# =============================================================================
# ASCOMCameraAdapter Tests
# =============================================================================

class TestASCOMCameraAdapter:
    """Test ASCOM camera adapter (stub)"""
    
    @pytest.fixture
    def adapter(self):
        """Create adapter"""
        return ASCOMCameraAdapter(config={}, logger=Mock())
    
    def test_implements_interface(self, adapter):
        """ASCOMCameraAdapter implements all CameraInterface methods"""
        assert hasattr(adapter, 'initialize')
        assert hasattr(adapter, 'detect_cameras')
        assert hasattr(adapter, 'connect')
        assert hasattr(adapter, 'disconnect')
        assert hasattr(adapter, 'configure')
        assert hasattr(adapter, 'capture_frame')
        assert hasattr(adapter, 'start_capture')
        assert hasattr(adapter, 'stop_capture')
    
    def test_capabilities_typical_astro_camera(self, adapter):
        """ASCOM adapter reports typical astro camera capabilities"""
        caps = adapter.capabilities
        
        assert caps.backend_name == "ASCOM"
        assert caps.supports_exposure_control is True
        assert caps.supports_cooling is True
        assert caps.native_bit_depth == 16  # Typical for astro


# =============================================================================
# Integration Tests
# =============================================================================

class TestCameraIntegration:
    """Integration tests for camera workflow"""
    
    def test_full_lifecycle_file_adapter(self, tmp_path):
        """Test complete lifecycle: create -> init -> detect -> connect -> disconnect"""
        # Create temporary watch directory
        watch_dir = str(tmp_path)
        config = {'watch_directory': watch_dir}
        
        # Create camera
        camera = create_camera('file', config=config)
        assert camera.state == CameraState.DISCONNECTED
        
        # Initialize
        assert camera.initialize() is True
        
        # Detect
        cameras = camera.detect_cameras()
        assert len(cameras) >= 1
        
        # Connect
        assert camera.connect(0) is True
        assert camera.state == CameraState.CONNECTED
        
        # Get settings
        settings = camera.get_current_settings()
        assert 'watch_directory' in settings
        
        # Disconnect
        camera.disconnect()
        assert camera.state == CameraState.DISCONNECTED
    
    def test_configure_preserves_settings(self, tmp_path):
        """Test that configure() updates settings correctly"""
        config = {'watch_directory': str(tmp_path)}
        camera = create_camera('file', config=config)
        
        new_dir = str(tmp_path / "subdir")
        os.makedirs(new_dir, exist_ok=True)
        
        camera.configure({'watch_directory': new_dir})
        
        settings = camera.get_current_settings()
        assert settings['watch_directory'] == new_dir


# =============================================================================
# ZWO Feature Parity Tests
# =============================================================================

class TestZWOFeatureParity:
    """
    Tests to ensure ZWOCameraAdapter maintains parity with legacy ZWOCamera.
    
    These tests verify the adapter doesn't lose functionality during abstraction.
    """
    
    def test_exposure_conversion_ms_to_us(self):
        """Verify exposure conversion: config uses ms, SDK uses μs"""
        adapter = ZWOCameraAdapter(config={'exposure': 1000}, logger=Mock())
        
        # 1000ms should become 1,000,000μs for SDK
        # This is handled in _convert_settings_to_zwo()
        settings = {'exposure': 1000}
        zwo_settings = adapter._convert_settings_to_zwo(settings)
        
        assert zwo_settings.get('exposure', 0) == 1000000  # μs
    
    def test_settings_round_trip(self):
        """Settings should survive conversion to ZWO format and back"""
        adapter = ZWOCameraAdapter(config={}, logger=Mock())
        
        original = {
            'exposure': 500,  # ms
            'gain': 200,
            'white_balance_r': 60,
            'white_balance_b': 40,
        }
        
        zwo_format = adapter._convert_settings_to_zwo(original)
        restored = adapter._convert_settings_from_zwo(zwo_format)
        
        # Exposure should round-trip (allowing for ms->μs->ms conversion)
        assert abs(restored['exposure'] - original['exposure']) < 1
        assert restored['gain'] == original['gain']
    
    def test_metadata_fields_present(self):
        """ZWO adapter should provide all expected metadata fields"""
        adapter = ZWOCameraAdapter(config={}, logger=Mock())
        caps = adapter.capabilities
        
        expected_fields = ['CAMERA', 'EXPOSURE', 'GAIN', 'TEMP', 'RES', 'DATETIME']
        
        for field in expected_fields:
            assert field in caps.metadata_fields


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
