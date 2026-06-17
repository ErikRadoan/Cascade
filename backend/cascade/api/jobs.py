"""Jobs routes — submit, monitor, and cancel simulation jobs."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..adapters.openmc_adapter import OpenMCRunSettings
from ..domain.job import JobStatus, SimulationJob
from ..dsl import loader, expander
from ..dsl.sweep import expand_sweep
from ..execution.backend_config import (
    BackendConfig,
    DockerBackendConfig,
    create_backend,
)
from .schemas import (
    DeletedResponse,
    JobDetail,
    JobSummary,
    SweepResponse,
)

router = APIRouter(prefix="/jobs", tags=["jobs"])

# ---------------------------------------------------------------------------
# In-memory stores — replace with repositories when DB is wired up
# ---------------------------------------------------------------------------

_jobs: dict[str, SimulationJob] = {}

# Stores the raw backend config dict used to submit each job.
# Keyed by job_id. Used to reconstruct the correct backend for
# status polling and cancellation — a SLURM job must be polled
# via SlurmBackend, not DockerBackend.
_job_backend_configs: dict[str, dict] = {}

# Sweep index: sweep_id -> list of job IDs
_sweeps: dict[str, list[str]] = {}

JOBS_BASE_DIR = Path.home() / ".cascade" / "jobs"

# Default backend config used when the request omits backend_config.
_DEFAULT_BACKEND_CONFIG = DockerBackendConfig(
    cli="podman",
    image="cascade-openmc:latest",
    openmc_bin="/opt/miniconda/envs/openmc/bin/openmc",
    nuclear_data_path=str(Path.home() / ".cascade" / "data"),
    nuclear_data_container_path="/nuclear-data",
    jobs_base_dir=str(JOBS_BASE_DIR),
)


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class JobSubmitRequest(BaseModel):
    geometry_text:  str
    material_ids:   list[str]
    backend_config: BackendConfig = Field(default=_DEFAULT_BACKEND_CONFIG)
    particles: int  = Field(1000, gt=0)
    inactive:  int  = Field(20,   gt=0)
    batches:   int  = Field(100,  gt=0)
    seed:      int  = 1
    notes:     str | None = None


class SweepSubmitRequest(BaseModel):
    geometry_text:  str
    material_ids:   list[str]
    backend_config: BackendConfig = Field(default=_DEFAULT_BACKEND_CONFIG)
    particles: int  = Field(1000, gt=0)
    inactive:  int  = Field(20,   gt=0)
    batches:   int  = Field(100,  gt=0)
    seed:      int  = 1
    notes:     str | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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


def _resolve_materials(material_ids: list[str]):
    from .materials import get_materials_by_ids
    return get_materials_by_ids(material_ids)


def _make_run_settings(body: JobSubmitRequest | SweepSubmitRequest) -> OpenMCRunSettings:
    return OpenMCRunSettings(
        particles=body.particles,
        inactive=body.inactive,
        batches=body.batches,
        seed=body.seed,
    )


def _backend_for_job(job_id: str):
    """Reconstruct the correct backend for an existing job.

    Uses the config stored at submission time — ensures a SLURM job
    is always managed by SlurmBackend, never accidentally by DockerBackend.

    Raises:
        HTTPException 404: If job_id is unknown.
        HTTPException 500: If the stored config is corrupt or unresolvable.
    """
    raw_config = _job_backend_configs.get(job_id)
    if raw_config is None:
        raise HTTPException(
            status_code=500,
            detail=(
                f"No backend config found for job '{job_id}'. "
                "The server may have restarted — backend config is not yet persisted."
            ),
        )
    try:
        from pydantic import TypeAdapter
        config = TypeAdapter(BackendConfig).validate_python(raw_config)
        return create_backend(config)
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Could not reconstruct backend for job '{job_id}': {e}",
        )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post("/submit", response_model=JobSummary, status_code=202)
async def submit_job(body: JobSubmitRequest) -> JobSummary:
    """Submit a single simulation job.

    The backend_config block controls where the simulation runs.
    Omit it to use the default local Docker/Podman backend.

    Examples — see GET /jobs/backends/available for all config fields.
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
    backend   = create_backend(body.backend_config)

    job_id = str(uuid.uuid4())
    job = SimulationJob(
        id=job_id,
        geometry=geometry,
        materials=materials,
        param_values={},
        backend=body.backend_config.type,
        working_dir=Path(body.backend_config.jobs_base_dir) / job_id,
        notes=body.notes,
    )

    try:
        job = backend.submit(job)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Submission failed: {e}")

    _jobs[job_id] = job
    # Store raw config dict — reconstructed when polling/cancelling
    _job_backend_configs[job_id] = body.backend_config.model_dump()

    return _to_summary(job)


