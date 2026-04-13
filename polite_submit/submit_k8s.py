"""
Kubernetes job submission for polite-submit.

Wraps ``kubectl apply`` with the same interface as the Slurm
``submit_job`` helper in ``cli.py``, so the polite-submission flow
(probe → decide → backoff → submit) stays identical regardless of
backend.
"""

from __future__ import annotations

import subprocess
from typing import Optional

from polite_submit.config import Config
from polite_submit.prober import run_cmd


def submit_job_k8s(
    manifest: str,
    config: Config,
    dry_run: bool = False,
    extra_args: Optional[list[str]] = None,
) -> Optional[str]:
    """
    Apply a Kubernetes Job manifest via ``kubectl apply``.

    Parameters
    ----------
    manifest:
        Path to a YAML file containing a Kubernetes Job (or Deployment)
        spec. A typical polite-submit user passes one Job per call.
    config:
        Standard polite-submit Config; reads ``config.namespace`` and
        ``config.host`` (SSH host, if running kubectl remotely).
    dry_run:
        When True, appends ``--dry-run=client`` and returns a sentinel
        job name.
    extra_args:
        Additional ``kubectl apply`` arguments (e.g. ``--wait=false``).

    Returns
    -------
    The created Job name (str) on success, or None on failure.
    """
    ns = config.namespace or "default"
    parts = ["kubectl", "apply", "-f", manifest, "-n", ns]
    if dry_run:
        parts.append("--dry-run=client")
    if extra_args:
        parts.extend(extra_args)
    cmd = " ".join(parts)

    if dry_run:
        # Still run client-side dry-run so user sees kubectl validation
        try:
            output = run_cmd(cmd, host=config.host)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            return "DRY-RUN-FAILED"
        return _parse_apply_output(output) or "DRY-RUN-OK"

    try:
        output = run_cmd(cmd, host=config.host)
    except subprocess.CalledProcessError:
        return None
    except subprocess.TimeoutExpired:
        return None

    return _parse_apply_output(output)


def _parse_apply_output(output: str) -> Optional[str]:
    """
    Extract the created resource name from ``kubectl apply`` output.

    Expected lines look like::

        job.batch/my-job created
        deployment.apps/my-dep configured

    Returns the short name (``my-job``) of the first Job- or Deployment-
    typed line, or the first line's name if no Job/Deployment is found.
    """
    first: Optional[str] = None
    for raw in output.splitlines():
        line = raw.strip()
        if not line or "/" not in line:
            continue
        resource, _, _rest = line.partition(" ")
        if "/" in resource:
            name = resource.split("/", 1)[1]
            if first is None:
                first = name
            if resource.startswith("job.") or resource.startswith("deployment."):
                return name
    return first
