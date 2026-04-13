"""Tests for the Kubernetes submission backend."""

from __future__ import annotations

import subprocess
from unittest.mock import patch

from polite_submit.config import Config
from polite_submit.submit_k8s import _parse_apply_output, submit_job_k8s


def test_parse_apply_output_prefers_job() -> None:
    out = (
        "configmap/foo created\n"
        "job.batch/my-training-42 created\n"
        "service/foo unchanged\n"
    )
    assert _parse_apply_output(out) == "my-training-42"


def test_parse_apply_output_prefers_deployment_when_no_job() -> None:
    out = "deployment.apps/burst-worker configured\n"
    assert _parse_apply_output(out) == "burst-worker"


def test_parse_apply_output_falls_back_to_first_resource() -> None:
    out = "configmap/foo created\nsecret/bar unchanged\n"
    assert _parse_apply_output(out) == "foo"


def test_parse_apply_output_empty() -> None:
    assert _parse_apply_output("") is None


def test_submit_job_k8s_happy_path() -> None:
    cfg = Config(backend="k8s", namespace="mine")
    fake_output = "job.batch/trainer-1 created"
    with patch("polite_submit.submit_k8s.run_cmd", return_value=fake_output) as mock:
        name = submit_job_k8s("path/to/job.yaml", cfg)
    assert name == "trainer-1"
    called_cmd = mock.call_args.args[0]
    assert "kubectl apply" in called_cmd
    assert "-f path/to/job.yaml" in called_cmd
    assert "-n mine" in called_cmd


def test_submit_job_k8s_dry_run() -> None:
    cfg = Config(backend="k8s", namespace="mine")
    fake_output = "job.batch/trainer-1 created (dry run)"
    with patch("polite_submit.submit_k8s.run_cmd", return_value=fake_output) as mock:
        name = submit_job_k8s("path/to/job.yaml", cfg, dry_run=True)
    assert name == "trainer-1"
    called_cmd = mock.call_args.args[0]
    assert "--dry-run=client" in called_cmd


def test_submit_job_k8s_failure_returns_none() -> None:
    cfg = Config(backend="k8s", namespace="mine")

    def boom(*_a, **_kw):
        raise subprocess.CalledProcessError(1, "kubectl", "", "err")

    with patch("polite_submit.submit_k8s.run_cmd", side_effect=boom):
        assert submit_job_k8s("bad.yaml", cfg) is None


def test_submit_job_k8s_timeout_returns_none() -> None:
    cfg = Config(backend="k8s", namespace="mine")
    with patch(
        "polite_submit.submit_k8s.run_cmd",
        side_effect=subprocess.TimeoutExpired("kubectl", 30),
    ):
        assert submit_job_k8s("bad.yaml", cfg) is None


def test_submit_job_k8s_default_namespace_when_none() -> None:
    cfg = Config(backend="k8s", namespace=None)
    fake_output = "job.batch/trainer-1 created"
    with patch("polite_submit.submit_k8s.run_cmd", return_value=fake_output) as mock:
        submit_job_k8s("job.yaml", cfg)
    called_cmd = mock.call_args.args[0]
    assert "-n default" in called_cmd


def test_submit_job_k8s_extra_args() -> None:
    cfg = Config(backend="k8s", namespace="mine")
    fake_output = "job.batch/trainer-1 created"
    with patch("polite_submit.submit_k8s.run_cmd", return_value=fake_output) as mock:
        submit_job_k8s("job.yaml", cfg, extra_args=["--wait=false"])
    called_cmd = mock.call_args.args[0]
    assert "--wait=false" in called_cmd
