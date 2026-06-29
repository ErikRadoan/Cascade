"""Jobs routes — submit, monitor, and cancel simulation jobs."""

from __future__ import annotations

import shutil
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..repositories.db import SessionLocal, get_db
from ..repositories.job_repository import JobRepository
from ..repositories.sweep_repository import SweepRepository
from ..adapters.openmc_adapter import OpenMCRunSettings
from ..domain.job import JobStatus, SimulationJob
from ..domain.results_config import (
    DiagnosticsConfig,
    EnergyGroupStructure,
    EnergySpectraConfig,
    MeshTallyConfig,
    MeshType,
    ResultsConfig,
    ScalarTallyConfig,
    SimulationSummaryConfig,
    TallyScore,
)
from ..dsl import loader, expander
from ..dsl.sweep import expand_sweep, parse_sweep, validate_preview
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

JOBS_BASE_DIR = Path.home() / ".cascade" / "jobs"

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

class ScalarTallyRequest(BaseModel):
    """Controls per-cell scalar tallies (flux, fission, absorption, heating)."""
    enabled:   bool            = True
    scores:    list[str]       = Field(
        default=["flux", "fission", "absorption", "heating"],
        description="TallyScore values to measure per cell.",
    )
    all_cells: bool            = False


class MeshTallyRequest(BaseModel):
    """Controls the 3-D mesh tally (power/flux map)."""
    enabled:   bool       = False
    mesh_type: str        = "regular"   # "regular" | "cylindrical"
    nx:        int        = Field(20, gt=0)
    ny:        int        = Field(20, gt=0)
    nz:        int        = Field(20, gt=0)
    nr:        int        = Field(20, gt=0)   # cylindrical only
    nz_cyl:    int        = Field(20, gt=0)   # cylindrical only
    scores:    list[str]  = Field(default=["flux", "fission", "heating-local"])


class EnergySpectraRequest(BaseModel):
    """Controls flux-vs-energy spectra per material region."""
    enabled:         bool = False
    group_structure: str  = "69"    # "33" | "69" | "252"
    per_material:    bool = True


class DiagnosticsRequest(BaseModel):
    stochastic_volumes: bool = False
    particle_tracks:    bool = False
    n_tracks:           int  = Field(100, gt=0)


class ResultsConfigRequest(BaseModel):
    """What to ask OpenMC to capture.  All groups default to the cheapest option."""
    scalars:     ScalarTallyRequest   = Field(default_factory=ScalarTallyRequest)
    mesh:        MeshTallyRequest     = Field(default_factory=MeshTallyRequest)
    spectra:     EnergySpectraRequest = Field(default_factory=EnergySpectraRequest)
    diagnostics: DiagnosticsRequest   = Field(default_factory=DiagnosticsRequest)

    def to_domain(self) -> ResultsConfig:
        """Convert Pydantic request model → domain ResultsConfig."""
        return ResultsConfig(
            summary=SimulationSummaryConfig(),  # always on
            scalars=ScalarTallyConfig(
                enabled=self.scalars.enabled,
                scores=[TallyScore(s) for s in self.scalars.scores],
                all_cells=self.scalars.all_cells,
            ),
            mesh=MeshTallyConfig(
                enabled=self.mesh.enabled,
                mesh_type=MeshType(self.mesh.mesh_type),
                nx=self.mesh.nx,
                ny=self.mesh.ny,
                nz=self.mesh.nz,
                nr=self.mesh.nr,
                nz_cyl=self.mesh.nz_cyl,
                scores=[TallyScore(s) for s in self.mesh.scores],
            ),
            spectra=EnergySpectraConfig(
                enabled=self.spectra.enabled,
                group_structure=EnergyGroupStructure(self.spectra.group_structure),
                per_material=self.spectra.per_material,
            ),
            diagnostics=DiagnosticsConfig(
                stochastic_volumes=self.diagnostics.stochastic_volumes,
                particle_tracks=self.diagnostics.particle_tracks,
                n_tracks=self.diagnostics.n_tracks,
            ),
        )


class JobSubmitRequest(BaseModel):
    geometry_text:  str
    material_ids:   list[str]
    backend_config: BackendConfig          = Field(default=_DEFAULT_BACKEND_CONFIG)
    particles:      int                    = Field(1000, gt=0)
    inactive:       int                    = Field(20,   gt=0)
    batches:        int                    = Field(100,  gt=0)
    seed:           int                    = 1
    results_config: ResultsConfigRequest   = Field(default_factory=ResultsConfigRequest)
    notes:          str | None             = None


class SweepSubmitRequest(BaseModel):
    geometry_text:  str
    material_ids:   list[str]
    backend_config: BackendConfig          = Field(default=_DEFAULT_BACKEND_CONFIG)
    particles:      int                    = Field(1000, gt=0)
    inactive:       int                    = Field(20,   gt=0)
    batches:        int                    = Field(100,  gt=0)
    seed:           int                    = 1
    results_config: ResultsConfigRequest   = Field(default_factory=ResultsConfigRequest)
    notes:          str | None             = None


