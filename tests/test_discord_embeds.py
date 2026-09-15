"""
Test Discord embed formatting, attachments, periodic updates, image-path
validation, and webhook-token redaction.

Split out of test_discord.py (which keeps config/connection/send/retry
tests) to stay under the file-size cap.
"""
import pytest
import os
import sys
from unittest.mock import Mock, patch

project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)


class TestDiscordEmbed:
    """Test Discord embed structure"""
    
    @patch('services.discord_alerts.requests.post')
    def test_embed_has_timestamp(self, mock_post):
        """Test embed includes timestamp"""
        from services.discord_alerts import DiscordAlerts
        
        config = Mock()
        config.get = lambda key, default=None: {
            'discord': {
                'enabled': True,
                'webhook_url': 'https://test',
                'embed_color_hex': '#0EA5E9'
            }
        }.get(key, default)
        
        mock_response = Mock()
        mock_response.status_code = 204
        mock_post.return_value = mock_response
        
        alerts = DiscordAlerts(config)
        alerts.send_discord_message("Test", "Test")
        
        call_args = mock_post.call_args
        payload = call_args[1]['json']
        embed = payload['embeds'][0]
        
        assert 'timestamp' in embed
    
    @patch('services.discord_alerts.requests.post')
    def test_embed_has_footer(self, mock_post):
        """Test embed includes footer with level"""
        from services.discord_alerts import DiscordAlerts
        
        config = Mock()
        config.get = lambda key, default=None: {
            'discord': {
                'enabled': True,
                'webhook_url': 'https://test',
                'embed_color_hex': '#0EA5E9'
            }
        }.get(key, default)
        
        mock_response = Mock()
        mock_response.status_code = 204
        mock_post.return_value = mock_response
        
        alerts = DiscordAlerts(config)
        alerts.send_discord_message("Test", "Test", level="warning")
        
        call_args = mock_post.call_args
        payload = call_args[1]['json']
        embed = payload['embeds'][0]
        
        assert 'footer' in embed
        assert 'WARNING' in embed['footer']['text']


class TestDiscordImageAttachment:
    """Test image attachment functionality"""
    
    @patch('services.discord_alerts.requests.post')
    def test_send_with_image_attachment(self, mock_post, tmp_path):
        """Test sending message with image attachment"""
        from services.discord_alerts import DiscordAlerts
        
        # Create test image
        test_image = tmp_path / "test.jpg"
        test_image.write_bytes(b'\xff\xd8\xff\xe0' + b'\x00' * 100)
        
        config = Mock()
        config.get = lambda key, default=None: {
            'discord': {
                'enabled': True,
                'webhook_url': 'https://test',
                'embed_color_hex': '#0EA5E9',
                'include_latest_image': True
            }
        }.get(key, default)
        
        mock_response = Mock()
        mock_response.status_code = 204
        mock_post.return_value = mock_response
        
        alerts = DiscordAlerts(config)
        result = alerts.send_discord_message(
            "Test",
            "Test with image",
            image_path=str(test_image)
        )
        
        assert result is True
        
        # Should have used multipart form
        call_args = mock_post.call_args
        assert 'files' in call_args[1]
    
    def test_send_with_nonexistent_image(self):
        """Test handling of non-existent image path"""
        from services.discord_alerts import DiscordAlerts
        
        config = Mock()
        config.get = lambda key, default=None: {
            'discord': {
                'enabled': True,
                'webhook_url': 'https://test',
                'embed_color_hex': '#0EA5E9',
                'include_latest_image': True
            }
        }.get(key, default)
        
        alerts = DiscordAlerts(config)
        
        with patch('services.discord_alerts.requests.post') as mock_post:
            mock_response = Mock()
            mock_response.status_code = 204
            mock_post.return_value = mock_response
            
            result = alerts.send_discord_message(
                "Test",
                "Test",
                image_path="/nonexistent/image.jpg"
            )
            
            # Should still succeed without image
            assert result is True
            
            # Should have used json, not files
            call_args = mock_post.call_args
            assert 'json' in call_args[1]


class TestPeriodicUpdates:
    """Test periodic update functionality"""
    
    def test_periodic_disabled(self):
        """Test periodic updates when disabled"""
        from services.discord_alerts import DiscordAlerts
        
        config = Mock()
        config.get = lambda key, default=None: {
            'discord': {
                'enabled': True,
                'webhook_url': 'https://test',
                'periodic_enabled': False
            }
        }.get(key, default)
        
        alerts = DiscordAlerts(config)
        result = alerts.send_periodic_update()
        
        assert result is False
    
    @patch('services.discord_alerts.requests.post')
    def test_periodic_enabled(self, mock_post, tmp_path):
        """Test periodic updates when enabled"""
        from services.discord_alerts import DiscordAlerts
        
        # Create test image
        test_image = tmp_path / "latest.jpg"
        test_image.write_bytes(b'\xff\xd8\xff\xe0' + b'\x00' * 100)
        
        config = Mock()
        config.get = lambda key, default=None: {
            'discord': {
                'enabled': True,
                'webhook_url': 'https://test',
                'embed_color_hex': '#0EA5E9',
                'periodic_enabled': True,
                'include_latest_image': True
            }
        }.get(key, default)
        
        mock_response = Mock()
        mock_response.status_code = 204
        mock_post.return_value = mock_response
        
        alerts = DiscordAlerts(config)
        result = alerts.send_periodic_update(latest_image_path=str(test_image))
        
        assert result is True


