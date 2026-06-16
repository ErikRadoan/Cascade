"""Job HTTP routes."""
from __future__ import annotations
from fastapi import APIRouter, Query
from ..domain.job import JobStatus
from ..services.job_service import JobService
router = APIRouter(prefix="/jobs", tags=["jobs"])
job_service = JobService()
@router.get("/")
def list_jobs() -> list[dict[str, object]]:
    return [job.to_dict() for job in job_service.list()]
@router.post("/submit")
def submit_job(
    geometry_id: str,
    material_ids: list[str] = Query(default=[]),
    backend: str = "local",
) -> dict[str, object]:
    job = job_service.create_job(geometry_id=geometry_id, material_ids=material_ids, backend=backend)
    return job.to_dict()
@router.post("/{job_id}/status/{status}")
def update_status(job_id: str, status: JobStatus) -> dict[str, object]:
    job = job_service.update_status(job_id, status)
    return job.to_dict()

