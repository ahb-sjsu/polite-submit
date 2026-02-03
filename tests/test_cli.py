"""Tests for the CLI module."""

import subprocess
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from polite_submit.cli import (
    echo_status,
    main,
    submit_array_chunked,
    submit_batch,
    submit_job,
    submit_single,
)
from polite_submit.config import Config
from polite_submit.prober import ClusterState


@pytest.fixture
def runner():
    """Create a Click CLI test runner."""
    return CliRunner()


@pytest.fixture
def default_config():
    """Create a default config for testing."""
    return Config()


@pytest.fixture
def mock_cluster_state():
    """Create a mock cluster state that allows submission."""
    return ClusterState(
        partition="gpu",
        total_nodes=10,
        allocated_nodes=5,
        idle_nodes=5,
        my_running=1,
        my_pending=0,
        others_pending=2,
    )


class TestEchoStatus:
    def test_echo_info(self, capsys):
        with patch("polite_submit.cli.click.echo") as mock_echo:
            echo_status("Test message", "info")
            mock_echo.assert_called_once()
            call_arg = mock_echo.call_args[0][0]
            assert "Test message" in call_arg

    def test_echo_success(self):
        with patch("polite_submit.cli.click.echo") as mock_echo:
            echo_status("Success!", "success")
            mock_echo.assert_called_once()

    def test_echo_warning(self):
        with patch("polite_submit.cli.click.echo") as mock_echo:
            echo_status("Warning!", "warning")
            mock_echo.assert_called_once()

    def test_echo_error(self):
        with patch("polite_submit.cli.click.echo") as mock_echo:
            echo_status("Error!", "error")
            mock_echo.assert_called_once()

    def test_echo_wait(self):
        with patch("polite_submit.cli.click.echo") as mock_echo:
            echo_status("Waiting...", "wait")
            mock_echo.assert_called_once()

    def test_echo_unknown_level(self):
        with patch("polite_submit.cli.click.echo") as mock_echo:
            echo_status("Unknown level", "unknown")
            mock_echo.assert_called_once()


class TestSubmitJob:
    def test_dry_run_returns_fake_id(self, default_config):
        result = submit_job("job.sh", default_config, dry_run=True)
        assert result == "DRY-RUN-12345"

    def test_successful_submission(self, default_config):
        with patch("polite_submit.cli.run_cmd") as mock_run:
            mock_run.return_value = "Submitted batch job 12345"
            result = submit_job("job.sh", default_config, dry_run=False)
            assert result == "12345"

    def test_submission_with_extra_args(self, default_config):
        with patch("polite_submit.cli.run_cmd") as mock_run:
            mock_run.return_value = "Submitted batch job 99999"
            result = submit_job(
                "job.sh", default_config, dry_run=False, extra_args=["--array=0-10"]
            )
            assert result == "99999"
            # Verify command includes extra args
            call_cmd = mock_run.call_args[0][0]
            assert "--array=0-10" in call_cmd

    def test_submission_returns_raw_output_if_no_job_id(self, default_config):
        with patch("polite_submit.cli.run_cmd") as mock_run:
            mock_run.return_value = "Some other output"
            result = submit_job("job.sh", default_config, dry_run=False)
            assert result == "Some other output"

    def test_submission_handles_called_process_error(self, default_config):
        with patch("polite_submit.cli.run_cmd") as mock_run:
            mock_run.side_effect = subprocess.CalledProcessError(1, "sbatch")
            result = submit_job("job.sh", default_config, dry_run=False)
            assert result is None

    def test_submission_handles_timeout(self, default_config):
        with patch("polite_submit.cli.run_cmd") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired("sbatch", 30)
            result = submit_job("job.sh", default_config, dry_run=False)
            assert result is None


