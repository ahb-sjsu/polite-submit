"""
Command-line interface for polite-submit.

Provides a Click-based CLI for polite job submission to Slurm clusters.
"""

from __future__ import annotations

import subprocess
import sys
from typing import Optional

import click

from polite_submit.backoff import BackoffController, format_duration
from polite_submit.config import Config, get_effective_username, load_config
from polite_submit.decider import Decision, decide
from polite_submit.prober import probe, run_cmd  # probe kept at module scope for test patching
from polite_submit.prober_k8s import probe as probe_k8s_fn
from polite_submit.submit_k8s import submit_job_k8s


def echo_status(message: str, level: str = "info") -> None:
    """Print a status message with appropriate styling."""
    prefix = {
        "info": click.style("[*]", fg="blue"),
        "success": click.style("[+]", fg="green"),
        "warning": click.style("[!]", fg="yellow"),
        "error": click.style("[-]", fg="red"),
        "wait": click.style("[~]", fg="cyan"),
    }.get(level, "[*]")
    click.echo(f"{prefix} {message}")


def submit_job(
    script: str,
    config: Config,
    dry_run: bool = False,
    extra_args: Optional[list[str]] = None,
) -> Optional[str]:
    """
    Submit a single job via the configured backend (``sbatch`` for Slurm,
    ``kubectl apply`` for Kubernetes).

    Args:
        script: Path to job script (Slurm) or manifest YAML (K8s)
        config: Configuration
        dry_run: If True, don't actually submit
        extra_args: Additional backend-specific arguments

    Returns:
        Job ID / name if successful, None otherwise
    """
    if config.backend == "k8s":
        return submit_job_k8s(script, config, dry_run=dry_run, extra_args=extra_args)

    # Default: Slurm
    cmd_parts = ["sbatch"]
    if extra_args:
        cmd_parts.extend(extra_args)
    cmd_parts.append(script)
    cmd = " ".join(cmd_parts)

    if dry_run:
        echo_status(f"Would run: {cmd}", "info")
        return "DRY-RUN-12345"

    try:
        output = run_cmd(cmd, host=config.host)
        # Parse job ID from "Submitted batch job 12345"
        if "Submitted batch job" in output:
            job_id = output.split()[-1]
            return job_id
        return output
    except subprocess.CalledProcessError as e:
        echo_status(f"sbatch failed: {e}", "error")
        return None
    except subprocess.TimeoutExpired:
        echo_status("sbatch timed out", "error")
        return None


def probe_cluster(config: Config):
    """Dispatch to the right prober based on ``config.backend``.

    Uses module-level ``probe`` and ``probe_k8s_fn`` names directly so
    that tests patching ``polite_submit.cli.probe`` continue to work.
    """
    if config.backend == "k8s":
        ns = config.namespace or "default"
        return probe_k8s_fn(namespace=ns, host=config.host)
    return probe(
        partition=config.partition,
        username=get_effective_username(config),
        host=config.host,
    )


def submit_single(
    script: str,
    config: Config,
    dry_run: bool = False,
    extra_args: Optional[list[str]] = None,
) -> bool:
    """
    Submit a single job with polite backoff.

    Args:
        script: Path to job script
        config: Configuration
        dry_run: If True, don't actually submit
        extra_args: Additional sbatch arguments

    Returns:
        True if job was submitted successfully
    """
    backoff = BackoffController.from_config(config)
    username = get_effective_username(config)

    _ = username  # kept for backwards compat; actual identity handled by prober
    while not backoff.should_abort:
        # Probe cluster state
        if dry_run:
            from polite_submit.prober import probe_mock

            state = probe_mock(utilization=0.5)
        else:
            state = probe_cluster(config)

        # Make decision
        decision, reason = decide(state, config)

        if decision == Decision.SUBMIT:
            echo_status(f"Submitting {script}", "success")
            job_id = submit_job(script, config, dry_run, extra_args)
            if job_id:
                echo_status(f"Job submitted: {job_id}", "success")
                return True
            else:
                echo_status("Submission failed, will retry", "warning")

        elif decision == Decision.BACKOFF:
            wait_time = backoff.calculate_wait()
            echo_status(
                f"Backing off: {reason} "
                f"(wait {format_duration(wait_time)}, attempt {backoff.attempt + 1})",
                "wait",
            )
            backoff.wait()

    echo_status(
        f"Aborting after {backoff.attempt} attempts "
        f"(total wait: {format_duration(backoff.total_wait)})",
        "error",
    )
    return False


def submit_batch(
    scripts: list[str],
    config: Config,
    dry_run: bool = False,
) -> int:
    """
    Submit multiple job scripts with polite backoff between each.

    Args:
        scripts: List of script paths
        config: Configuration
        dry_run: If True, don't actually submit

    Returns:
        Number of successfully submitted jobs
    """
    submitted = 0
    total = len(scripts)

    for i, script in enumerate(scripts, 1):
        echo_status(f"Processing job {i}/{total}: {script}", "info")
        if submit_single(script, config, dry_run):
            submitted += 1

    echo_status(
        f"Submitted {submitted}/{total} jobs",
        "success" if submitted == total else "warning",
    )
    return submitted


