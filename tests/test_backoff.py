"""Tests for the backoff module."""

from unittest.mock import patch

from polite_submit.backoff import BackoffController, format_duration
from polite_submit.config import Config


class TestBackoffController:
    def test_initial_state(self):
        controller = BackoffController()
        assert controller.attempt == 0
        assert controller.total_wait == 0.0
        assert not controller.should_abort

    def test_calculate_wait_increases(self):
        controller = BackoffController(initial_backoff=10, jitter_range=(1.0, 1.0))
        wait1 = controller.calculate_wait()
        controller.attempt = 1
        wait2 = controller.calculate_wait()
        controller.attempt = 2
        wait3 = controller.calculate_wait()

        assert wait1 == 10
        assert wait2 == 20
        assert wait3 == 40

    def test_calculate_wait_respects_max(self):
        controller = BackoffController(
            initial_backoff=100,
            max_backoff=150,
            jitter_range=(1.0, 1.0),
        )
        controller.attempt = 10  # Would be 100 * 2^10 = 102400
        wait = controller.calculate_wait()
        assert wait == 150

    def test_wait_async_increments_attempt(self):
        controller = BackoffController(jitter_range=(1.0, 1.0))
        assert controller.attempt == 0
        controller.wait_async()
        assert controller.attempt == 1
        controller.wait_async()
        assert controller.attempt == 2

    def test_wait_async_tracks_total(self):
        controller = BackoffController(
            initial_backoff=10,
            jitter_range=(1.0, 1.0),
        )
        controller.wait_async()  # 10
        controller.wait_async()  # 20
        assert controller.total_wait == 30

    def test_reset_clears_attempt(self):
        controller = BackoffController()
        controller.attempt = 5
        controller.total_wait = 100
        controller.reset()
        assert controller.attempt == 0
        assert controller.total_wait == 100  # Not reset

    def test_should_abort_after_max_attempts(self):
        controller = BackoffController(max_attempts=3)
        assert not controller.should_abort
        controller.attempt = 2
        assert not controller.should_abort
        controller.attempt = 3
        assert controller.should_abort

    def test_jitter_within_range(self):
        controller = BackoffController(
            initial_backoff=100,
            jitter_range=(0.5, 1.5),
        )
        for _ in range(100):
            wait = controller.calculate_wait()
            assert 50 <= wait <= 150

    def test_from_config(self):
        config = Config(
            initial_backoff=60,
            max_backoff=3600,
            backoff_multiplier=3.0,
            max_attempts=10,
        )
        controller = BackoffController.from_config(config)
        assert controller.initial_backoff == 60
        assert controller.max_backoff == 3600
        assert controller.multiplier == 3.0
        assert controller.max_attempts == 10

    def test_str_representation(self):
        controller = BackoffController(initial_backoff=30)
        controller.attempt = 2
        controller.total_wait = 90
        s = str(controller)
        assert "attempt=2" in s
        assert "total=90" in s

    def test_wait_calls_sleep(self):
        controller = BackoffController(
            initial_backoff=10,
            jitter_range=(1.0, 1.0),  # No jitter for predictable test
        )
        with patch("polite_submit.backoff.time.sleep") as mock_sleep:
            wait_time = controller.wait()
            assert wait_time == 10
            mock_sleep.assert_called_once_with(10)
            assert controller.attempt == 1
            assert controller.total_wait == 10

    def test_wait_increments_state(self):
        controller = BackoffController(
            initial_backoff=5,
            jitter_range=(1.0, 1.0),
        )
        with patch("polite_submit.backoff.time.sleep"):
            controller.wait()  # 5
            controller.wait()  # 10
            controller.wait()  # 20

            assert controller.attempt == 3
            assert controller.total_wait == 35

    def test_wait_respects_max_backoff(self):
        controller = BackoffController(
            initial_backoff=100,
            max_backoff=50,
            jitter_range=(1.0, 1.0),
        )
        with patch("polite_submit.backoff.time.sleep") as mock_sleep:
            wait_time = controller.wait()
            assert wait_time == 50  # Capped at max
            mock_sleep.assert_called_once_with(50)

    def test_next_wait_estimate(self):
        controller = BackoffController(
            initial_backoff=10,
            multiplier=2.0,
        )
        assert controller.next_wait_estimate == 10
        controller.attempt = 1
        assert controller.next_wait_estimate == 20
        controller.attempt = 2
        assert controller.next_wait_estimate == 40

    def test_next_wait_estimate_respects_max(self):
        controller = BackoffController(
            initial_backoff=100,
            max_backoff=150,
            multiplier=2.0,
        )
        controller.attempt = 5  # Would be 100 * 32 = 3200
        assert controller.next_wait_estimate == 150


class TestFormatDuration:
    def test_seconds(self):
        assert format_duration(45) == "45s"

    def test_minutes(self):
        assert format_duration(90) == "1.5m"

    def test_hours(self):
        assert format_duration(7200) == "2.0h"

    def test_boundary_seconds_minutes(self):
        assert format_duration(59) == "59s"
        assert format_duration(60) == "1.0m"

    def test_boundary_minutes_hours(self):
        assert format_duration(3599) == "60.0m"
        assert format_duration(3600) == "1.0h"