# ---------------------------------------------------------------------------
# Pure helpers — no DB access, no Depends
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


def _extract_swept_params(geometry_text: str) -> dict[str, list[float]]:
    """Parse sweep(...) expressions from YAML, returning param name -> value list."""
    import yaml as _yaml
    try:
        raw = _yaml.safe_load(geometry_text)
    except Exception:
        return {}

    result: dict[str, list[float]] = {}
    for comp_name, comp_data in raw.items():
        if not isinstance(comp_data, dict):
            continue
        for field_name, field_value in comp_data.items():
            if field_name == "type":
                continue
            values = parse_sweep(field_value)
            if values is not None:
                result[f"{comp_name}.{field_name}"] = values
    return result


# ---------------------------------------------------------------------------
# DB helpers — these are called from route handlers that already hold a
# session, so they accept db as a parameter rather than opening their own.
# The two exceptions (_backend_for_job, _get_job_or_404) are called from
# within route handlers *after* the injected session may have closed, so
# they open their own short-lived sessions via SessionLocal().
# ---------------------------------------------------------------------------

def _backend_for_job(job_id: str, db: Session):
    """Reconstruct the correct backend for a job using its stored config.

    Accepts the caller's session so we never open a competing SessionLocal()
    while the outer session is still active — that caused SQLite 'database is
    locked' errors which were swallowed by the bare except, leaving jobs stuck
    in RUNNING forever.
    """
    raw_config = JobRepository(db).get_backend_config(job_id)

    if raw_config is None:
        raise HTTPException(
            status_code=500,
            detail=f"No backend config found for job '{job_id}'.",
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


def _get_job_or_404(job_id: str, db: Session) -> SimulationJob:
    """Fetch a job from the DB or raise 404."""
    job = JobRepository(db).get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found.")
    return job


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post(
    "/submit",
    response_model=JobSummary | SweepResponse,
    status_code=202,
)
async def submit_job(
    body: JobSubmitRequest,
    db: Session = Depends(get_db),
):
    swept_params = _extract_swept_params(body.geometry_text)

    if swept_params:
        sweep_request = SweepSubmitRequest(
            geometry_text=body.geometry_text,
            material_ids=body.material_ids,
            backend_config=body.backend_config,
            particles=body.particles,
            inactive=body.inactive,
            batches=body.batches,
            seed=body.seed,
            results_config=body.results_config,
            notes=body.notes,
        )

        return await submit_sweep(sweep_request, db)

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
        results_config=body.results_config.to_domain(),
        working_dir=Path(body.backend_config.jobs_base_dir) / job_id,
        notes=body.notes,
    )

    try:
        job = backend.submit(job)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Submission failed: {e}")

    JobRepository(db).save(job, body.backend_config.model_dump())
    return _to_summary(job)


@router.post("/sweep", response_model=SweepResponse, status_code=202)
async def submit_sweep(
    body: SweepSubmitRequest,
    db:   Session = Depends(get_db),
) -> SweepResponse:
    """Submit a parametric sweep — one job per parameter combination."""
    errors = validate_preview(body.geometry_text)
    if errors:
        raise HTTPException(status_code=422, detail=errors)

    try:
        sweep_points = expand_sweep(body.geometry_text)
    except Exception as e:
        raise HTTPException(status_code=422, detail=str(e))

    if not sweep_points:
        raise HTTPException(
            status_code=422,
            detail="No sweep parameters found. Add sweep(...) expressions to the geometry.",
        )

    materials    = _resolve_materials(body.material_ids)
    backend      = create_backend(body.backend_config)
    sweep_id     = str(uuid.uuid4())
    raw_config   = body.backend_config.model_dump()
    swept_params = _extract_swept_params(body.geometry_text)
    job_repo     = JobRepository(db)

    job_ids:   list[str]        = []
    summaries: list[JobSummary] = []

    for param_values, geometry in sweep_points:
        job_id = str(uuid.uuid4())
        job = SimulationJob(
            id=job_id,
            geometry=geometry,
            materials=materials,
            param_values=param_values,
            backend=body.backend_config.type,
            results_config=body.results_config.to_domain(),
            working_dir=Path(body.backend_config.jobs_base_dir) / job_id,
            notes=body.notes,
        )

        try:
            job = backend.submit(job)
        except Exception as e:
            job.status = JobStatus.FAILED
            job.error  = str(e)

        job_repo.save(job, raw_config)
        job_ids.append(job_id)
        summaries.append(_to_summary(job))

    SweepRepository(db).save(
        sweep_id=      sweep_id,
        job_ids=       job_ids,
        geometry_text= body.geometry_text,
        swept_params=  swept_params,
        notes=         body.notes,
    )

    return SweepResponse(sweep_id=sweep_id, jobs=summaries, total=len(summaries))


