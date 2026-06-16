"""In-memory job repository scaffold."""
from __future__ import annotations
from ..domain.job import SimulationJob
class JobRepository:
    def __init__(self) -> None:
        self._jobs: dict[str, SimulationJob] = {}
    def save(self, job: SimulationJob) -> SimulationJob:
        self._jobs[job.id] = job
        return job
    def get(self, job_id: str) -> SimulationJob | None:
        return self._jobs.get(job_id)
    def list(self) -> list[SimulationJob]:
        return list(self._jobs.values())

