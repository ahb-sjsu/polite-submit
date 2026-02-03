"""
polite-submit: Client-side contention management for Slurm HPC clusters.

A CSMA/CA-inspired job submission wrapper that probes cluster state before
submission and backs off when resources are congested.
"""

__version__ = "0.1.1"

from polite_submit.prober import ClusterState, probe
from polite_submit.decider import Decision, decide
from polite_submit.backoff import BackoffController
from polite_submit.config import Config, load_config

__all__ = [
    "ClusterState",
    "probe",
    "Decision",
    "decide",
    "BackoffController",
    "Config",
    "load_config",
]
