"""
Submission decision logic for polite-submit.

Determines whether to submit a job, backoff, or abort based on cluster state
and configuration thresholds.
"""

from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from polite_submit.config import Config
    from polite_submit.prober import ClusterState


class Decision(Enum):
    """Possible submission decisions."""

    SUBMIT = "submit"
    BACKOFF = "backoff"
    ABORT = "abort"


def decide(state: ClusterState, config: Config) -> tuple[Decision, str]:
    """
    Decide whether to submit, backoff, or abort based on cluster state.

    The decision follows this priority:
    1. Self-limiting: Don't exceed own running/pending limits
    2. Courtesy: Yield to others when queue is congested
    3. Utilization: Back off when cluster is heavily loaded

    During peak hours, stricter limits apply.

    Args:
        state: Current cluster state snapshot
        config: Configuration with thresholds

    Returns:
        Tuple of (Decision, reason_string)
    """
    from polite_submit.config import is_peak_hours

    # Determine effective limits based on peak hours
    if is_peak_hours(config):
        max_concurrent = config.peak_max_concurrent
        max_pending = max(1, config.max_pending - 1)  # Stricter during peak
    else:
        max_concurrent = config.max_concurrent
        max_pending = config.max_pending

    # Self-limiting checks (highest priority)
    if state.my_running >= max_concurrent:
        return (
            Decision.BACKOFF,
            f"Already running {state.my_running}/{max_concurrent} jobs",
        )

    if state.my_pending >= max_pending:
        return (
            Decision.BACKOFF,
            f"Already {state.my_pending}/{max_pending} jobs pending",
        )

    # Courtesy checks (yield to others)
    if state.others_pending > config.queue_threshold:
        threshold = config.queue_threshold
        return (
            Decision.BACKOFF,
            f"{state.others_pending} others waiting (threshold: {threshold})",
        )

    # Utilization check
    if state.utilization > config.util_threshold:
        return (
            Decision.BACKOFF,
            f"Cluster {state.utilization*100:.0f}% utilized "
            f"(threshold: {config.util_threshold*100:.0f}%)",
        )

    # All checks passed
    return Decision.SUBMIT, "Clear to submit"


def should_submit(state: ClusterState, config: Config) -> bool:
    """
    Simple boolean check for whether submission is allowed.

    Args:
        state: Current cluster state
        config: Configuration

    Returns:
        True if submission is allowed
    """
    decision, _ = decide(state, config)
    return decision == Decision.SUBMIT
