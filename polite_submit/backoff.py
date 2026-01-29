"""
Exponential backoff controller for polite-submit.

Implements CSMA/CA-style exponential backoff with jitter to prevent
synchronization among multiple polite clients.
"""

from __future__ import annotations

import random
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from polite_submit.config import Config


class BackoffController:
    """
    Controls exponential backoff timing between submission attempts.

    The backoff time doubles with each attempt, with random jitter to
    prevent multiple clients from synchronizing their retries.

    Attributes:
        attempt: Current attempt number (0-indexed)
        total_wait: Total time waited across all backoffs
    """

    def __init__(
        self,
        initial_backoff: float = 30.0,
        max_backoff: float = 1800.0,
        multiplier: float = 2.0,
        max_attempts: int = 20,
        jitter_range: tuple[float, float] = (0.5, 1.5),
    ):
        """
        Initialize backoff controller.

        Args:
            initial_backoff: Base backoff time in seconds
            max_backoff: Maximum backoff time in seconds
            multiplier: Backoff multiplier per attempt
            max_attempts: Maximum attempts before aborting
            jitter_range: (min, max) multiplier for randomization
        """
        self.initial_backoff = initial_backoff
        self.max_backoff = max_backoff
        self.multiplier = multiplier
        self.max_attempts = max_attempts
        self.jitter_range = jitter_range

        self.attempt = 0
        self.total_wait = 0.0

    @classmethod
    def from_config(cls, config: Config) -> BackoffController:
        """Create a BackoffController from configuration."""
        return cls(
            initial_backoff=config.initial_backoff,
            max_backoff=config.max_backoff,
            multiplier=config.backoff_multiplier,
            max_attempts=config.max_attempts,
        )

    def calculate_wait(self) -> float:
        """
        Calculate the next wait time without actually waiting.

        Returns:
            Wait time in seconds
        """
        base = self.initial_backoff * (self.multiplier**self.attempt)
        jitter = random.uniform(*self.jitter_range)
        return min(base * jitter, self.max_backoff)

    def wait(self) -> float:
        """
        Calculate and execute backoff wait.

        Increments the attempt counter and sleeps for the calculated time.

        Returns:
            Actual wait time in seconds
        """
        wait_time = self.calculate_wait()
        self.attempt += 1
        self.total_wait += wait_time
        time.sleep(wait_time)
        return wait_time

    def wait_async(self) -> float:
        """
        Calculate backoff without sleeping.

        Useful for async code or when caller wants to handle the wait.
        Increments attempt counter.

        Returns:
            Wait time in seconds (caller should sleep)
        """
        wait_time = self.calculate_wait()
        self.attempt += 1
        self.total_wait += wait_time
        return wait_time

    def reset(self) -> None:
        """Reset backoff state after successful submission."""
        self.attempt = 0
        # Note: total_wait is not reset, for statistics

    @property
    def should_abort(self) -> bool:
        """Check if maximum attempts have been exceeded."""
        return self.attempt >= self.max_attempts

    @property
    def next_wait_estimate(self) -> float:
        """Estimate next wait time (without jitter, for display)."""
        base = self.initial_backoff * (self.multiplier**self.attempt)
        return min(base, self.max_backoff)

    def __str__(self) -> str:
        return (
            f"BackoffController(attempt={self.attempt}, "
            f"next~{self.next_wait_estimate:.0f}s, "
            f"total={self.total_wait:.0f}s)"
        )


def format_duration(seconds: float) -> str:
    """Format seconds as human-readable duration."""
    if seconds < 60:
        return f"{seconds:.0f}s"
    elif seconds < 3600:
        minutes = seconds / 60
        return f"{minutes:.1f}m"
    else:
        hours = seconds / 3600
        return f"{hours:.1f}h"
