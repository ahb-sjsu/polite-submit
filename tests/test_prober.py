"""Tests for the prober module."""

import subprocess
from unittest.mock import patch, MagicMock

import pytest

from polite_submit.prober import (
    ClusterState,
    parse_sinfo,
    parse_squeue_states,
    parse_squeue_users,
    probe,
    probe_mock,
    run_cmd,
)


class TestClusterState:
    def test_utilization_normal(self):
        state = ClusterState(
            partition="gpu",
            total_nodes=10,
            allocated_nodes=7,
            idle_nodes=3,
            my_running=2,
            my_pending=1,
            others_pending=5,
        )
        assert state.utilization == 0.7

    def test_utilization_empty_cluster(self):
        state = ClusterState(
            partition="gpu",
            total_nodes=0,
            allocated_nodes=0,
            idle_nodes=0,
            my_running=0,
            my_pending=0,
            others_pending=0,
        )
        assert state.utilization == 1.0  # Conservative: assume full

    def test_utilization_fully_allocated(self):
        state = ClusterState(
            partition="gpu",
            total_nodes=8,
            allocated_nodes=8,
            idle_nodes=0,
            my_running=4,
            my_pending=2,
            others_pending=10,
        )
        assert state.utilization == 1.0

    def test_str_representation(self):
        state = ClusterState(
            partition="gpu",
            total_nodes=10,
            allocated_nodes=8,
            idle_nodes=2,
            my_running=2,
            my_pending=1,
            others_pending=5,
        )
        s = str(state)
        assert "gpu" in s
        assert "80%" in s
        assert "2R" in s
        assert "1P" in s


class TestParseSinfo:
    def test_parse_normal_output(self):
        output = """4 alloc
2 idle
1 mix"""
        total, allocated, idle = parse_sinfo(output)
        assert total == 7
        assert allocated == 5  # alloc + mix
        assert idle == 2

    def test_parse_empty_output(self):
        output = ""
        total, allocated, idle = parse_sinfo(output)
        assert total == 0
        assert allocated == 0
        assert idle == 0

    def test_parse_all_idle(self):
        output = "8 idle"
        total, allocated, idle = parse_sinfo(output)
        assert total == 8
        assert allocated == 0
        assert idle == 8


class TestParseSqueueStates:
    def test_parse_mixed_states(self):
        output = """R
R
R
PD
PD"""
        running, pending = parse_squeue_states(output)
        assert running == 3
        assert pending == 2

    def test_parse_empty(self):
        output = ""
        running, pending = parse_squeue_states(output)
        assert running == 0
        assert pending == 0

    def test_parse_all_running(self):
        output = "R\nR\nR"
        running, pending = parse_squeue_states(output)
        assert running == 3
        assert pending == 0


class TestParseSqueueUsers:
    def test_count_others(self):
        output = """alice
alice
bob
charlie
alice"""
        others = parse_squeue_users(output, "alice")
        assert others == 2  # bob + charlie

    def test_no_others(self):
        output = """alice
alice"""
        others = parse_squeue_users(output, "alice")
        assert others == 0

    def test_empty_queue(self):
        output = ""
        others = parse_squeue_users(output, "alice")
        assert others == 0


class TestProbeMock:
    def test_mock_utilization(self):
        state = probe_mock(utilization=0.8)
        assert state.utilization == 0.8

    def test_mock_job_counts(self):
        state = probe_mock(my_running=3, my_pending=2, others_pending=10)
        assert state.my_running == 3
        assert state.my_pending == 2
        assert state.others_pending == 10

    def test_mock_partition(self):
        state = probe_mock(partition="cpu")
        assert state.partition == "cpu"


