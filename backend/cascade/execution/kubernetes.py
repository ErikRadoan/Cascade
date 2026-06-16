"""
Kubernetes execution backend scaffold.

    TODO:
        - implement submit() to create a Kubernetes Job and start it
        - implement status() to poll the Kubernetes Job status
        - implement cancel() to delete the Kubernetes Job
        - implement fetch_results() to copy output files from the pod to local storage

"""
from __future__ import annotations
from ..domain.job import SimulationJob
from .base import ExecutionBackend
class KubernetesBackend(ExecutionBackend):
    name = "kubernetes"
    def submit(self, job: SimulationJob) -> dict[str, object]:
        raise NotImplementedError("Kubernetes execution is not wired up in the scaffold yet.")

