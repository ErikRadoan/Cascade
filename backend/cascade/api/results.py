"""Results routes — retrieve tally results and statepoint files."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from ..domain.job import JobStatus
from .schemas import SweepResultsResponse, TallyResultOut, TallyResultSet

router = APIRouter(prefix="/results", tags=["results"])


def _get_job(job_id: str):
    """Shared helper — looks up job and raises 404 if missing."""
    from .jobs import _jobs
    job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found.")
    return job


def _require_completed(job):
    if job.status != JobStatus.COMPLETED:
        raise HTTPException(
            status_code=409,
            detail=f"Job is {job.status.value}. Results are only available after completion.",
        )


def _parse_k_effective(job) -> tuple[float | None, float | None]:
    """Read k-effective from the run log if available.

    OpenMC prints lines like:
        k-effective (Track-length) = 1.36466 +/- 0.01914
    We parse the track-length estimator as it has the lowest variance.

    Returns (k_eff, k_unc) or (None, None) if not found.
    """
    if job.working_dir is None:
        return None, None

    log_path = job.working_dir / "run.log"
    if not log_path.exists():
        return None, None

    import re
    pattern = re.compile(
        r"k-effective \(Track-length\)\s*=\s*([\d.]+)\s*\+/-\s*([\d.]+)"
    )
    for line in log_path.read_text().splitlines():
        m = pattern.search(line)
        if m:
            return float(m.group(1)), float(m.group(2))

    return None, None


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/{job_id}", response_model=TallyResultSet)
async def get_results(job_id: str) -> TallyResultSet:
    """Get tally results for a completed job.

    Currently returns k-effective parsed from the run log.
    Full tally parsing from statepoint.h5 is a future implementation
    (requires tallies.xml to be generated and h5py parsing).
    """
    job = _get_job(job_id)
    _require_completed(job)

    k_eff, k_unc = _parse_k_effective(job)

    return TallyResultSet(
        job_id=job_id,
        param_values=job.param_values,
        tallies=[],          # populated when HDF5 parsing is implemented
        k_effective=k_eff,
        k_uncertainty=k_unc,
    )


@router.get("/sweep/{sweep_id}", response_model=SweepResultsResponse)
async def get_sweep_results(sweep_id: str) -> SweepResultsResponse:
    """Get results for all jobs in a parametric sweep.

    Returns one TallyResultSet per sweep point, each tagged with its
    param_values so the frontend can plot objective vs parameter.

    Jobs that are still running are included with empty tallies and
    null k-effective — the frontend should poll until all are complete.
    """
    from .jobs import _sweeps, _jobs

    job_ids = _sweeps.get(sweep_id)
    if job_ids is None:
        raise HTTPException(status_code=404, detail=f"Sweep '{sweep_id}' not found.")

    points: list[TallyResultSet] = []
    for job_id in job_ids:
        job = _jobs.get(job_id)
        if job is None:
            continue

        if job.status == JobStatus.COMPLETED:
            k_eff, k_unc = _parse_k_effective(job)
        else:
            k_eff = k_unc = None

        points.append(TallyResultSet(
            job_id=job_id,
            param_values=job.param_values,
            tallies=[],
            k_effective=k_eff,
            k_uncertainty=k_unc,
        ))

    return SweepResultsResponse(sweep_id=sweep_id, points=points)


@router.get("/{job_id}/download")
async def download_statepoint(job_id: str):
    """Download the raw OpenMC statepoint HDF5 file for a completed job.

    Returns the file as a binary download. The statepoint contains full
    tally data that can be analysed with OpenMC's Python API or h5py.
    """
    job = _get_job(job_id)
    _require_completed(job)

    if job.working_dir is None:
        raise HTTPException(status_code=404, detail="No working directory for this job.")

    statepoints = list(job.input_dir().glob("statepoint.*.h5"))
    if not statepoints:
        raise HTTPException(
            status_code=404,
            detail="No statepoint file found. The job may have completed without producing output.",
        )

    # Return the last statepoint (highest batch number)
    statepoint = sorted(statepoints)[-1]

    return FileResponse(
        path=str(statepoint),
        filename=statepoint.name,
        media_type="application/x-hdf5",
    )