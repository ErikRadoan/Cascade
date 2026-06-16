"""Local execution backend scaffold.

    TODO:
        - implement the submit() function
        - implement the fetch_results() function

"""
from __future__ import annotations
from ..domain.job import JobStatus, SimulationJob
from .base import ExecutionBackend
class LocalExecutionBackend(ExecutionBackend):
    name = "local"
    def submit(self, job: SimulationJob) -> dict[str, object]:
        raise NotImplementedError("Local execution is not wired up in the scaffold yet.")

