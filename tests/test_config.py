"""Tests for the config module."""

from datetime import datetime
from pathlib import Path
from unittest.mock import patch


from polite_submit.config import (
    Config,
    get_effective_username,
    is_peak_hours,
    load_config,
)


class TestConfig:
    def test_default_values(self):
        config = Config()
        assert config.host is None
        assert config.username is None
        assert config.partition == "gpu"
        assert config.max_concurrent == 4
        assert config.max_pending == 2
        assert config.queue_threshold == 10
        assert config.util_threshold == 0.85
        assert config.peak_enabled is True
        assert config.peak_hours == [(9, 17)]
        assert config.peak_max_concurrent == 2
        assert config.weekend_exempt is True
        assert config.initial_backoff == 30.0
        assert config.max_backoff == 1800.0
        assert config.backoff_multiplier == 2.0
        assert config.max_attempts == 20
        assert config.array_chunk_size == 10
        assert config.log_level == "INFO"
        assert config.log_file is None

    def test_aggressive_mode(self):
        config = Config(
            host="hpc",
            username="testuser",
            partition="gpu",
            log_level="DEBUG",
            log_file="/var/log/test.log",
        )
        aggressive = config.aggressive_mode()

        # Should preserve these
        assert aggressive.host == "hpc"
        assert aggressive.username == "testuser"
        assert aggressive.partition == "gpu"
        assert aggressive.log_level == "DEBUG"
        assert aggressive.log_file == "/var/log/test.log"

        # Should change these to aggressive values
        assert aggressive.max_concurrent == 100
        assert aggressive.max_pending == 100
        assert aggressive.queue_threshold == 1000
        assert aggressive.util_threshold == 1.0
        assert aggressive.peak_enabled is False
        assert aggressive.initial_backoff == 5.0
        assert aggressive.max_backoff == 60.0
        assert aggressive.backoff_multiplier == 1.5
        assert aggressive.max_attempts == 5
        assert aggressive.array_chunk_size == 100


