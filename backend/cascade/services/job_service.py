"""Job service."""
from __future__ import annotations
from uuid import uuid4
from ..domain.job import JobStatus, SimulationJob
class JobService:
    def __init__(self) -> None:
        self._jobs: dict[str, SimulationJob] = {}
    def create_job(self, geometry_id: str, material_ids: list[str], backend: str = "local") -> SimulationJob:
        job = SimulationJob(
            id=uuid4().hex,
            geometry_id=geometry_id,
            material_ids=list(material_ids),
            backend=backend,
        )
        self._jobs[job.id] = job
        return job
    def update_status(self, job_id: str, status: JobStatus) -> SimulationJob:
        job = self._jobs[job_id]
        job.status = status
        return job
    def list(self) -> list[SimulationJob]:
        return list(self._jobs.values())
    def get(self, job_id: str) -> SimulationJob | None:
        return self._jobs.get(job_id)

