"""
Cluster state probing for Kubernetes backends (e.g., NRP Nautilus).

Queries cluster state via ``kubectl`` and maps results onto the same
``ClusterState`` dataclass that the Slurm prober uses, so the
scheduler-agnostic decider/backoff logic continues to work unchanged.

Design notes
------------

* Reads are opportunistic: if a given ``kubectl`` call fails (perms,
  timeout, missing API), we degrade gracefully rather than aborting.
* Namespace-scoped by default. Cluster-wide pod listing (needed for
  ``others_pending``) is attempted but may require cluster-reader
  RBAC. If denied, ``others_pending`` falls back to 0 — the decider
  then relies on self-limiting + utilization checks alone.
* Treats "allocated" nodes loosely as "nodes with at least one
  non-system pod scheduled" — good enough for a politeness signal,
  not a precise utilization metric. When ``kubectl top nodes`` is
  available, swap in real CPU utilization.
"""

from __future__ import annotations

import json
import subprocess
from typing import Optional

from polite_submit.prober import ClusterState, run_cmd


def _kubectl(
    args: str,
    host: Optional[str] = None,
    timeout: int = 30,
) -> str:
    """Run a ``kubectl`` subcommand and return stdout, or empty on error."""
    try:
        return run_cmd(f"kubectl {args}", host=host, timeout=timeout)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
        return ""


def parse_nodes(nodes_json: str) -> tuple[int, int, int]:
    """
    Parse ``kubectl get nodes -o json`` output.

    Returns (total, allocated_approx, idle).
    ``allocated_approx`` counts nodes that have at least one non-system
    pod scheduled — a coarse stand-in for cluster load.
    """
    if not nodes_json.strip():
        return 0, 0, 0
    try:
        data = json.loads(nodes_json)
    except json.JSONDecodeError:
        return 0, 0, 0

    items = data.get("items", [])
    total = 0
    ready = 0
    for node in items:
        conditions = node.get("status", {}).get("conditions", [])
        is_ready = any(
            c.get("type") == "Ready" and c.get("status") == "True"
            for c in conditions
        )
        # Skip nodes that are SchedulingDisabled or NotReady
        spec = node.get("spec", {})
        if spec.get("unschedulable"):
            continue
        total += 1
        if is_ready:
            ready += 1
    # Without per-node pod counts we can't distinguish allocated from
    # idle precisely; callers that want precision should use
    # ``kubectl top nodes`` via ``parse_top_nodes`` below.
    return total, total - ready, ready


def parse_top_nodes(top_output: str, busy_cpu_pct: float = 20.0) -> tuple[int, int]:
    """
    Parse ``kubectl top nodes`` output (CPU%% MEM%%).

    A node is considered "allocated" if its CPU%% is above ``busy_cpu_pct``.
    Returns (allocated, total).

    Example input:
        NAME          CPU(cores)   CPU%   MEMORY(bytes)   MEMORY%
        node-01       12000m       80%    64Gi            55%
        node-02       500m         3%     12Gi            11%
    """
    if not top_output.strip():
        return 0, 0
    lines = top_output.strip().split("\n")
    if len(lines) < 2:
        return 0, 0
    allocated = 0
    total = 0
    for line in lines[1:]:  # skip header
        parts = line.split()
        if len(parts) < 3:
            continue
        cpu_pct_str = parts[2].rstrip("%")
        try:
            cpu_pct = float(cpu_pct_str)
        except ValueError:
            continue
        total += 1
        if cpu_pct >= busy_cpu_pct:
            allocated += 1
    return allocated, total


def parse_jobs(jobs_json: str) -> tuple[int, int]:
    """
    Parse ``kubectl get jobs -n <ns> -o json`` to count my jobs.

    A job is "running" if it has any active pods; "pending" if it has
    declared parallelism but no active pods yet (start-up or queued).

    Returns (running, pending).
    """
    if not jobs_json.strip():
        return 0, 0
    try:
        data = json.loads(jobs_json)
    except json.JSONDecodeError:
        return 0, 0

    running = 0
    pending = 0
    for job in data.get("items", []):
        status = job.get("status", {})
        active = status.get("active", 0)
        succeeded = status.get("succeeded", 0)
        failed = status.get("failed", 0)
        spec = job.get("spec", {})
        completions = spec.get("completions", 1)
        if active > 0:
            running += 1
        elif succeeded + failed < completions:
            # Declared but not yet active — treat as pending
            pending += 1
    return running, pending


def parse_pending_pods(pods_json: str, my_namespace: str) -> int:
    """
    Parse ``kubectl get pods --all-namespaces --field-selector=status.phase=Pending``
    to count pending pods that are NOT ours.
    """
    if not pods_json.strip():
        return 0
    try:
        data = json.loads(pods_json)
    except json.JSONDecodeError:
        return 0
    others = 0
    for pod in data.get("items", []):
        ns = pod.get("metadata", {}).get("namespace", "")
        if ns != my_namespace:
            others += 1
    return others


def probe(
    namespace: str = "default",
    username: Optional[str] = None,
    host: Optional[str] = None,
) -> ClusterState:
    """
    Probe Kubernetes cluster state and return a ``ClusterState``.

    Parameters mirror the Slurm ``probe()`` signature; ``namespace``
    takes the role of ``partition`` in Slurm terminology.

    ``username`` is currently unused for K8s (identity comes from the
    kubeconfig / service account) but kept for signature compatibility
    with the Slurm prober.
    """
    _ = username  # currently unused; reserved for future label-based filters

    # ---- Node info (cluster-wide) --------------------------------
    top_out = _kubectl("top nodes --no-headers", host=host)
    if top_out:
        allocated, total = parse_top_nodes(top_out)
        idle = max(total - allocated, 0)
    else:
        # Fallback when metrics-server isn't reachable
        nodes_json = _kubectl("get nodes -o json", host=host)
        total, allocated, idle = parse_nodes(nodes_json)

    # ---- My jobs in namespace ------------------------------------
    jobs_json = _kubectl(f"get jobs -n {namespace} -o json", host=host)
    my_running, my_pending = parse_jobs(jobs_json)

    # ---- Global pending pressure (cluster-wide, may require RBAC)
    pending_json = _kubectl(
        "get pods --all-namespaces "
        "--field-selector=status.phase=Pending -o json",
        host=host,
    )
    others_pending = parse_pending_pods(pending_json, namespace)

    return ClusterState(
        partition=namespace,
        total_nodes=total,
        allocated_nodes=allocated,
        idle_nodes=idle,
        my_running=my_running,
        my_pending=my_pending,
        others_pending=others_pending,
    )
