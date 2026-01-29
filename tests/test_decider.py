"""Tests for the decider module."""

from polite_submit.config import Config
from polite_submit.decider import Decision, decide, should_submit
from polite_submit.prober import ClusterState


def make_state(
    utilization: float = 0.5,
    my_running: int = 0,
    my_pending: int = 0,
    others_pending: int = 0,
) -> ClusterState:
    """Helper to create ClusterState with specific values."""
    total = 10
    allocated = int(total * utilization)
    return ClusterState(
        partition="gpu",
        total_nodes=total,
        allocated_nodes=allocated,
        idle_nodes=total - allocated,
        my_running=my_running,
        my_pending=my_pending,
        others_pending=others_pending,
    )


def make_config(**overrides) -> Config:
    """Helper to create Config with overrides."""
    defaults = {
        "max_concurrent": 4,
        "max_pending": 2,
        "queue_threshold": 10,
        "util_threshold": 0.85,
        "peak_enabled": False,  # Disable peak for predictable tests
    }
    defaults.update(overrides)
    return Config(**defaults)


class TestDecide:
    def test_submit_when_clear(self):
        state = make_state(utilization=0.5, my_running=0, others_pending=0)
        config = make_config()
        decision, reason = decide(state, config)
        assert decision == Decision.SUBMIT
        assert "Clear" in reason

    def test_backoff_too_many_running(self):
        state = make_state(my_running=4)
        config = make_config(max_concurrent=4)
        decision, reason = decide(state, config)
        assert decision == Decision.BACKOFF
        assert "running" in reason.lower()

    def test_backoff_too_many_pending(self):
        state = make_state(my_pending=2)
        config = make_config(max_pending=2)
        decision, reason = decide(state, config)
        assert decision == Decision.BACKOFF
        assert "pending" in reason.lower()

    def test_backoff_others_waiting(self):
        state = make_state(others_pending=15)
        config = make_config(queue_threshold=10)
        decision, reason = decide(state, config)
        assert decision == Decision.BACKOFF
        assert "others" in reason.lower()

    def test_backoff_high_utilization(self):
        state = make_state(utilization=0.9)
        config = make_config(util_threshold=0.85)
        decision, reason = decide(state, config)
        assert decision == Decision.BACKOFF
        assert "utilized" in reason.lower()

    def test_submit_at_threshold(self):
        state = make_state(
            utilization=0.84,
            my_running=3,
            my_pending=1,
            others_pending=9,
        )
        config = make_config(
            max_concurrent=4,
            max_pending=2,
            queue_threshold=10,
            util_threshold=0.85,
        )
        decision, _ = decide(state, config)
        assert decision == Decision.SUBMIT


class TestShouldSubmit:
    def test_returns_true_for_submit(self):
        state = make_state(utilization=0.5)
        config = make_config()
        assert should_submit(state, config) is True

    def test_returns_false_for_backoff(self):
        state = make_state(my_running=10)
        config = make_config(max_concurrent=4)
        assert should_submit(state, config) is False


class TestPeakHours:
    def test_stricter_during_peak(self):
        state = make_state(my_running=3)
        config = make_config(
            max_concurrent=4,
            peak_enabled=True,
            peak_max_concurrent=2,
            peak_hours=[(0, 24)],  # Always peak
            weekend_exempt=False,
        )
        decision, _ = decide(state, config)
        assert decision == Decision.BACKOFF

    def test_normal_outside_peak(self):
        state = make_state(my_running=3)
        config = make_config(
            max_concurrent=4,
            peak_enabled=True,
            peak_max_concurrent=2,
            peak_hours=[(0, 0)],  # Never peak (invalid range)
            weekend_exempt=False,
        )
        decision, _ = decide(state, config)
        assert decision == Decision.SUBMIT