def submit_array_chunked(
    script: str,
    array_range: str,
    chunk_size: int,
    config: Config,
    dry_run: bool = False,
) -> int:
    """
    Submit an array job in polite chunks.

    Args:
        script: Path to job script
        array_range: Array range (e.g., "0-99")
        chunk_size: Number of array tasks per submission
        config: Configuration
        dry_run: If True, don't actually submit

    Returns:
        Number of successfully submitted chunks
    """
    # Parse range
    if "-" in array_range:
        start, end = map(int, array_range.split("-"))
    else:
        start = end = int(array_range)

    chunks = []
    current = start
    while current <= end:
        chunk_end = min(current + chunk_size - 1, end)
        chunks.append(f"{current}-{chunk_end}")
        current = chunk_end + 1

    echo_status(
        f"Splitting array {array_range} into {len(chunks)} chunks of ~{chunk_size}",
        "info",
    )

    submitted = 0
    for i, chunk_range in enumerate(chunks, 1):
        echo_status(f"Chunk {i}/{len(chunks)}: --array={chunk_range}", "info")
        extra_args = [f"--array={chunk_range}"]
        if submit_single(script, config, dry_run, extra_args):
            submitted += 1

    echo_status(
        f"Submitted {submitted}/{len(chunks)} array chunks",
        "success" if submitted == len(chunks) else "warning",
    )
    return submitted


@click.command()
@click.argument("script", required=False)
@click.option(
    "--batch",
    "-b",
    multiple=True,
    type=click.Path(exists=True),
    help="Submit multiple scripts (can be repeated)",
)
@click.option(
    "--array",
    "-a",
    type=click.Path(exists=True),
    help="Submit as array job",
)
@click.option(
    "--range",
    "array_range",
    help="Array range (e.g., 0-99). Required with --array",
)
@click.option(
    "--chunk",
    default=None,
    type=int,
    help="Chunk size for array jobs (default: from config)",
)
@click.option(
    "--aggressive",
    is_flag=True,
    help="Skip politeness checks (use sparingly!)",
)
@click.option(
    "--dry-run",
    "-n",
    is_flag=True,
    help="Show what would happen without submitting",
)
@click.option(
    "--config",
    "-c",
    "config_path",
    type=click.Path(exists=True),
    help="Path to config file",
)
@click.option(
    "--partition",
    "-p",
    help="Override partition (default: from config)",
)
@click.option(
    "--host",
    "-H",
    help="SSH host for remote cluster",
)
@click.option(
    "--backend",
    type=click.Choice(["slurm", "k8s"], case_sensitive=False),
    help="Submission backend (default: from config, or auto-detected from file ext)",
)
@click.option(
    "--namespace",
    "-N",
    help="Kubernetes namespace (K8s backend only)",
)
@click.version_option()
def main(
    script: Optional[str],
    batch: tuple[str, ...],
    array: Optional[str],
    array_range: Optional[str],
    chunk: Optional[int],
    aggressive: bool,
    dry_run: bool,
    config_path: Optional[str],
    partition: Optional[str],
    host: Optional[str],
    backend: Optional[str],
    namespace: Optional[str],
) -> None:
    """
    Submit jobs politely with CSMA/CA-inspired contention backoff.

    Probes cluster state before submission and backs off when congested,
    improving queue health for all users.

    \b
    Examples:
        polite-submit job.sh
        polite-submit --batch *.sh
        polite-submit --array sweep.sh --range 0-99 --chunk 10
        polite-submit --dry-run job.sh
        polite-submit --aggressive job.sh
    """
    # Load config
    config = load_config(config_path)

    # Apply CLI overrides
    if partition:
        config.partition = partition
    if host:
        config.host = host
    if chunk:
        config.array_chunk_size = chunk
    if backend:
        config.backend = backend.lower()
    if namespace:
        config.namespace = namespace

    # Auto-detect K8s from YAML manifest extension if backend not set
    def _looks_like_k8s(path: Optional[str]) -> bool:
        return bool(path and path.lower().endswith((".yaml", ".yml")))

    if not backend and config.backend == "slurm":
        if (
            _looks_like_k8s(script)
            or any(_looks_like_k8s(b) for b in batch)
            or _looks_like_k8s(array)
        ):
            config.backend = "k8s"
            echo_status(
                "Auto-detected Kubernetes backend from manifest extension",
                "info",
            )

    if aggressive:
        config = config.aggressive_mode()

    # Guard: K8s needs a namespace
    if config.backend == "k8s" and not config.namespace:
        raise click.UsageError(
            "Kubernetes backend requires a namespace. "
            "Set 'cluster.namespace' in config or pass --namespace."
        )

    # Guard: K8s backend does not support --array (no Slurm array semantics).
    if config.backend == "k8s" and array:
        raise click.UsageError(
            "--array is a Slurm concept. "
            "For K8s, use Job parallelism or submit a batch of manifests."
        )

    # Validate arguments
    if array and not array_range:
        raise click.UsageError("--range is required when using --array")

    if not script and not batch and not array:
        raise click.UsageError(
            "Please provide a script, --batch scripts, or --array with --range"
        )

    # Execute
    if dry_run:
        echo_status("DRY RUN MODE - no jobs will be submitted", "warning")

    if array:
        chunk_size = config.array_chunk_size
        submit_array_chunked(array, array_range, chunk_size, config, dry_run)
    elif batch:
        scripts = list(batch)
        if script:
            scripts.insert(0, script)
        submit_batch(scripts, config, dry_run)
    else:
        success = submit_single(script, config, dry_run)
        sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
