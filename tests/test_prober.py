"""Tests for the prober module."""

from polite_submit.prober import (
    ClusterState,
    parse_sinfo,
    parse_squeue_states,
    parse_squeue_users,
    probe_mock,
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
