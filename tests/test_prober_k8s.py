"""Tests for the Kubernetes prober backend."""

from __future__ import annotations

import json
import subprocess
from unittest.mock import patch

from polite_submit.prober import ClusterState
from polite_submit.prober_k8s import (
    _kubectl,
    parse_jobs,
    parse_nodes,
    parse_pending_pods,
    parse_top_nodes,
    probe,
)

# ─── parse_nodes ─────────────────────────────────────────────────


def test_parse_nodes_empty() -> None:
    assert parse_nodes("") == (0, 0, 0)


def test_parse_nodes_invalid_json() -> None:
    assert parse_nodes("not json") == (0, 0, 0)


def test_parse_nodes_counts_ready_and_skips_unschedulable() -> None:
    data = {
        "items": [
            {
                "spec": {},
                "status": {"conditions": [{"type": "Ready", "status": "True"}]},
            },
            {
                "spec": {},
                "status": {"conditions": [{"type": "Ready", "status": "True"}]},
            },
            {
                "spec": {},
                "status": {"conditions": [{"type": "Ready", "status": "False"}]},
            },
            {
                "spec": {"unschedulable": True},
                "status": {"conditions": [{"type": "Ready", "status": "True"}]},
            },
        ]
    }
    total, allocated_approx, idle = parse_nodes(json.dumps(data))
    assert total == 3  # unschedulable is excluded
    assert idle == 2  # the two Ready nodes
    assert allocated_approx == 1  # the one Not-Ready node


# ─── parse_top_nodes ─────────────────────────────────────────────


def test_parse_top_nodes_empty() -> None:
    assert parse_top_nodes("") == (0, 0)


def test_parse_top_nodes_single_row_no_header() -> None:
    # Only a header, no data rows
    assert parse_top_nodes("NAME   CPU(cores)   CPU%   MEM(bytes)   MEM%") == (0, 0)


def test_parse_top_nodes_busy_threshold() -> None:
    out = (
        "NAME       CPU(cores)   CPU%   MEM(bytes)   MEM%\n"
        "node-01    12000m       80%    64Gi          55%\n"
        "node-02    500m         3%     12Gi          11%\n"
        "node-03    3000m        25%    32Gi          40%\n"
    )
    # busy_cpu_pct default = 20 → node-01 (80%) and node-03 (25%) are busy
    allocated, total = parse_top_nodes(out)
    assert total == 3
    assert allocated == 2


def test_parse_top_nodes_ignores_malformed_rows() -> None:
    out = (
        "NAME   CPU(cores)   CPU%   MEM(bytes)   MEM%\n"
        "good   1000m        10%    1Gi          1%\n"
        "weird  notanumber   bogus  1Gi          1%\n"
    )
    allocated, total = parse_top_nodes(out, busy_cpu_pct=5.0)
    assert total == 1  # the "weird" row is skipped
    assert allocated == 1  # "good" is 10% > 5%


# ─── parse_jobs ──────────────────────────────────────────────────


def test_parse_jobs_empty() -> None:
    assert parse_jobs("") == (0, 0)


def test_parse_jobs_counts_active_and_pending() -> None:
    data = {
        "items": [
            {"status": {"active": 1}, "spec": {"completions": 1}},
            {"status": {"active": 2}, "spec": {"completions": 3}},
            {"status": {"succeeded": 0, "failed": 0}, "spec": {"completions": 1}},
            {"status": {"succeeded": 1}, "spec": {"completions": 1}},
        ]
    }
    running, pending = parse_jobs(json.dumps(data))
    assert running == 2
    assert pending == 1  # the not-yet-started one


# ─── parse_pending_pods ──────────────────────────────────────────


def test_parse_pending_pods_counts_other_namespaces() -> None:
    data = {
        "items": [
            {"metadata": {"namespace": "mine"}},
            {"metadata": {"namespace": "mine"}},
            {"metadata": {"namespace": "alice"}},
            {"metadata": {"namespace": "bob"}},
            {"metadata": {"namespace": "bob"}},
        ]
    }
    assert parse_pending_pods(json.dumps(data), my_namespace="mine") == 3


def test_parse_pending_pods_empty() -> None:
    assert parse_pending_pods("", "x") == 0


# ─── _kubectl graceful fallback ──────────────────────────────────


def test_kubectl_returns_empty_on_failure() -> None:
    def boom(*_a, **_kw):
        raise subprocess.CalledProcessError(1, "kubectl", "", "nope")

    with patch("polite_submit.prober_k8s.run_cmd", side_effect=boom):
        assert _kubectl("get nodes -o json") == ""


def test_kubectl_returns_empty_on_missing_binary() -> None:
    with patch("polite_submit.prober_k8s.run_cmd", side_effect=FileNotFoundError()):
        assert _kubectl("get nodes") == ""


# ─── end-to-end probe() ──────────────────────────────────────────


def _fake_outputs(
    *, top: str = "", jobs_json: str = "", pending_json: str = "", nodes_json: str = ""
) -> dict[str, str]:
    pending_key = (
        "get pods --all-namespaces " "--field-selector=status.phase=Pending -o json"
    )
    return {
        "top nodes --no-headers": top,
        "get nodes -o json": nodes_json,
        pending_key: pending_json,
    }


def test_probe_populates_cluster_state() -> None:
    top = (
        "NAME       CPU(cores)   CPU%   MEM(bytes)   MEM%\n"
        "node-01    12000m       80%    64Gi          55%\n"
        "node-02    500m         3%     12Gi          11%\n"
    )
    jobs_json = json.dumps(
        {"items": [{"status": {"active": 1}, "spec": {"completions": 1}}]}
    )
    pending_json = json.dumps(
        {
            "items": [
                {"metadata": {"namespace": "mine"}},
                {"metadata": {"namespace": "alice"}},
                {"metadata": {"namespace": "bob"}},
            ]
        }
    )

    calls: list[str] = []

    def fake_kubectl(args: str, host=None, timeout=30) -> str:
        calls.append(args)
        if args == "top nodes --no-headers":
            return top
        if args == "get jobs -n mine -o json":
            return jobs_json
        if args.startswith("get pods"):
            return pending_json
        return ""

    with patch("polite_submit.prober_k8s._kubectl", side_effect=fake_kubectl):
        state = probe(namespace="mine")

    assert isinstance(state, ClusterState)
    assert state.partition == "mine"
    assert state.total_nodes == 2
    assert state.allocated_nodes == 1  # node-01 is busy
    assert state.idle_nodes == 1
    assert state.my_running == 1
    assert state.my_pending == 0
    assert state.others_pending == 2  # alice + bob
    assert 0.0 <= state.utilization <= 1.0


def test_probe_falls_back_to_get_nodes_when_top_unavailable() -> None:
    nodes_json = json.dumps(
        {
            "items": [
                {
                    "spec": {},
                    "status": {"conditions": [{"type": "Ready", "status": "True"}]},
                }
            ]
        }
    )

    def fake_kubectl(args: str, host=None, timeout=30) -> str:
        if args == "top nodes --no-headers":
            return ""  # metrics-server not reachable
        if args == "get nodes -o json":
            return nodes_json
        return ""

    with patch("polite_submit.prober_k8s._kubectl", side_effect=fake_kubectl):
        state = probe(namespace="x")

    assert state.total_nodes == 1


def test_probe_degrades_gracefully_when_all_fail() -> None:
    with patch("polite_submit.prober_k8s._kubectl", return_value=""):
        state = probe(namespace="mine")
    assert state.total_nodes == 0
    assert state.my_running == 0
    assert state.others_pending == 0