class TestRunCmd:
    def test_local_command(self):
        with patch("polite_submit.prober.subprocess.run") as mock_run:
            mock_result = MagicMock()
            mock_result.stdout = "output\n"
            mock_run.return_value = mock_result

            result = run_cmd("echo hello", host=None)
            assert result == "output"
            # Should split command for local execution
            mock_run.assert_called_once()
            call_args = mock_run.call_args
            assert call_args[0][0] == ["echo", "hello"]

    def test_ssh_command(self):
        with patch("polite_submit.prober.subprocess.run") as mock_run:
            mock_result = MagicMock()
            mock_result.stdout = "remote output\n"
            mock_run.return_value = mock_result

            result = run_cmd("sinfo", host="hpc-cluster")
            assert result == "remote output"
            # Should prepend ssh
            mock_run.assert_called_once()
            call_args = mock_run.call_args
            assert call_args[0][0] == ["ssh", "hpc-cluster", "sinfo"]

    def test_timeout(self):
        with patch("polite_submit.prober.subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired("cmd", 30)
            with pytest.raises(subprocess.TimeoutExpired):
                run_cmd("slow_command", timeout=30)

    def test_command_failure(self):
        with patch("polite_submit.prober.subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.CalledProcessError(1, "cmd")
            with pytest.raises(subprocess.CalledProcessError):
                run_cmd("failing_command")


class TestProbe:
    def test_probe_successful(self):
        with patch("polite_submit.prober.run_cmd") as mock_run:
            # Mock sinfo, squeue for my jobs, squeue for pending
            mock_run.side_effect = [
                "4 alloc\n2 idle\n1 mix",  # sinfo
                "R\nR\nPD",  # squeue mine
                "alice\nbob\nalice",  # squeue pending
            ]

            state = probe(partition="gpu", username="alice")

            assert state.partition == "gpu"
            assert state.total_nodes == 7
            assert state.allocated_nodes == 5
            assert state.idle_nodes == 2
            assert state.my_running == 2
            assert state.my_pending == 1
            assert state.others_pending == 1  # Only bob

    def test_probe_sinfo_failure(self):
        with patch("polite_submit.prober.run_cmd") as mock_run:
            mock_run.side_effect = [
                subprocess.CalledProcessError(1, "sinfo"),  # sinfo fails
                "R\nR",  # squeue mine succeeds
                "bob",  # squeue pending succeeds
            ]

            state = probe(partition="gpu", username="alice")

            # Should use defaults for failed sinfo
            assert state.total_nodes == 0
            assert state.allocated_nodes == 0
            assert state.idle_nodes == 0
            # Other values should work
            assert state.my_running == 2
            assert state.my_pending == 0

    def test_probe_squeue_mine_failure(self):
        with patch("polite_submit.prober.run_cmd") as mock_run:
            mock_run.side_effect = [
                "4 alloc\n2 idle",  # sinfo succeeds
                subprocess.TimeoutExpired("squeue", 30),  # squeue mine fails
                "bob\ncharlie",  # squeue pending succeeds
            ]

            state = probe(partition="gpu", username="alice")

            assert state.total_nodes == 6
            assert state.my_running == 0
            assert state.my_pending == 0
            assert state.others_pending == 2

    def test_probe_squeue_pending_failure(self):
        with patch("polite_submit.prober.run_cmd") as mock_run:
            mock_run.side_effect = [
                "4 alloc\n2 idle",  # sinfo succeeds
                "R\nPD",  # squeue mine succeeds
                subprocess.CalledProcessError(1, "squeue"),  # pending fails
            ]

            state = probe(partition="gpu", username="alice")

            assert state.my_running == 1
            assert state.my_pending == 1
            assert state.others_pending == 0

    def test_probe_default_username(self, monkeypatch):
        monkeypatch.setenv("USER", "defaultuser")

        with patch("polite_submit.prober.run_cmd") as mock_run:
            mock_run.side_effect = [
                "4 alloc",
                "R",
                "otheruser",
            ]

            state = probe(partition="gpu")
            # Should use USER env var
            assert state.others_pending == 1

    def test_probe_username_from_username_env(self, monkeypatch):
        monkeypatch.delenv("USER", raising=False)
        monkeypatch.setenv("USERNAME", "winuser")

        with patch("polite_submit.prober.run_cmd") as mock_run:
            mock_run.side_effect = [
                "4 alloc",
                "R",
                "otheruser",
            ]

            state = probe(partition="gpu")
            assert state.others_pending == 1