@router.post("/sweep", response_model=SweepResponse, status_code=202)
async def submit_sweep(body: SweepSubmitRequest) -> SweepResponse:
    """Submit a parametric sweep — one job per parameter combination."""
    errors = loader.validate(body.geometry_text)
    if errors:
        raise HTTPException(status_code=422, detail=errors)

    try:
        sweep_points = expand_sweep(body.geometry_text)
    except Exception as e:
        raise HTTPException(status_code=422, detail=str(e))

    materials = _resolve_materials(body.material_ids)
    backend   = create_backend(body.backend_config)
    sweep_id  = str(uuid.uuid4())
    job_ids:   list[str]       = []
    summaries: list[JobSummary] = []

    raw_config = body.backend_config.model_dump()

    for param_values, geometry in sweep_points:
        job_id = str(uuid.uuid4())
        job = SimulationJob(
            id=job_id,
            geometry=geometry,
            materials=materials,
            param_values=param_values,
            backend=body.backend_config.type,
            working_dir=Path(body.backend_config.jobs_base_dir) / job_id,
            notes=body.notes,
        )

        try:
            job = backend.submit(job)
        except Exception as e:
            job.status = JobStatus.FAILED
            job.error  = str(e)

        _jobs[job_id] = job
        _job_backend_configs[job_id] = raw_config
        job_ids.append(job_id)
        summaries.append(_to_summary(job))

    _sweeps[sweep_id] = job_ids
    return SweepResponse(sweep_id=sweep_id, jobs=summaries, total=len(summaries))


@router.get("/backends/available")
async def list_available_backends() -> list[dict]:
    """List available backend types with their configuration schemas.

    The frontend uses this to build the backend configuration form
    dynamically — showing the right fields for Docker vs SLURM etc.
    """
    from ..execution.backend_config import (
        DockerBackendConfig, LocalBackendConfig, SlurmBackendConfig,
    )
    return [
        {
            "type":        "docker",
            "label":       "Docker / Podman (local container)",
            "description": "Run simulations in a local container. Best for development.",
            "schema":      DockerBackendConfig.model_json_schema(),
            "default":     DockerBackendConfig().model_dump(),
        },
        {
            "type":        "local",
            "label":       "Local process (OpenMC installed directly)",
            "description": "Run OpenMC directly. Requires OpenMC on PATH or at openmc_bin.",
            "schema":      LocalBackendConfig.model_json_schema(),
            "default":     LocalBackendConfig().model_dump(),
        },
        {
            "type":        "slurm",
            "label":       "SLURM HPC cluster",
            "description": "Submit via SSH to a university cluster. Supports MetaCentrum.",
            "schema":      SlurmBackendConfig.model_json_schema(),
            "default":     None,
        },
    ]


@router.get("/", response_model=list[JobSummary])
async def list_jobs() -> list[JobSummary]:
    """List all jobs, most recent first."""
    return [
        _to_summary(j)
        for j in sorted(_jobs.values(), key=lambda j: j.created_at, reverse=True)
    ]


@router.get("/{job_id}", response_model=JobDetail)
async def get_job(job_id: str) -> JobDetail:
    """Get current status and detail for a job.

    If still running, polls the correct backend before returning.
    """
    job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found.")

    if job.status == JobStatus.RUNNING:
        try:
            backend    = _backend_for_job(job_id)
            job.status = backend.status(job)
            _jobs[job_id] = job
        except HTTPException:
            pass   # stale status is better than surfacing a 500 here
        except Exception:
            pass

    return _to_detail(job)


@router.post("/{job_id}/cancel", response_model=JobSummary)
async def cancel_job(job_id: str) -> JobSummary:
    """Cancel a queued or running job using the correct backend."""
    job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found.")

    if job.status in (JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED):
        raise HTTPException(
            status_code=409,
            detail=f"Job is already {job.status.value} — cannot cancel.",
        )

    backend = _backend_for_job(job_id)

    try:
        job = backend.cancel(job)
        _jobs[job_id] = job
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Cancel failed: {e}")

    return _to_summary(job)


@router.delete("/{job_id}", response_model=DeletedResponse)
async def delete_job(job_id: str) -> DeletedResponse:
    """Delete a job record and its working directory."""
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
    _job_backend_configs.pop(job_id, None)
    return DeletedResponse(id=job_id)