class TestDiscordImagePathValidation:
    """Test that Discord methods properly validate image paths"""
    
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
            }
        }
    
    @pytest.fixture
    def discord_alerts(self, discord_config):
        """Create DiscordAlerts instance"""
        from services.discord_alerts import DiscordAlerts
        
        config = Mock()
        config.get = lambda key, default=None: discord_config.get(key, default)
        
        return DiscordAlerts(config)
    
    @patch('services.discord_alerts.requests.post')
    def test_send_message_with_pil_image_fails_gracefully(self, mock_post, discord_alerts):
        """Test that passing PIL Image instead of path is handled safely
        
        This tests the fix for: Discord webhook error: _path_exists: 
        path should be string, bytes, os.PathLike or integer, not Image
        """
        from PIL import Image
        
        # Create a PIL Image object (NOT a path)
        pil_image = Image.new('RGB', (100, 100), color='red')
        
        mock_response = Mock()
        mock_response.status_code = 204
        mock_post.return_value = mock_response
        
        # Should NOT crash - PIL Image should be treated as invalid path
        # The os.path.exists check will handle this by catching the TypeError
        result = discord_alerts.send_discord_message(
            "Test Title",
            "Test Description",
            level="info",
            image_path=pil_image  # Wrong type! Should be str, not Image
        )
        
        # Should succeed (message sent without image) or fail gracefully
        # The key is it should NOT crash with an exception
        assert result in [True, False]
    
    @patch('services.discord_alerts.requests.post')
    def test_send_message_with_valid_path(self, mock_post, discord_alerts, tmp_path):
        """Test that valid string path works correctly"""
        # Create a test image file
        test_image = tmp_path / "test.jpg"
        test_image.write_bytes(b'\xff\xd8\xff\xe0' + b'\x00' * 100)
        
        mock_response = Mock()
        mock_response.status_code = 204
        mock_post.return_value = mock_response
        
        result = discord_alerts.send_discord_message(
            "Test Title",
            "Test Description",
            level="info",
            image_path=str(test_image)  # Correct: string path
        )
        
        assert result is True
    
    @patch('services.discord_alerts.requests.post')
    def test_send_message_with_none_path(self, mock_post, discord_alerts):
        """Test that None path sends text-only message"""
        mock_response = Mock()
        mock_response.status_code = 204
        mock_post.return_value = mock_response
        
        result = discord_alerts.send_discord_message(
            "Test Title",
            "Test Description",
            level="info",
            image_path=None
        )
        
        assert result is True


class TestDiscordTokenRedaction:
    """T5 — a webhook token in an exception must never reach logs or status.

    requests reports the failed request as a BARE PATH (not a full URL): for
    Discord that is /api/webhooks/<id>/<token> — a live secret. Logs egress to
    PostHog over OTLP, so any leak leaves the host.
    """

    SENTINEL = "s3cr3t-webhook-token-DO-NOT-LEAK"
    WEBHOOK = f"https://discord.com/api/webhooks/123456789/{SENTINEL}"
    # The shape requests actually produces — webhook appears as a bare path.
    REAL_MSG = (
        "HTTPSConnectionPool(host='discord.com', port=443): Max retries "
        f"exceeded with url: /api/webhooks/123456789/{SENTINEL} "
        "(Caused by NewConnectionError('<urllib3.connection."
        "HTTPSConnection object>: Failed to establish a new connection'))"
    )

    def _alerts(self):
        from services.discord_alerts import DiscordAlerts
        cfg = {'discord': {'enabled': True, 'webhook_url': self.WEBHOOK,
                           'include_latest_image': False}}
        config = Mock()
        config.get = lambda key, default=None: cfg.get(key, default)
        return DiscordAlerts(config)

    @patch('services.discord_alerts.time.sleep', lambda *a, **k: None)
    @patch('services.discord_alerts.requests.post')
    def test_connection_error_token_not_logged_or_stored(self, mock_post):
        import requests
        from services import discord_alerts as da

        # The realistic requests ConnectionError — bare webhook path embedded.
        mock_post.side_effect = requests.exceptions.ConnectionError(self.REAL_MSG)

        logged = []
        with patch.object(da.app_logger, 'error', lambda msg: logged.append(str(msg))), \
             patch.object(da.app_logger, 'warning', lambda msg: logged.append(str(msg))):
            alerts = self._alerts()
            result = alerts.send_discord_message("T", "D", level="error")

        assert result is False
        assert self.SENTINEL not in alerts.last_send_status
        assert all(self.SENTINEL not in line for line in logged), logged

    @patch('services.discord_alerts.time.sleep', lambda *a, **k: None)
    @patch('services.discord_alerts.requests.post')
    def test_generic_error_token_not_logged_or_stored(self, mock_post):
        from services import discord_alerts as da

        # A non-retryable exception carrying the same bare-path webhook leak.
        mock_post.side_effect = RuntimeError(self.REAL_MSG)

        logged = []
        with patch.object(da.app_logger, 'error', lambda msg: logged.append(str(msg))):
            alerts = self._alerts()
            result = alerts.send_discord_message("T", "D", level="error")

        assert result is False
        assert self.SENTINEL not in alerts.last_send_status
        assert all(self.SENTINEL not in line for line in logged), logged

    def test_redactor_strips_bare_webhook_path(self):
        from services.discord_alerts import redact_discord_error
        out = redact_discord_error(RuntimeError(self.REAL_MSG))
        assert self.SENTINEL not in out
        assert "/api/webhooks/[REDACTED]" in out