@router.get("/sweeps", response_model=list[dict])
async def list_sweeps(db: Session = Depends(get_db)) -> list[dict]:
    """List all parametric sweeps, most recent first."""
    return [r.to_dict() for r in SweepRepository(db).list()]


@router.get("/sweeps/{sweep_id}", response_model=dict)
async def get_sweep(
    sweep_id: str,
    db:       Session = Depends(get_db),
) -> dict:
    """Get sweep metadata and derived status."""
    record = SweepRepository(db).get(sweep_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Sweep '{sweep_id}' not found.")
    return record.to_dict()


@router.delete("/sweeps/{sweep_id}", response_model=DeletedResponse)
async def delete_sweep(
    sweep_id:    str,
    delete_jobs: bool    = False,
    db:          Session = Depends(get_db),
) -> DeletedResponse:
    """Delete a sweep record.

    Query params:
        delete_jobs: Also delete child job records and working directories.
    """
    deleted = SweepRepository(db).delete(sweep_id, delete_jobs=delete_jobs)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Sweep '{sweep_id}' not found.")
    return DeletedResponse(id=sweep_id)


@router.get("/backends/available")
async def list_available_backends() -> list[dict]:
    """List available backend types with their configuration schemas."""
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
async def list_jobs(db: Session = Depends(get_db)) -> list[JobSummary]:
    """List all jobs, most recent first."""
    return [_to_summary(j) for j in JobRepository(db).list()]


@router.get("/{job_id}", response_model=JobDetail)
async def get_job(
    job_id: str,
    db:     Session = Depends(get_db),
) -> JobDetail:
    """Get current status and detail for a job.

    Polls the correct backend if still running, then persists any status change.
    """
    job = _get_job_or_404(job_id, db)

    if job.status == JobStatus.RUNNING:
        try:
            backend     = _backend_for_job(job_id, db)
            # Capture status BEFORE calling backend.status() because the
            # backend mutates job.status in-place — comparing new_status to
            # job.status after the call always yields equal, so the update
            # block was never entered and the DB was never written.
            old_status  = job.status
            new_status  = backend.status(job)   # may mutate job in-place
            if new_status != old_status:
                # Ensure finished_at is set for terminal transitions
                if new_status in (JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED):
                    if not job.finished_at:
                        from datetime import datetime, timezone
                        job.finished_at = datetime.now(timezone.utc)
                JobRepository(db).update_status(
                    job_id=      job_id,
                    status=      new_status,
                    error=       job.error,
                    started_at=  job.started_at,
                    finished_at= job.finished_at,
                )
                db.expire_all()
                db.commit()
        except HTTPException:
            raise
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(
                "Failed to persist status update for job %s: %s", job_id, e
            )

    return _to_detail(job)


@router.post("/{job_id}/cancel", response_model=JobSummary)
async def cancel_job(
    job_id: str,
    db:     Session = Depends(get_db),
) -> JobSummary:
    """Cancel a queued or running job using the correct backend."""
    job = _get_job_or_404(job_id, db)

    if job.status in (JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED):
        raise HTTPException(
            status_code=409,
            detail=f"Job is already {job.status.value} — cannot cancel.",
        )

    backend = _backend_for_job(job_id, db)

    try:
        job = backend.cancel(job)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Cancel failed: {e}")

    JobRepository(db).update_status(
        job_id=      job_id,
        status=      job.status,
        finished_at= job.finished_at,
    )

    return _to_summary(job)


@router.delete("/{job_id}", response_model=DeletedResponse)
async def delete_job(
    job_id: str,
    db:     Session = Depends(get_db),
) -> DeletedResponse:
    """Delete a job record and its working directory."""
    job = _get_job_or_404(job_id, db)

    if job.status == JobStatus.RUNNING:
        raise HTTPException(
            status_code=409,
            detail="Cannot delete a running job. Cancel it first.",
        )

    if job.working_dir and job.working_dir.exists():
        shutil.rmtree(job.working_dir, ignore_errors=True)

    JobRepository(db).delete(job_id)
    return DeletedResponse(id=job_id)

@router.get("/{job_id}/stdout")
async def get_job_stdout(
    job_id: str,
    db:     Session = Depends(get_db),
) -> dict:
    """Return the raw stdout from a job's run.log file.

    This is the actual OpenMC terminal output — particle transport
    progress, k-eff per batch, timing, and errors.
    Polled every 2s by the frontend while a job is running.

    Returns:
        { "lines": str, "available": bool }
    """
    job = _get_job_or_404(job_id, db)

    if not job.working_dir:
        return {"lines": "", "available": False}

    log_path = job.working_dir / "run.log"
    if not log_path.exists():
        return {"lines": "", "available": False}

    try:
        content = log_path.read_text(encoding="utf-8", errors="replace")
        return {"lines": content, "available": True}
    except OSError:
        return {"lines": "", "available": False}