class TestSubmitSingle:
    def test_dry_run_succeeds(self, default_config):
        with patch("polite_submit.cli.echo_status"):
            result = submit_single("job.sh", default_config, dry_run=True)
            assert result is True

    def test_submit_on_clear_cluster(self, default_config, mock_cluster_state):
        with (
            patch("polite_submit.cli.probe") as mock_probe,
            patch("polite_submit.cli.submit_job") as mock_submit,
            patch("polite_submit.cli.echo_status"),
        ):
            mock_probe.return_value = mock_cluster_state
            mock_submit.return_value = "12345"

            result = submit_single("job.sh", default_config, dry_run=False)
            assert result is True
            mock_submit.assert_called_once()

    def test_backoff_when_congested(self, default_config):
        congested_state = ClusterState(
            partition="gpu",
            total_nodes=10,
            allocated_nodes=10,
            idle_nodes=0,
            my_running=5,
            my_pending=3,
            others_pending=50,
        )
        # Config with very low max_attempts to abort quickly
        config = Config(max_attempts=1, initial_backoff=0.01)

        with (
            patch("polite_submit.cli.probe") as mock_probe,
            patch("polite_submit.cli.echo_status"),
            patch("polite_submit.cli.BackoffController") as mock_backoff_cls,
        ):
            mock_probe.return_value = congested_state

            # Mock backoff controller to abort immediately
            mock_backoff = MagicMock()
            mock_backoff.should_abort = True
            mock_backoff.attempt = 1
            mock_backoff.total_wait = 0.01
            mock_backoff_cls.from_config.return_value = mock_backoff

            result = submit_single("job.sh", config, dry_run=False)
            assert result is False

    def test_retry_on_failed_submission(self, default_config, mock_cluster_state):
        config = Config(max_attempts=2, initial_backoff=0.01)

        with (
            patch("polite_submit.cli.probe") as mock_probe,
            patch("polite_submit.cli.submit_job") as mock_submit,
            patch("polite_submit.cli.echo_status"),
            patch("polite_submit.cli.BackoffController") as mock_backoff_cls,
        ):
            mock_probe.return_value = mock_cluster_state
            # First call fails, second succeeds
            mock_submit.side_effect = [None, "12345"]

            mock_backoff = MagicMock()
            mock_backoff.should_abort = False
            mock_backoff.attempt = 0
            mock_backoff.calculate_wait.return_value = 0.01
            mock_backoff.total_wait = 0
            mock_backoff_cls.from_config.return_value = mock_backoff

            # After first failure and backoff
            def update_should_abort(*args, **kwargs):
                mock_backoff.attempt += 1
                if mock_backoff.attempt >= 2:
                    mock_backoff.should_abort = True

            mock_backoff.wait.side_effect = update_should_abort

            submit_single("job.sh", config, dry_run=False)
            # Should succeed on second try
            assert mock_submit.call_count >= 1

    def test_backoff_decision_waits_then_retries(self):
        """Test that BACKOFF decision triggers wait and retry."""

        config = Config(max_attempts=3, initial_backoff=0.01)

        # Create states: first congested (BACKOFF), then clear (SUBMIT)
        congested_state = ClusterState(
            partition="gpu",
            total_nodes=10,
            allocated_nodes=10,
            idle_nodes=0,
            my_running=10,  # Over limit
            my_pending=0,
            others_pending=0,
        )
        clear_state = ClusterState(
            partition="gpu",
            total_nodes=10,
            allocated_nodes=5,
            idle_nodes=5,
            my_running=1,
            my_pending=0,
            others_pending=0,
        )

        call_count = [0]

        def probe_side_effect(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return congested_state
            return clear_state

        with (
            patch("polite_submit.cli.probe") as mock_probe,
            patch("polite_submit.cli.submit_job") as mock_submit,
            patch("polite_submit.cli.echo_status"),
            patch("polite_submit.backoff.time.sleep"),
        ):
            mock_probe.side_effect = probe_side_effect
            mock_submit.return_value = "12345"

            result = submit_single("job.sh", config, dry_run=False)

            assert result is True
            # Should have probed twice (once BACKOFF, once SUBMIT)
            assert mock_probe.call_count == 2
            mock_submit.assert_called_once()


class TestSubmitBatch:
    def test_batch_all_succeed(self, default_config):
        with (
            patch("polite_submit.cli.submit_single") as mock_single,
            patch("polite_submit.cli.echo_status"),
        ):
            mock_single.return_value = True
            result = submit_batch(
                ["job1.sh", "job2.sh", "job3.sh"], default_config, dry_run=True
            )
            assert result == 3
            assert mock_single.call_count == 3

    def test_batch_partial_success(self, default_config):
        with (
            patch("polite_submit.cli.submit_single") as mock_single,
            patch("polite_submit.cli.echo_status"),
        ):
            mock_single.side_effect = [True, False, True]
            result = submit_batch(
                ["job1.sh", "job2.sh", "job3.sh"], default_config, dry_run=True
            )
            assert result == 2

    def test_batch_all_fail(self, default_config):
        with (
            patch("polite_submit.cli.submit_single") as mock_single,
            patch("polite_submit.cli.echo_status"),
        ):
            mock_single.return_value = False
            result = submit_batch(["job1.sh", "job2.sh"], default_config, dry_run=True)
            assert result == 0


class TestSubmitArrayChunked:
    def test_array_chunking(self, default_config):
        with (
            patch("polite_submit.cli.submit_single") as mock_single,
            patch("polite_submit.cli.echo_status"),
        ):
            mock_single.return_value = True
            result = submit_array_chunked(
                "sweep.sh", "0-99", 10, default_config, dry_run=True
            )
            assert result == 10  # 100 tasks / 10 per chunk = 10 chunks
            assert mock_single.call_count == 10

    def test_array_single_value(self, default_config):
        with (
            patch("polite_submit.cli.submit_single") as mock_single,
            patch("polite_submit.cli.echo_status"),
        ):
            mock_single.return_value = True
            result = submit_array_chunked(
                "sweep.sh", "5", 10, default_config, dry_run=True
            )
            assert result == 1
            assert mock_single.call_count == 1

    def test_array_uneven_chunks(self, default_config):
        with (
            patch("polite_submit.cli.submit_single") as mock_single,
            patch("polite_submit.cli.echo_status"),
        ):
            mock_single.return_value = True
            # 0-24 = 25 tasks, chunk size 10 = 3 chunks (10, 10, 5)
            result = submit_array_chunked(
                "sweep.sh", "0-24", 10, default_config, dry_run=True
            )
            assert result == 3

    def test_array_partial_failure(self, default_config):
        with (
            patch("polite_submit.cli.submit_single") as mock_single,
            patch("polite_submit.cli.echo_status"),
        ):
            mock_single.side_effect = [True, False, True]
            result = submit_array_chunked(
                "sweep.sh", "0-29", 10, default_config, dry_run=True
            )
            assert result == 2


class TestMainCLI:
    def test_help(self, runner):
        result = runner.invoke(main, ["--help"])
        assert result.exit_code == 0
        assert "Submit jobs politely" in result.output

    def test_version(self, runner):
        result = runner.invoke(main, ["--version"])
        assert result.exit_code == 0

    def test_no_arguments_error(self, runner):
        result = runner.invoke(main, [])
        assert result.exit_code != 0
        assert "Please provide a script" in result.output

    def test_array_without_range_error(self, runner):
        with runner.isolated_filesystem():
            with open("sweep.sh", "w") as f:
                f.write("#!/bin/bash\necho test")
            result = runner.invoke(main, ["--array", "sweep.sh"])
            assert result.exit_code != 0
            assert "--range is required" in result.output

    def test_dry_run_single_job(self, runner):
        with runner.isolated_filesystem():
            with open("job.sh", "w") as f:
                f.write("#!/bin/bash\necho test")

            with patch("polite_submit.cli.submit_single") as mock_single:
                mock_single.return_value = True
                result = runner.invoke(main, ["--dry-run", "job.sh"])
                assert result.exit_code == 0
                mock_single.assert_called_once()

    def test_dry_run_batch_jobs(self, runner):
        with runner.isolated_filesystem():
            for name in ["job1.sh", "job2.sh"]:
                with open(name, "w") as f:
                    f.write("#!/bin/bash\necho test")

            with patch("polite_submit.cli.submit_batch") as mock_batch:
                mock_batch.return_value = 2
                result = runner.invoke(
                    main, ["--dry-run", "--batch", "job1.sh", "--batch", "job2.sh"]
                )
                assert result.exit_code == 0

    def test_dry_run_array_job(self, runner):
        with runner.isolated_filesystem():
            with open("sweep.sh", "w") as f:
                f.write("#!/bin/bash\necho test")

            with patch("polite_submit.cli.submit_array_chunked") as mock_array:
                mock_array.return_value = 10
                result = runner.invoke(
                    main, ["--dry-run", "--array", "sweep.sh", "--range", "0-99"]
                )
                assert result.exit_code == 0

    def test_partition_override(self, runner):
        with runner.isolated_filesystem():
            with open("job.sh", "w") as f:
                f.write("#!/bin/bash\necho test")

            with patch("polite_submit.cli.submit_single") as mock_single:
                mock_single.return_value = True
                result = runner.invoke(
                    main, ["--dry-run", "--partition", "cpu", "job.sh"]
                )
                assert result.exit_code == 0
                # Check that config was modified
                call_config = mock_single.call_args[0][1]
                assert call_config.partition == "cpu"

    def test_host_override(self, runner):
        with runner.isolated_filesystem():
            with open("job.sh", "w") as f:
                f.write("#!/bin/bash\necho test")

            with patch("polite_submit.cli.submit_single") as mock_single:
                mock_single.return_value = True
                result = runner.invoke(
                    main, ["--dry-run", "--host", "hpc-cluster", "job.sh"]
                )
                assert result.exit_code == 0
                call_config = mock_single.call_args[0][1]
                assert call_config.host == "hpc-cluster"

    def test_chunk_override(self, runner):
        with runner.isolated_filesystem():
            with open("sweep.sh", "w") as f:
                f.write("#!/bin/bash\necho test")

            with patch("polite_submit.cli.submit_array_chunked") as mock_array:
                mock_array.return_value = 5
                result = runner.invoke(
                    main,
                    [
                        "--dry-run",
                        "--array",
                        "sweep.sh",
                        "--range",
                        "0-99",
                        "--chunk",
                        "20",
                    ],
                )
                assert result.exit_code == 0
                # Chunk size should be passed
                assert mock_array.call_args[0][2] == 20

    def test_aggressive_mode(self, runner):
        with runner.isolated_filesystem():
            with open("job.sh", "w") as f:
                f.write("#!/bin/bash\necho test")

            with patch("polite_submit.cli.submit_single") as mock_single:
                mock_single.return_value = True
                result = runner.invoke(main, ["--dry-run", "--aggressive", "job.sh"])
                assert result.exit_code == 0
                # Check aggressive config applied
                call_config = mock_single.call_args[0][1]
                assert call_config.max_concurrent == 100

    def test_batch_with_script_arg(self, runner):
        with runner.isolated_filesystem():
            for name in ["job1.sh", "job2.sh", "job3.sh"]:
                with open(name, "w") as f:
                    f.write("#!/bin/bash\necho test")

            with patch("polite_submit.cli.submit_batch") as mock_batch:
                mock_batch.return_value = 3
                result = runner.invoke(
                    main,
                    [
                        "--dry-run",
                        "job1.sh",
                        "--batch",
                        "job2.sh",
                        "--batch",
                        "job3.sh",
                    ],
                )
                assert result.exit_code == 0
                # All three scripts should be in the list
                scripts = mock_batch.call_args[0][0]
                assert len(scripts) == 3

    def test_failed_submission_exit_code(self, runner):
        with runner.isolated_filesystem():
            with open("job.sh", "w") as f:
                f.write("#!/bin/bash\necho test")

            with patch("polite_submit.cli.submit_single") as mock_single:
                mock_single.return_value = False
                result = runner.invoke(main, ["--dry-run", "job.sh"])
                assert result.exit_code == 1

    def test_config_file_option(self, runner):
        with runner.isolated_filesystem():
            with open("job.sh", "w") as f:
                f.write("#!/bin/bash\necho test")

            with open("custom-config.yaml", "w") as f:
                f.write("cluster:\n  partition: custom-partition\n")

            with patch("polite_submit.cli.submit_single") as mock_single:
                mock_single.return_value = True
                result = runner.invoke(
                    main, ["--dry-run", "--config", "custom-config.yaml", "job.sh"]
                )
                assert result.exit_code == 0
                call_config = mock_single.call_args[0][1]
                assert call_config.partition == "custom-partition"
