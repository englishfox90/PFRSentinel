"""
Test Discord webhook integration
"""
import pytest
import os
import sys
import json
from unittest.mock import Mock, patch, MagicMock
from datetime import datetime

project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)


class TestDiscordConfiguration:
    """Test Discord configuration handling"""
    
    def test_default_config_structure(self):
        """Test default Discord config has expected keys"""
        from services.config import DEFAULT_CONFIG
        
        discord_config = DEFAULT_CONFIG.get('discord', {})
        
        assert 'enabled' in discord_config
        assert 'webhook_url' in discord_config
        assert 'username_override' in discord_config
        assert 'embed_color_hex' in discord_config
        assert 'include_latest_image' in discord_config
    
    def test_discord_disabled_by_default(self):
        """Test Discord is disabled by default for safety"""
        from services.config import DEFAULT_CONFIG
        
        discord_config = DEFAULT_CONFIG.get('discord', {})
        
        # Should be disabled by default to prevent accidental webhook spam
        assert discord_config.get('enabled', True) is False


class TestDiscordAlerts:
    """Test DiscordAlerts class"""

    @pytest.fixture(autouse=True)
    def _no_backoff_sleep(self):
        with patch('services.discord_alerts.time.sleep'):  # exhausting BACKOFF_DELAYS is ~5 s per test
            yield

    @pytest.fixture
    def discord_config(self):
        """Sample Discord config"""
        return {
            'discord': {
                'enabled': True,
                'webhook_url': 'https://discord.com/api/webhooks/test/test',
                'username_override': 'TestBot',
                'avatar_url': '',
                'embed_color_hex': '#0EA5E9',
                'include_latest_image': True,
                'post_startup_shutdown': True,
                'post_errors': True,
                'periodic_enabled': True,
                'periodic_interval_minutes': 15
            },
            'capture_mode': 'camera',
            'output_directory': '/test/output'
        }
    
    @pytest.fixture
    def discord_alerts(self, discord_config):
        """Create DiscordAlerts instance"""
        from services.discord_alerts import DiscordAlerts
        
        config = Mock()
        config.get = lambda key, default=None: discord_config.get(key, default)
        
        return DiscordAlerts(config)
    
    def test_is_enabled_true(self, discord_alerts):
        """Test is_enabled returns True when properly configured"""
        assert discord_alerts.is_enabled()  # truthy check
    
    def test_is_enabled_false_when_disabled(self):
        """Test is_enabled returns False when disabled"""
        from services.discord_alerts import DiscordAlerts
        
        config = Mock()
        config.get = lambda key, default=None: {
            'discord': {'enabled': False, 'webhook_url': 'https://test'}
        }.get(key, default)
        
        alerts = DiscordAlerts(config)
        assert not alerts.is_enabled()
    
    def test_is_enabled_false_no_webhook(self):
        """Test is_enabled returns False when no webhook URL"""
        from services.discord_alerts import DiscordAlerts
        
        config = Mock()
        config.get = lambda key, default=None: {
            'discord': {'enabled': True, 'webhook_url': ''}
        }.get(key, default)
        
        alerts = DiscordAlerts(config)
        assert not alerts.is_enabled()
    
    def test_get_color_int_valid(self, discord_alerts):
        """Test hex color conversion"""
        color = discord_alerts.get_color_int()
        
        # 0EA5E9 in decimal
        expected = int('0EA5E9', 16)
        assert color == expected
    
    def test_get_color_int_with_hash(self):
        """Test hex color with # prefix"""
        from services.discord_alerts import DiscordAlerts
        
        config = Mock()
        config.get = lambda key, default=None: {
            'discord': {'embed_color_hex': '#FF5500'}
        }.get(key, default)
        
        alerts = DiscordAlerts(config)
        color = alerts.get_color_int()
        
        expected = int('FF5500', 16)
        assert color == expected
    
    def test_get_color_int_without_hash(self):
        """Test hex color without # prefix"""
        from services.discord_alerts import DiscordAlerts
        
        config = Mock()
        config.get = lambda key, default=None: {
            'discord': {'embed_color_hex': 'FF5500'}
        }.get(key, default)
        
        alerts = DiscordAlerts(config)
        color = alerts.get_color_int()
        
        expected = int('FF5500', 16)
        assert color == expected
    
    def test_get_color_int_invalid_returns_default(self):
        """Test invalid color returns default"""
        from services.discord_alerts import DiscordAlerts
        
        config = Mock()
        config.get = lambda key, default=None: {
            'discord': {'embed_color_hex': 'invalid'}
        }.get(key, default)
        
        alerts = DiscordAlerts(config)
        color = alerts.get_color_int()
        
        default_color = int('0EA5E9', 16)
        assert color == default_color
    
    @patch('services.discord_alerts.requests.post')
    def test_send_message_success(self, mock_post, discord_alerts):
        """Test successful message send"""
        mock_response = Mock()
        mock_response.status_code = 204
        mock_post.return_value = mock_response
        
        result = discord_alerts.send_discord_message(
            "Test Title",
            "Test Description",
            level="info"
        )
        
        assert result is True
        assert discord_alerts.last_send_status == "Success (HTTP 204)"
        assert mock_post.called
    
    @patch('services.discord_alerts.requests.post')
    def test_send_message_failure(self, mock_post, discord_alerts):
        """Test failed message send"""
        mock_response = Mock()
        mock_response.status_code = 400
        mock_response.text = "Bad Request"
        mock_response.json.side_effect = ValueError()
        mock_post.return_value = mock_response
        
        result = discord_alerts.send_discord_message(
            "Test Title",
            "Test Description",
            level="error"
        )
        
        assert result is False
        assert "Failed" in discord_alerts.last_send_status
    
    @patch('services.discord_alerts.requests.post')
    def test_send_message_timeout(self, mock_post, discord_alerts):
        """Test message send timeout"""
        import requests
        mock_post.side_effect = requests.exceptions.Timeout()
        
        result = discord_alerts.send_discord_message(
            "Test Title",
            "Test Description"
        )
        
        assert result is False
        assert "timeout" in discord_alerts.last_send_status.lower()
    
    @patch('services.discord_alerts.requests.post')
    def test_send_message_connection_error(self, mock_post, discord_alerts):
        """Test message send connection error"""
        import requests
        mock_post.side_effect = requests.exceptions.ConnectionError()
        
        result = discord_alerts.send_discord_message(
            "Test Title",
            "Test Description"
        )
        
        assert result is False
        assert "connection" in discord_alerts.last_send_status.lower()
    
    def test_send_message_disabled(self):
        """Test send returns False when disabled"""
        from services.discord_alerts import DiscordAlerts
        
        config = Mock()
        config.get = lambda key, default=None: {
            'discord': {'enabled': False, 'webhook_url': ''}
        }.get(key, default)
        
        alerts = DiscordAlerts(config)
        result = alerts.send_discord_message("Test", "Test")
        
        assert result is False
    
    @patch('services.discord_alerts.requests.post')
    def test_send_startup_message(self, mock_post, discord_alerts):
        """Test startup notification"""
        mock_response = Mock()
        mock_response.status_code = 204
        mock_post.return_value = mock_response
        
        result = discord_alerts.send_startup_message()
        
        assert result is True
        
        # Verify embed content includes mode info
        call_args = mock_post.call_args
        payload = call_args[1]['json']
        
        assert payload['username'] == 'TestBot'
        embed = payload['embeds'][0]
        assert 'Started' in embed['title']
    
    @patch('services.discord_alerts.requests.post')
    def test_send_shutdown_message(self, mock_post, discord_alerts):
        """Test shutdown notification"""
        mock_response = Mock()
        mock_response.status_code = 204
        mock_post.return_value = mock_response
        
        result = discord_alerts.send_shutdown_message()
        
        assert result is True
        
        call_args = mock_post.call_args
        payload = call_args[1]['json']
        embed = payload['embeds'][0]
        assert 'Stopped' in embed['title']
    
    @patch('services.discord_alerts.requests.post')
    def test_send_error_message(self, mock_post, discord_alerts):
        """Test error notification"""
        mock_response = Mock()
        mock_response.status_code = 204
        mock_post.return_value = mock_response
        
        result = discord_alerts.send_error_message("Test error occurred")
        
        assert result is True
        
        call_args = mock_post.call_args
        payload = call_args[1]['json']
        embed = payload['embeds'][0]
        assert 'Error' in embed['title']
        assert 'Test error occurred' in embed['description']
    
    def test_get_last_status_no_sends(self, discord_alerts):
        """Test status when no messages sent"""
        status = discord_alerts.get_last_status()
        assert "No messages sent" in status
    
    @patch('services.discord_alerts.requests.post')
    def test_get_last_status_after_send(self, mock_post, discord_alerts):
        """Test status after successful send"""
        mock_response = Mock()
        mock_response.status_code = 204
        mock_post.return_value = mock_response
        
        discord_alerts.send_discord_message("Test", "Test")
        
        status = discord_alerts.get_last_status()
        assert "Last message" in status
        assert "Success" in status