class TestLoadConfig:
    def test_load_default_when_no_file(self, tmp_path):
        # Change to a directory with no config
        with patch.object(Path, "home", return_value=tmp_path):
            config = load_config(None)
            assert config.partition == "gpu"  # Default value

    def test_load_explicit_path(self, tmp_path):
        config_file = tmp_path / "test-config.yaml"
        config_file.write_text(
            """
cluster:
  partition: test-partition
  host: test-host
  username: testuser
politeness:
  max_concurrent_jobs: 8
  max_pending_jobs: 4
  queue_depth_threshold: 20
  utilization_threshold: 0.9
peak_hours:
  enabled: false
  schedule:
    - [8, 18]
  max_concurrent: 3
  weekend_exempt: false
backoff:
  initial_seconds: 60
  max_seconds: 3600
  multiplier: 3.0
  max_attempts: 10
array:
  chunk_size: 25
logging:
  level: DEBUG
  file: /tmp/polite.log
"""
        )
        config = load_config(str(config_file))

        assert config.partition == "test-partition"
        assert config.host == "test-host"
        assert config.username == "testuser"
        assert config.max_concurrent == 8
        assert config.max_pending == 4
        assert config.queue_threshold == 20
        assert config.util_threshold == 0.9
        assert config.peak_enabled is False
        assert config.peak_hours == [(8, 18)]
        assert config.peak_max_concurrent == 3
        assert config.weekend_exempt is False
        assert config.initial_backoff == 60
        assert config.max_backoff == 3600
        assert config.backoff_multiplier == 3.0
        assert config.max_attempts == 10
        assert config.array_chunk_size == 25
        assert config.log_level == "DEBUG"
        assert config.log_file == "/tmp/polite.log"

    def test_load_from_cwd(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        config_file = tmp_path / "polite-submit.yaml"
        config_file.write_text(
            """
cluster:
  partition: cwd-partition
"""
        )
        config = load_config(None)
        assert config.partition == "cwd-partition"

    def test_load_from_home_dir(self, tmp_path, monkeypatch):
        # Ensure no config in cwd
        cwd = tmp_path / "workdir"
        cwd.mkdir()
        monkeypatch.chdir(cwd)

        # Put config in home
        home_config = tmp_path / ".polite-submit.yaml"
        home_config.write_text(
            """
cluster:
  partition: home-partition
"""
        )
        with patch.object(Path, "home", return_value=tmp_path):
            config = load_config(None)
            assert config.partition == "home-partition"

    def test_load_from_config_dir(self, tmp_path, monkeypatch):
        # Ensure no config in cwd or home root
        cwd = tmp_path / "workdir"
        cwd.mkdir()
        monkeypatch.chdir(cwd)

        # Put config in ~/.config/polite-submit/
        config_dir = tmp_path / ".config" / "polite-submit"
        config_dir.mkdir(parents=True)
        config_file = config_dir / "config.yaml"
        config_file.write_text(
            """
cluster:
  partition: config-dir-partition
"""
        )
        with patch.object(Path, "home", return_value=tmp_path):
            config = load_config(None)
            assert config.partition == "config-dir-partition"

    def test_load_empty_yaml(self, tmp_path):
        config_file = tmp_path / "empty.yaml"
        config_file.write_text("")
        config = load_config(str(config_file))
        # Should use defaults
        assert config.partition == "gpu"

    def test_load_partial_yaml(self, tmp_path):
        config_file = tmp_path / "partial.yaml"
        config_file.write_text(
            """
cluster:
  partition: partial-partition
"""
        )
        config = load_config(str(config_file))
        assert config.partition == "partial-partition"
        # Other values should be defaults
        assert config.max_concurrent == 4


class TestIsPeakHours:
    def test_peak_disabled(self):
        config = Config(peak_enabled=False)
        # Even during peak hours, should return False
        monday_10am = datetime(2025, 1, 6, 10, 0)  # Monday
        assert is_peak_hours(config, monday_10am) is False

    def test_within_peak_hours(self):
        config = Config(peak_enabled=True, peak_hours=[(9, 17)])
        monday_10am = datetime(2025, 1, 6, 10, 0)  # Monday
        assert is_peak_hours(config, monday_10am) is True

    def test_outside_peak_hours_before(self):
        config = Config(peak_enabled=True, peak_hours=[(9, 17)])
        monday_7am = datetime(2025, 1, 6, 7, 0)  # Monday
        assert is_peak_hours(config, monday_7am) is False

    def test_outside_peak_hours_after(self):
        config = Config(peak_enabled=True, peak_hours=[(9, 17)])
        monday_8pm = datetime(2025, 1, 6, 20, 0)  # Monday
        assert is_peak_hours(config, monday_8pm) is False

    def test_peak_boundary_start(self):
        config = Config(peak_enabled=True, peak_hours=[(9, 17)])
        monday_9am = datetime(2025, 1, 6, 9, 0)  # Monday
        assert is_peak_hours(config, monday_9am) is True

    def test_peak_boundary_end(self):
        config = Config(peak_enabled=True, peak_hours=[(9, 17)])
        monday_5pm = datetime(2025, 1, 6, 17, 0)  # Monday
        # 17 is NOT < 17, so should be False
        assert is_peak_hours(config, monday_5pm) is False

    def test_weekend_exempt_saturday(self):
        config = Config(peak_enabled=True, peak_hours=[(9, 17)], weekend_exempt=True)
        saturday_10am = datetime(2025, 1, 4, 10, 0)  # Saturday
        assert is_peak_hours(config, saturday_10am) is False

    def test_weekend_exempt_sunday(self):
        config = Config(peak_enabled=True, peak_hours=[(9, 17)], weekend_exempt=True)
        sunday_10am = datetime(2025, 1, 5, 10, 0)  # Sunday
        assert is_peak_hours(config, sunday_10am) is False

    def test_weekend_not_exempt(self):
        config = Config(peak_enabled=True, peak_hours=[(9, 17)], weekend_exempt=False)
        saturday_10am = datetime(2025, 1, 4, 10, 0)  # Saturday
        assert is_peak_hours(config, saturday_10am) is True

    def test_multiple_peak_windows(self):
        config = Config(peak_enabled=True, peak_hours=[(9, 12), (13, 17)])
        monday_10am = datetime(2025, 1, 6, 10, 0)
        monday_1230pm = datetime(2025, 1, 6, 12, 30)
        monday_2pm = datetime(2025, 1, 6, 14, 0)

        assert is_peak_hours(config, monday_10am) is True
        assert is_peak_hours(config, monday_1230pm) is False  # Lunch break
        assert is_peak_hours(config, monday_2pm) is True

    def test_default_now(self):
        config = Config(peak_enabled=True, peak_hours=[(0, 24)])  # Always peak
        # Should use current time - just verify it doesn't crash
        result = is_peak_hours(config)
        assert isinstance(result, bool)


class TestGetEffectiveUsername:
    def test_explicit_username(self):
        config = Config(username="explicit_user")
        assert get_effective_username(config) == "explicit_user"

    def test_from_user_env(self, monkeypatch):
        config = Config(username=None)
        monkeypatch.setenv("USER", "env_user")
        monkeypatch.delenv("USERNAME", raising=False)
        assert get_effective_username(config) == "env_user"

    def test_from_username_env(self, monkeypatch):
        config = Config(username=None)
        monkeypatch.delenv("USER", raising=False)
        monkeypatch.setenv("USERNAME", "windows_user")
        assert get_effective_username(config) == "windows_user"

    def test_fallback_unknown(self, monkeypatch):
        config = Config(username=None)
        monkeypatch.delenv("USER", raising=False)
        monkeypatch.delenv("USERNAME", raising=False)
        assert get_effective_username(config) == "unknown"
