"""Jobs routes — submit, monitor, and cancel simulation jobs."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException

from ..adapters.openmc_adapter import OpenMCRunSettings
from ..domain.job import JobStatus, SimulationJob
from ..dsl import loader, expander
from ..dsl.sweep import expand_sweep
from ..execution.docker_backend import DockerBackend
from .schemas import (
    DeletedResponse,
    JobDetail,
    JobSubmitRequest,
    JobSummary,
    SweepResponse,
    SweepSubmitRequest,
)

router = APIRouter(prefix="/jobs", tags=["jobs"])

# In-memory job store — replace with repository when DB is wired up.
_jobs: dict[str, SimulationJob] = {}

# Sweep index: sweep_id -> list of job IDs
_sweeps: dict[str, list[str]] = {}

# Single backend instance — replace with backend registry when config is wired up
_backend = DockerBackend()

JOBS_BASE_DIR = Path.home() / ".cascade" / "jobs"


def _to_summary(job: SimulationJob) -> JobSummary:
    return JobSummary(
        id=job.id,
        status=job.status.value,
        backend=job.backend,
        param_values=job.param_values,
        created_at=job.created_at,
        notes=job.notes,
    )


def _to_detail(job: SimulationJob) -> JobDetail:
    return JobDetail(
        id=job.id,
        status=job.status.value,
        backend=job.backend,
        param_values=job.param_values,
        created_at=job.created_at,
        notes=job.notes,
        geometry_id=job.geometry.id,
        started_at=job.started_at,
        finished_at=job.finished_at,
        error=job.error,
        working_dir=str(job.working_dir) if job.working_dir else None,
    )


def _make_settings(body: JobSubmitRequest | SweepSubmitRequest) -> OpenMCRunSettings:
    return OpenMCRunSettings(
        particles=body.particles,
        inactive=body.inactive,
        batches=body.batches,
        seed=body.seed,
    )


def _resolve_materials(material_ids: list[str]):
    from .materials import get_materials_by_ids
    return get_materials_by_ids(material_ids)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post("/submit", response_model=JobSummary, status_code=202)
async def submit_job(body: JobSubmitRequest) -> JobSummary:
    """Submit a single simulation job.

    Validates and expands the geometry, resolves materials from the
    library, stages input files, and starts the container.

    Returns immediately with status=queued. Poll GET /jobs/{id} for updates.
    """
    errors = loader.validate(body.geometry_text)
    if errors:
        raise HTTPException(status_code=422, detail=errors)

    schemas = loader.load(body.geometry_text)

    try:
        geometry = expander.expand(schemas)
    except Exception as e:
        raise HTTPException(status_code=422, detail=str(e))

    materials = _resolve_materials(body.material_ids)

    job_id = str(uuid.uuid4())
    job = SimulationJob(
        id=job_id,
        geometry=geometry,
        materials=materials,
        param_values={},
        backend=body.backend,
        working_dir=JOBS_BASE_DIR / job_id,
        notes=body.notes,
    )

    try:
        job = _backend.submit(job)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Submission failed: {e}")

    _jobs[job_id] = job
    return _to_summary(job)


@router.post("/sweep", response_model=SweepResponse, status_code=202)
async def submit_sweep(body: SweepSubmitRequest) -> SweepResponse:
    """Submit a parametric sweep — one job per parameter combination.

    Detects sweep(...) expressions in the geometry YAML, expands the
    cartesian product, and submits one job per combination.

    Returns a sweep_id and list of job summaries. Use GET /results/sweep/{sweep_id}
    to collect results after all jobs complete.
    """
    errors = loader.validate(body.geometry_text)
    if errors:
        raise HTTPException(status_code=422, detail=errors)

    try:
        sweep_points = expand_sweep(body.geometry_text)
    except Exception as e:
        raise HTTPException(status_code=422, detail=str(e))

    materials = _resolve_materials(body.material_ids)
    sweep_id = str(uuid.uuid4())
    job_ids: list[str] = []
    summaries: list[JobSummary] = []

    for param_values, geometry in sweep_points:
        job_id = str(uuid.uuid4())
        job = SimulationJob(
            id=job_id,
            geometry=geometry,
            materials=materials,
            param_values=param_values,
            backend=body.backend,
            working_dir=JOBS_BASE_DIR / job_id,
            notes=body.notes,
        )

        try:
            job = _backend.submit(job)
        except Exception as e:
            job.status = JobStatus.FAILED
            job.error  = str(e)

        _jobs[job_id] = job
        job_ids.append(job_id)
        summaries.append(_to_summary(job))

    _sweeps[sweep_id] = job_ids

    return SweepResponse(sweep_id=sweep_id, jobs=summaries, total=len(summaries))


@router.get("/", response_model=list[JobSummary])
async def list_jobs() -> list[JobSummary]:
    """List all jobs, most recent first."""
    jobs = sorted(_jobs.values(), key=lambda j: j.created_at, reverse=True)
    return [_to_summary(j) for j in jobs]


@router.get("/{job_id}", response_model=JobDetail)
async def get_job(job_id: str) -> JobDetail:
    """Get current status and detail for a job.

    Polls the backend for live status if the job is still running,
    then returns the updated job record.
    """
    job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found.")

    # Refresh status from backend if still running
    if job.status == JobStatus.RUNNING:
        try:
            job.status = _backend.status(job)
            _jobs[job_id] = job
        except Exception:
            pass  # stale status is better than a 500

    return _to_detail(job)


@router.post("/{job_id}/cancel", response_model=JobSummary)
async def cancel_job(job_id: str) -> JobSummary:
    """Cancel a queued or running job."""
    job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found.")

    if job.status in (JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED):
        raise HTTPException(
            status_code=409,
            detail=f"Job is already {job.status.value} and cannot be cancelled.",
        )

    try:
        job = _backend.cancel(job)
        _jobs[job_id] = job
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Cancel failed: {e}")

    return _to_summary(job)


@router.delete("/{job_id}", response_model=DeletedResponse)
async def delete_job(job_id: str) -> DeletedResponse:
    """Delete a job record and its working directory.

    Only completed, failed, or cancelled jobs can be deleted.
    Running jobs must be cancelled first.
    """
    job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found.")

    if job.status == JobStatus.RUNNING:
        raise HTTPException(
            status_code=409,
            detail="Cannot delete a running job. Cancel it first.",
        )

    import shutil
    if job.working_dir and job.working_dir.exists():
        shutil.rmtree(job.working_dir, ignore_errors=True)

    del _jobs[job_id]
    return DeletedResponse(id=job_id)