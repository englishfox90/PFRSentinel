"""
Test heartbeat writer and staleness detection
"""
import pytest
import json
import os
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from services.heartbeat import (
    write_heartbeat, read_heartbeat, is_heartbeat_stale, HeartbeatWriter,
    get_heartbeat_path
)


class TestHeartbeatWrite:
    """Test heartbeat file writing"""

    def test_write_creates_file_with_valid_timestamp(self, temp_dir):
        """Test heartbeat file is written with valid timestamp"""
        path = os.path.join(temp_dir, 'heartbeat.json')
        result = write_heartbeat(path)

        assert result is True
        assert os.path.exists(path)

        with open(path, 'r') as f:
            data = json.load(f)

        assert 'timestamp' in data
        assert 'pid' in data
        # Should be parseable as ISO datetime
        ts = datetime.fromisoformat(data['timestamp'])
        assert ts.tzinfo is not None

    def test_write_updates_on_subsequent_calls(self, temp_dir):
        """Test heartbeat file is updated on subsequent writes"""
        path = os.path.join(temp_dir, 'heartbeat.json')

        write_heartbeat(path)
        with open(path, 'r') as f:
            first = json.load(f)

        time.sleep(0.05)
        write_heartbeat(path)
        with open(path, 'r') as f:
            second = json.load(f)

        # Timestamps should differ
        assert first['timestamp'] != second['timestamp']

    def test_write_creates_parent_directory(self, temp_dir):
        """Test heartbeat file creation when directory doesn't exist"""
        path = os.path.join(temp_dir, 'nested', 'subdir', 'heartbeat.json')
        result = write_heartbeat(path)

        assert result is True
        assert os.path.exists(path)


class TestHeartbeatRead:
    """Test heartbeat file reading"""

    def test_read_returns_dict_with_timestamp(self, temp_dir):
        """Test reading a valid heartbeat returns parsed data"""
        path = os.path.join(temp_dir, 'heartbeat.json')
        write_heartbeat(path)

        hb = read_heartbeat(path)
        assert hb is not None
        assert isinstance(hb['timestamp'], datetime)
        assert isinstance(hb['pid'], int)

    def test_read_nonexistent_returns_none(self, temp_dir):
        """Test reading nonexistent file returns None"""
        path = os.path.join(temp_dir, 'nonexistent.json')
        assert read_heartbeat(path) is None

    def test_read_corrupt_file_returns_none(self, temp_dir):
        """Test reading corrupt file returns None"""
        path = os.path.join(temp_dir, 'heartbeat.json')
        with open(path, 'w') as f:
            f.write('not json')

        assert read_heartbeat(path) is None


class TestHeartbeatStaleness:
    """Test heartbeat staleness detection"""

    def test_fresh_heartbeat_is_not_stale(self, temp_dir):
        """Test supervisor detects healthy heartbeat (no restart triggered)"""
        path = os.path.join(temp_dir, 'heartbeat.json')
        write_heartbeat(path)

        assert is_heartbeat_stale(path, interval=30) is False

    def test_old_heartbeat_is_stale(self, temp_dir):
        """Test supervisor detects stale heartbeat"""
        path = os.path.join(temp_dir, 'heartbeat.json')
        # Write a heartbeat with a timestamp 5 minutes ago
        old_time = datetime.now(timezone.utc) - timedelta(minutes=5)
        data = {'timestamp': old_time.isoformat(), 'pid': os.getpid()}
        with open(path, 'w') as f:
            json.dump(data, f)

        # With 30s interval and 3x multiplier, 5 min is definitely stale
        assert is_heartbeat_stale(path, interval=30) is True

    def test_missing_heartbeat_is_stale(self, temp_dir):
        """Test missing heartbeat file is treated as stale"""
        path = os.path.join(temp_dir, 'nonexistent.json')
        assert is_heartbeat_stale(path) is True


class TestHeartbeatWriter:
    """Test HeartbeatWriter background thread"""

    def test_writer_creates_heartbeat_on_start(self, temp_dir):
        """Test HeartbeatWriter writes immediately on start"""
        path = os.path.join(temp_dir, 'heartbeat.json')
        writer = HeartbeatWriter(interval=60, path=path)
        writer.start()
        time.sleep(0.2)  # Give thread time to write
        writer.stop()

        assert os.path.exists(path)
        hb = read_heartbeat(path)
        assert hb is not None

    def test_writer_stop_is_clean(self, temp_dir):
        """Test HeartbeatWriter stops cleanly without hanging"""
        path = os.path.join(temp_dir, 'heartbeat.json')
        writer = HeartbeatWriter(interval=1, path=path)
        writer.start()
        time.sleep(0.2)
        writer.stop()
        # Should not hang — if it does, test will timeout
        assert writer._thread is None


class TestHeartbeatPathResolution:
    """get_heartbeat_path() must not read LOCALAPPDATA directly.

    It used to, with an empty-string default — off Windows that produced a
    relative 'PFRSentinel/heartbeat.json' and write_heartbeat() created it
    under whatever the process working directory happened to be.
    """

    def test_path_is_absolute_without_localappdata(self, monkeypatch, tmp_path):
        monkeypatch.delenv('LOCALAPPDATA', raising=False)
        monkeypatch.chdir(tmp_path)

        path = get_heartbeat_path()

        assert os.path.isabs(path)
        assert os.path.basename(path) == 'heartbeat.json'
        assert tmp_path not in Path(path).parents

    def test_path_does_not_follow_the_working_directory(self, monkeypatch, tmp_path):
        """Same resolver, two different cwds — the answer may not move."""
        monkeypatch.delenv('LOCALAPPDATA', raising=False)

        monkeypatch.chdir(tmp_path)
        first = os.path.abspath(get_heartbeat_path())
        nested = tmp_path / 'elsewhere'
        nested.mkdir()
        monkeypatch.chdir(nested)
        second = os.path.abspath(get_heartbeat_path())

        assert first == second
