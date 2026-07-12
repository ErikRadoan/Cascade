"""Results API — HTTP layer over OpenMCAdapter's statepoint parsing.

Endpoints
---------
GET /results/{job_id}/summary
    k-eff (all three estimators + combined), neutron balance, timing,
    per-batch k history, Shannon entropy history.

GET /results/{job_id}/tallies
    Scalar cell tallies — mean + std dev per cell per score (tally IDs 101–199).

GET /results/{job_id}/mesh
    3-D mesh tally as a structured response (tally ID 200).

GET /results/{job_id}/spectra
    Energy flux spectra per material (tally IDs 301+).

GET /results/{job_id}/statepoint/path
    Filesystem path of the final statepoint file (for download links).

This router only handles HTTP concerns: resolving the job, checking status,
figuring out which directories on disk might hold results (job-level vs.
per-step, since a job's `.steps` may be populated for r2s/depletion runs),
and translating adapter-level errors into HTTP responses. All knowledge of
OpenMC's on-disk file formats (statepoint layout, tally id conventions,
sum/sum_sq encoding, mesh layout) lives in OpenMCAdapter — see
adapters/openmc_adapter.py's "Result import" section — so the write side
(export_tallies, export_settings, ...) and read side stay in sync in one
place instead of drifting apart.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import h5py
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..adapters.openmc_adapter import OpenMCAdapter
from ..repositories.db import get_db
from ..repositories.job_repository import JobRepository
from ..domain.job import JobStatus, SimulationJob

router = APIRouter(prefix="/results", tags=["results"])

adapter = OpenMCAdapter()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_job_or_404(job_id: str, db: Session) -> SimulationJob:
    job = JobRepository(db).get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found.")
    return job


def _require_completed(job: SimulationJob) -> None:
    status = job.effective_status()
    if status != JobStatus.COMPLETED:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Job '{job.id}' is {status.value} — results are only "
                "available for completed jobs."
            ),
        )


def _result_search_dirs(job: SimulationJob) -> list[Path]:
    """Directories that may hold this job's OpenMC output files.

    Legacy (unstepped) jobs run directly in job.input_dir()/output_dir().
    Stepped jobs (r2s, depletion) run each leg in its own JobStep's
    input_dir()/output_dir() instead — job.input_dir()/output_dir() are
    never created for these at all (see docker_backend.py's _submit_r2s /
    _submit_depletion). The "final" result for a stepped job lives with
    the highest-sequence step (for r2s, the photon leg; for depletion,
    the only step there is) — this mirrors DockerBackend.fetch_results().
    """
    if job.steps:
        last_step = max(job.steps, key=lambda s: s.sequence)
        return [last_step.input_dir(), last_step.output_dir()]
    return [job.input_dir(), job.output_dir()]


def _open_statepoint(job: SimulationJob) -> tuple[Path, h5py.File]:
    """Resolve and open this job's statepoint file, as HTTP errors."""
    search_dirs = _result_search_dirs(job)
    try:
        sp_path = adapter.find_statepoint(search_dirs)
    except FileNotFoundError:
        raise HTTPException(
            status_code=404,
            detail=(
                f"No statepoint file found for job '{job.id}'. "
                "The job may have failed before writing output, or is still running."
            ),
        )
    try:
        return sp_path, adapter.open_statepoint(sp_path)
    except OSError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


# ---------------------------------------------------------------------------
# /summary
# ---------------------------------------------------------------------------

@router.get("/{job_id}/summary")
async def get_summary(
    job_id: str,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Return k-eff, neutron balance, timing, and per-batch convergence history."""
    job = _get_job_or_404(job_id, db)
    _require_completed(job)
    sp_path, sp = _open_statepoint(job)
    try:
        return adapter.import_summary(job_id, sp)
    finally:
        sp.close()


def _scalars_config(job: SimulationJob):
    """Find the ScalarTallyConfig that governs this job's cell tallies.

    Single-leg modes (eigenvalue/fixed_source/depletion) carry a flat
    ResultsConfig — `job.results_config.scalars` directly. r2s carries a
    per-leg R2SResultsConfig instead (job.py's SimulationJob docstring,
    job-settings-model.md §1) with no top-level `.scalars` — scalar cell
    tallies only make sense for the neutron leg here (the photon leg's
    ResultsConfig can't request fission/nu-fission at all, per
    SimulationJob.__post_init__), so fall back to that.

    Returns None if neither shape matches, which callers should treat as
    "can't reconstruct tally names for this job" rather than crashing.
    """
    scalars = getattr(job.results_config, "scalars", None)
    if scalars is not None:
        return scalars
    neutron_leg = getattr(job.results_config, "neutron_leg", None)
    return getattr(neutron_leg, "scalars", None)


# ---------------------------------------------------------------------------
# /tallies
# ---------------------------------------------------------------------------

@router.get("/{job_id}/tallies")
async def get_tallies(
    job_id: str,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Return scalar cell tally results — mean + std dev per cell per score."""
    job = _get_job_or_404(job_id, db)
    _require_completed(job)

    scalars_cfg = _scalars_config(job)
    if scalars_cfg is None:
        raise HTTPException(
            status_code=404,
            detail=f"Job '{job_id}' has no scalar tally configuration to reconstruct results against.",
        )

    sp_path, sp = _open_statepoint(job)
    try:
        return adapter.import_tallies(job_id, sp, job.geometry, job.materials, scalars_cfg)
    finally:
        sp.close()


# ---------------------------------------------------------------------------
# /mesh
# ---------------------------------------------------------------------------

@router.get("/{job_id}/mesh")
async def get_mesh(
    job_id: str,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Return the 3-D mesh tally as a structured response."""
    job = _get_job_or_404(job_id, db)
    _require_completed(job)

    if not job.results_config.mesh.enabled:
        raise HTTPException(
            status_code=404,
            detail="Mesh tally was not requested for this job.",
        )

    sp_path, sp = _open_statepoint(job)
    try:
        return adapter.import_mesh(job_id, sp, job.results_config.mesh.mesh_type)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    finally:
        sp.close()


# ---------------------------------------------------------------------------
# /spectra
# ---------------------------------------------------------------------------

@router.get("/{job_id}/spectra")
async def get_spectra(
    job_id: str,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Return energy flux spectra per material (tally IDs 301+)."""
    job = _get_job_or_404(job_id, db)
    _require_completed(job)

    if not job.results_config.spectra.enabled:
        raise HTTPException(
            status_code=404,
            detail="Energy spectra were not requested for this job.",
        )

    sp_path, sp = _open_statepoint(job)
    try:
        return adapter.import_spectra(job_id, sp, job.results_config)
    finally:
        sp.close()


# ---------------------------------------------------------------------------
# /statepoint/path
# ---------------------------------------------------------------------------

@router.get("/{job_id}/statepoint/path")
async def get_statepoint_path(
    job_id: str,
    db: Session = Depends(get_db),
) -> dict[str, str]:
    """Return the filesystem path of the final statepoint file."""
    job = _get_job_or_404(job_id, db)
    _require_completed(job)
    sp_path, sp = _open_statepoint(job)
    sp.close()
    return {"job_id": job_id, "path": str(sp_path)}