"""Results routes — retrieve tally results and statepoint files."""

from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from ..repositories.db import get_db
from ..repositories.job_repository import JobRepository
from ..repositories.sweep_repository import SweepRepository
from ..domain.job import JobStatus
from .schemas import SweepResultsResponse, TallyResultOut, TallyResultSet

router = APIRouter(prefix="/results", tags=["results"])


# ---------------------------------------------------------------------------
# Pure helpers — no DB, no Depends
# ---------------------------------------------------------------------------

def _require_completed(job) -> None:
    if job.status != JobStatus.COMPLETED:
        raise HTTPException(
            status_code=409,
            detail=f"Job is {job.status.value}. Results only available after completion.",
        )


def _parse_k_effective(job) -> tuple[float | None, float | None]:
    """Read k-effective (track-length estimator) from the run log.

    Returns (k_eff, k_unc) or (None, None) if not found.
    """
    if job.working_dir is None:
        return None, None

    log_path = job.working_dir / "run.log"
    if not log_path.exists():
        return None, None

    pattern = re.compile(
        r"k-effective \(Track-length\)\s*=\s*([\d.]+)\s*\+/-\s*([\d.]+)"
    )
    for line in log_path.read_text().splitlines():
        m = pattern.search(line)
        if m:
            return float(m.group(1)), float(m.group(2))

    return None, None


def _get_job_or_404(job_id: str, db: Session):
    """Fetch a job or raise 404. Accepts the injected session from the caller."""
    job = JobRepository(db).get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found.")
    return job


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/{job_id}", response_model=TallyResultSet)
async def get_results(
    job_id: str,
    db:     Session = Depends(get_db),   # note: get_db, not get_db()
) -> TallyResultSet:
    """Get tally results for a completed job.

    Currently returns k-effective parsed from the run log.
    Full tally parsing from statepoint.h5 is a future implementation.
    """
    job = _get_job_or_404(job_id, db)
    _require_completed(job)
    k_eff, k_unc = _parse_k_effective(job)

    return TallyResultSet(
        job_id=job_id,
        param_values=job.param_values,
        tallies=[],
        k_effective=k_eff,
        k_uncertainty=k_unc,
    )


@router.get("/sweep/{sweep_id}", response_model=SweepResultsResponse)
async def get_sweep_results(
    sweep_id: str,
    db:       Session = Depends(get_db),
) -> SweepResultsResponse:
    """Get results for all jobs in a parametric sweep.

    Returns one TallyResultSet per sweep point tagged with param_values
    so the frontend can plot objective vs parameter.

    Jobs still running are included with null k_effective — poll until complete.
    """
    record = SweepRepository(db).get(sweep_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Sweep '{sweep_id}' not found.")

    job_repo = JobRepository(db)
    points: list[TallyResultSet] = []

    for job_id in record.job_ids:
        job = job_repo.get(job_id)
        if job is None:
            continue

        k_eff, k_unc = _parse_k_effective(job) if job.status == JobStatus.COMPLETED else (None, None)

        points.append(TallyResultSet(
            job_id=job_id,
            param_values=job.param_values,
            tallies=[],
            k_effective=k_eff,
            k_uncertainty=k_unc,
        ))

    return SweepResultsResponse(sweep_id=sweep_id, points=points)


@router.get("/{job_id}/download")
async def download_statepoint(
    job_id: str,
    db:     Session = Depends(get_db),
):
    """Download the raw OpenMC statepoint HDF5 file for a completed job."""
    job = _get_job_or_404(job_id, db)
    _require_completed(job)

    if job.working_dir is None:
        raise HTTPException(status_code=404, detail="No working directory for this job.")

    statepoints = list(job.input_dir().glob("statepoint.*.h5"))
    if not statepoints:
        raise HTTPException(
            status_code=404,
            detail="No statepoint file found.",
        )

    statepoint = sorted(statepoints)[-1]
    return FileResponse(
        path=str(statepoint),
        filename=statepoint.name,
        media_type="application/x-hdf5",
    )