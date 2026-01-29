"""
Cluster state probing for polite-submit.

Queries Slurm cluster state via sinfo and squeue commands, either locally
or over SSH.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class ClusterState:
    """Snapshot of cluster state at a point in time."""

    partition: str
    total_nodes: int
    allocated_nodes: int
    idle_nodes: int
    my_running: int
    my_pending: int
    others_pending: int
    timestamp: datetime = field(default_factory=datetime.now)

    @property
    def utilization(self) -> float:
        """Fraction of nodes currently allocated (0.0 to 1.0)."""
        if self.total_nodes == 0:
            return 1.0
        return self.allocated_nodes / self.total_nodes

    def __str__(self) -> str:
        return (
            f"ClusterState({self.partition}: "
            f"{self.utilization*100:.0f}% util, "
            f"my: {self.my_running}R/{self.my_pending}P, "
            f"others: {self.others_pending}P)"
        )


def run_cmd(
    cmd: str,
    host: Optional[str] = None,
    timeout: int = 30,
) -> str:
    """
    Run a command locally or via SSH.

    Args:
        cmd: The command to run
        host: SSH host alias (None for local execution)
        timeout: Command timeout in seconds

    Returns:
        Command stdout as string

    Raises:
        subprocess.TimeoutExpired: If command times out
        subprocess.CalledProcessError: If command fails
    """
    if host:
        full_cmd = ["ssh", host, cmd]
    else:
        full_cmd = cmd if isinstance(cmd, list) else cmd.split()

    result = subprocess.run(
        full_cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=True,
    )
    return result.stdout.strip()


def parse_sinfo(output: str) -> tuple[int, int, int]:
    """
    Parse sinfo output to get node counts.

    Expected format from: sinfo -h -p <partition> -o '%D %t'
    Example output:
        4 alloc
        2 idle
        1 mix

    Returns:
        (total_nodes, allocated_nodes, idle_nodes)
    """
    total = 0
    allocated = 0
    idle = 0

    for line in output.strip().split("\n"):
        if not line.strip():
            continue
        parts = line.split()
        if len(parts) >= 2:
            count = int(parts[0])
            state = parts[1].lower()
            total += count
            if state in ("alloc", "allocated", "mix", "mixed"):
                allocated += count
            elif state in ("idle",):
                idle += count

    return total, allocated, idle


def parse_squeue_states(output: str) -> tuple[int, int]:
    """
    Parse squeue output to count running and pending jobs.

    Expected format from: squeue -h -p <partition> -u <user> -o '%t'
    Example output:
        R
        R
        PD

    Returns:
        (running_count, pending_count)
    """
    running = 0
    pending = 0

    for line in output.strip().split("\n"):
        state = line.strip().upper()
        if state == "R":
            running += 1
        elif state == "PD":
            pending += 1

    return running, pending


def parse_squeue_users(output: str, my_username: str) -> int:
    """
    Parse squeue output to count pending jobs from other users.

    Expected format from: squeue -h -p <partition> -t PENDING -o '%u'

    Returns:
        Count of pending jobs from users other than my_username
    """
    others = 0
    for line in output.strip().split("\n"):
        username = line.strip()
        if username and username != my_username:
            others += 1
    return others


def probe(
    partition: str = "gpu",
    username: Optional[str] = None,
    host: Optional[str] = None,
) -> ClusterState:
    """
    Probe cluster state by querying Slurm.

    Args:
        partition: Slurm partition to query
        username: Username to identify own jobs (defaults to $USER)
        host: SSH host alias for remote cluster (None for local)

    Returns:
        ClusterState snapshot
    """
    if username is None:
        username = os.environ.get("USER", os.environ.get("USERNAME", "unknown"))

    # Get partition info
    sinfo_cmd = f"sinfo -h -p {partition} -o '%D %t'"
    try:
        sinfo_output = run_cmd(sinfo_cmd, host)
        total, allocated, idle = parse_sinfo(sinfo_output)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        # If we can't get sinfo, assume worst case
        total, allocated, idle = 0, 0, 0

    # Get my job states
    squeue_mine_cmd = f"squeue -h -p {partition} -u {username} -o '%t'"
    try:
        squeue_mine_output = run_cmd(squeue_mine_cmd, host)
        my_running, my_pending = parse_squeue_states(squeue_mine_output)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        my_running, my_pending = 0, 0

    # Get others' pending jobs
    squeue_pending_cmd = f"squeue -h -p {partition} -t PENDING -o '%u'"
    try:
        squeue_pending_output = run_cmd(squeue_pending_cmd, host)
        others_pending = parse_squeue_users(squeue_pending_output, username)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        others_pending = 0

    return ClusterState(
        partition=partition,
        total_nodes=total,
        allocated_nodes=allocated,
        idle_nodes=idle,
        my_running=my_running,
        my_pending=my_pending,
        others_pending=others_pending,
    )


def probe_mock(
    utilization: float = 0.5,
    my_running: int = 0,
    my_pending: int = 0,
    others_pending: int = 0,
    partition: str = "gpu",
) -> ClusterState:
    """
    Create a mock ClusterState for testing without a real cluster.

    Args:
        utilization: Simulated cluster utilization (0.0 to 1.0)
        my_running: Simulated running job count
        my_pending: Simulated pending job count
        others_pending: Simulated other users' pending jobs
        partition: Partition name

    Returns:
        Mock ClusterState
    """
    total_nodes = 10
    allocated_nodes = int(total_nodes * utilization)
    idle_nodes = total_nodes - allocated_nodes

    return ClusterState(
        partition=partition,
        total_nodes=total_nodes,
        allocated_nodes=allocated_nodes,
        idle_nodes=idle_nodes,
        my_running=my_running,
        my_pending=my_pending,
        others_pending=others_pending,
    )
