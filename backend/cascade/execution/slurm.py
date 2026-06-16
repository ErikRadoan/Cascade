"""Slurm execution backend scaffold.

    TODO:
        - implement the submit() function
        - implement the fetch_results() function
"""
from __future__ import annotations
from ..domain.job import SimulationJob
from .base import ExecutionBackend
class SlurmBackend(ExecutionBackend):
    name = "slurm"
    def submit(self, job: SimulationJob) -> dict[str, object]:
        raise NotImplementedError("Slurm execution is not wired up in the scaffold yet.")

