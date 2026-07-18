"""
Kubernetes execution backend scaffold.

CHANGE (interface audit): like local.py/slurm.py, this only overrode
submit(), so ExecutionBackend's ABC would refuse to instantiate it
(missing status/cancel/fetch_results). Added stubs for all four.

NOT YET WIRED INTO backend_config.py — and deliberately left that way,
rather than guessing: there's no `KubernetesBackendConfig` class, and
`BackendConfig`'s discriminated union / `create_backend()` don't mention
"kubernetes" at all, so it's unreachable from the API today regardless of
this file's contents. Wiring it in needs real fields from you first
(namespace, image, kubeconfig/in-cluster auth, resource requests/limits,
how nuclear_data_path gets mounted into the pod — e.g. hostPath vs. a
PersistentVolumeClaim) — happy to draft KubernetesBackendConfig once you
know what your cluster setup looks like.

TODO (once wired in):
    - submit(): build a batch/v1 Job manifest (via the `kubernetes`
      Python client) that mounts nuclear_data_path read-only and runs
      the same openmc_bin invocation DockerBackend builds for `podman
      run`/`docker run`, then create it via the Kubernetes API.
    - status(): poll the Job's pod status via the API.
    - cancel(): delete the Job.
    - fetch_results(): copy output files out of the pod (kubectl cp
      equivalent via the API, or a shared PVC) to job.output_dir().
"""
from __future__ import annotations

from pathlib import Path

from ..domain.job import JobStatus, SimulationJob
from .base import ExecutionBackend


class KubernetesBackend(ExecutionBackend):
    name = "kubernetes"

    def __init__(self, config: object):
        self._config = config

    @classmethod
    def from_config(cls, config: object) -> "KubernetesBackend":
        return cls(config)

    def submit(self, job: SimulationJob) -> SimulationJob:
        raise NotImplementedError("Kubernetes execution is not wired up in the scaffold yet.")

    def status(self, job: SimulationJob) -> JobStatus:
        raise NotImplementedError("Kubernetes execution is not wired up in the scaffold yet.")

    def cancel(self, job: SimulationJob) -> SimulationJob:
        raise NotImplementedError("Kubernetes execution is not wired up in the scaffold yet.")

    def fetch_results(self, job: SimulationJob) -> list[Path]:
        raise NotImplementedError("Kubernetes execution is not wired up in the scaffold yet.")