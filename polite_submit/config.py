"""
Configuration handling for polite-submit.

Loads configuration from YAML files with sensible defaults.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

import yaml


@dataclass
class Config:
    """Configuration for polite-submit behavior."""

    # Cluster connection
    host: Optional[str] = None  # SSH host alias, None for local
    username: Optional[str] = None  # Slurm username, defaults to $USER
    partition: str = "gpu"  # Default partition

    # Politeness thresholds
    max_concurrent: int = 4  # Max running jobs at once
    max_pending: int = 2  # Max jobs waiting in queue
    queue_threshold: int = 10  # Back off if this many others pending
    util_threshold: float = 0.85  # Back off if cluster this full

    # Peak hours settings
    peak_enabled: bool = True
    peak_hours: list[tuple[int, int]] = field(
        default_factory=lambda: [(9, 17)]
    )  # 9 AM - 5 PM
    peak_max_concurrent: int = 2  # Stricter during peak
    weekend_exempt: bool = True  # No peak restrictions on weekends

    # Backoff settings
    initial_backoff: float = 30.0  # Initial backoff in seconds
    max_backoff: float = 1800.0  # Max backoff (30 minutes)
    backoff_multiplier: float = 2.0
    max_attempts: int = 20

    # Array job settings
    array_chunk_size: int = 10  # Submit arrays in chunks of this size

    # Logging
    log_level: str = "INFO"
    log_file: Optional[str] = None

    def aggressive_mode(self) -> Config:
        """Return a copy with aggressive (less polite) settings."""
        return Config(
            host=self.host,
            username=self.username,
            partition=self.partition,
            max_concurrent=100,
            max_pending=100,
            queue_threshold=1000,
            util_threshold=1.0,
            peak_enabled=False,
            peak_hours=self.peak_hours,
            peak_max_concurrent=100,
            weekend_exempt=True,
            initial_backoff=5.0,
            max_backoff=60.0,
            backoff_multiplier=1.5,
            max_attempts=5,
            array_chunk_size=100,
            log_level=self.log_level,
            log_file=self.log_file,
        )


def load_config(path: Optional[str] = None) -> Config:
    """
    Load configuration from YAML file.

    Searches for config in order:
    1. Explicit path argument
    2. ./polite-submit.yaml
    3. ~/.polite-submit.yaml
    4. ~/.config/polite-submit/config.yaml

    If no file found, returns default configuration.

    Args:
        path: Explicit path to config file

    Returns:
        Config object
    """
    search_paths = []

    if path:
        search_paths.append(Path(path))
    else:
        search_paths.extend(
            [
                Path("polite-submit.yaml"),
                Path.home() / ".polite-submit.yaml",
                Path.home() / ".config" / "polite-submit" / "config.yaml",
            ]
        )

    for config_path in search_paths:
        if config_path.exists():
            return _load_yaml_config(config_path)

    return Config()


def _load_yaml_config(path: Path) -> Config:
    """Load config from a YAML file."""
    with open(path) as f:
        data = yaml.safe_load(f) or {}

    # Flatten nested structure
    cluster = data.get("cluster", {})
    politeness = data.get("politeness", {})
    peak = data.get("peak_hours", {})
    backoff = data.get("backoff", {})
    array = data.get("array", {})
    logging = data.get("logging", {})

    # Parse peak hours schedule
    peak_hours_raw = peak.get("schedule", [(9, 17)])
    peak_hours = [tuple(h) for h in peak_hours_raw]

    return Config(
        # Cluster
        host=cluster.get("host"),
        username=cluster.get("username"),
        partition=cluster.get("partition", "gpu"),
        # Politeness
        max_concurrent=politeness.get("max_concurrent_jobs", 4),
        max_pending=politeness.get("max_pending_jobs", 2),
        queue_threshold=politeness.get("queue_depth_threshold", 10),
        util_threshold=politeness.get("utilization_threshold", 0.85),
        # Peak hours
        peak_enabled=peak.get("enabled", True),
        peak_hours=peak_hours,
        peak_max_concurrent=peak.get("max_concurrent", 2),
        weekend_exempt=peak.get("weekend_exempt", True),
        # Backoff
        initial_backoff=backoff.get("initial_seconds", 30.0),
        max_backoff=backoff.get("max_seconds", 1800.0),
        backoff_multiplier=backoff.get("multiplier", 2.0),
        max_attempts=backoff.get("max_attempts", 20),
        # Array
        array_chunk_size=array.get("chunk_size", 10),
        # Logging
        log_level=logging.get("level", "INFO"),
        log_file=logging.get("file"),
    )


def is_peak_hours(config: Config, now: Optional[datetime] = None) -> bool:
    """
    Check if current time is within peak hours.

    Args:
        config: Configuration with peak hour settings
        now: Time to check (defaults to current time)

    Returns:
        True if within peak hours and peak checking is enabled
    """
    if not config.peak_enabled:
        return False

    if now is None:
        now = datetime.now()

    # Check weekend exemption
    if config.weekend_exempt and now.weekday() >= 5:  # Saturday=5, Sunday=6
        return False

    # Check if current hour is within any peak window
    current_hour = now.hour
    for start_hour, end_hour in config.peak_hours:
        if start_hour <= current_hour < end_hour:
            return True

    return False


def get_effective_username(config: Config) -> str:
    """Get the effective username for Slurm queries."""
    if config.username:
        return config.username
    return os.environ.get("USER", os.environ.get("USERNAME", "unknown"))