class TestDiscordRetry:
    """Test Discord retry with exponential backoff"""

    @pytest.fixture
    def discord_config(self):
        return {
            'discord': {
                'enabled': True,
                'webhook_url': 'https://discord.com/api/webhooks/test/test',
                'username_override': 'TestBot',
                'avatar_url': '',
                'embed_color_hex': '#0EA5E9',
                'include_latest_image': True,
                'post_startup_shutdown': True,
                'post_errors': True,
            },
            'capture_mode': 'camera',
            'output_directory': '/test/output'
        }

    @pytest.fixture
    def discord_alerts(self, discord_config):
        from services.discord_alerts import DiscordAlerts
        config = Mock()
        config.get = lambda key, default=None: discord_config.get(key, default)
        return DiscordAlerts(config)

    @patch('services.discord_alerts.time.sleep')
    @patch('services.discord_alerts.requests.post')
    def test_retry_on_transient_failure(self, mock_post, mock_sleep, discord_alerts):
        """Test retry succeeds on second attempt after transient failure"""
        import requests as req
        mock_post.side_effect = [
            req.exceptions.ConnectionError("transient"),
            Mock(status_code=204)
        ]

        result = discord_alerts.send_discord_message("Test", "Test")

        assert result is True
        assert mock_post.call_count == 2
        mock_sleep.assert_called_once_with(1)  # First backoff delay

    @patch('services.discord_alerts.time.sleep')
    @patch('services.discord_alerts.requests.post')
    def test_all_retries_exhausted(self, mock_post, mock_sleep, discord_alerts):
        """Test all 3 attempts fail returns False"""
        import requests as req
        mock_post.side_effect = req.exceptions.Timeout()

        result = discord_alerts.send_discord_message("Test", "Test")

        assert result is False
        assert mock_post.call_count == 3
        assert mock_sleep.call_count == 2  # Sleep between retries only

    @patch('services.discord_alerts.time.sleep')
    @patch('services.discord_alerts.requests.post')
    def test_rate_limit_honors_retry_after(self, mock_post, mock_sleep, discord_alerts):
        """Test 429 response honors Retry-After header"""
        rate_limit_response = Mock()
        rate_limit_response.status_code = 429
        rate_limit_response.json.return_value = {'retry_after': 2.5}

        success_response = Mock()
        success_response.status_code = 204

        mock_post.side_effect = [rate_limit_response, success_response]

        result = discord_alerts.send_discord_message("Test", "Test")

        assert result is True
        mock_sleep.assert_called_once_with(2.5)

    @patch('services.discord_alerts.time.sleep')
    @patch('services.discord_alerts.requests.post')
    def test_no_retry_on_success(self, mock_post, mock_sleep, discord_alerts):
        """Test successful first attempt doesn't retry"""
        mock_post.return_value = Mock(status_code=204)

        result = discord_alerts.send_discord_message("Test", "Test")

        assert result is True
        assert mock_post.call_count == 1
        mock_sleep.assert_not_called()

    @patch('services.discord_alerts.time.sleep')
    @patch('services.discord_alerts.requests.post')
    def test_backoff_timing(self, mock_post, mock_sleep, discord_alerts):
        """Test exponential backoff delay values"""
        import requests as req
        mock_post.side_effect = req.exceptions.Timeout()

        discord_alerts.send_discord_message("Test", "Test")

        # Should sleep with delays [1, 4] (not after last attempt)
        assert mock_sleep.call_count == 2
        mock_sleep.assert_any_call(1)
        mock_sleep.assert_any_call(4)
