"""Sweep repository — persists parametric sweep records to the database.

A sweep groups multiple SimulationJob records that were submitted together
as a parametric study. The repository stores the sweep metadata and
derives overall status from the child jobs at read time.

Usage:
    from ..repositories.sweep_repository import SweepRepository, SweepRecord
    from ..repositories.db import get_db

    @router.get("/results/sweep/{sweep_id}")
    def get_sweep(sweep_id: str, db: Session = Depends(get_db)):
        repo = SweepRepository(db)
        record = repo.get(sweep_id)
        ...
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from ..domain.job import JobStatus
from .job_repository import JobRepository
from .models import JobRow, SweepRow


# ---------------------------------------------------------------------------
# Sweep status — derived from child job statuses
# ---------------------------------------------------------------------------

class SweepStatus:
    PENDING   = "pending"    # all jobs still running or queued
    PARTIAL   = "partial"    # some completed, some still running
    COMPLETE  = "complete"   # all jobs completed successfully
    FAILED    = "failed"     # all jobs failed
    MIXED     = "mixed"      # some completed, some failed


def _derive_status(statuses: list[str]) -> str:
    """Compute overall sweep status from a list of child job status strings."""
    if not statuses:
        return SweepStatus.PENDING

    status_set = set(statuses)
    all_done   = not (status_set & {JobStatus.QUEUED, JobStatus.RUNNING})

    if not all_done:
        has_complete = JobStatus.COMPLETED in status_set
        return SweepStatus.PARTIAL if has_complete else SweepStatus.PENDING

    # All finished
    has_complete = JobStatus.COMPLETED in status_set
    has_failed   = JobStatus.FAILED   in status_set

    if has_complete and has_failed:
        return SweepStatus.MIXED
    if has_complete:
        return SweepStatus.COMPLETE
    return SweepStatus.FAILED


# ---------------------------------------------------------------------------
# SweepRecord — the domain object returned by SweepRepository
# ---------------------------------------------------------------------------

@dataclass
class SweepRecord:
    """A parametric sweep with its child job summaries.

    Attributes:
        sweep_id:      Unique identifier.
        job_ids:       Ordered list of job IDs in this sweep.
        geometry_text: Original YAML that defined the sweep.
        swept_params:  Dict of param_name -> list of values that were swept.
                       e.g. {"fuel_pin.pellet_radius": [0.38, 0.39, 0.40]}
        notes:         Optional user label.
        status:        Derived overall status (see SweepStatus constants).
        total:         Total number of jobs in this sweep.
        completed:     Number of jobs with status=COMPLETED.
        failed:        Number of jobs with status=FAILED.
        created_at:    UTC timestamp of sweep creation.
    """
    sweep_id:      str
    job_ids:       list[str]
    geometry_text: str
    swept_params:  dict[str, list]
    notes:         str | None
    status:        str
    total:         int
    completed:     int
    failed:        int
    created_at:    datetime

    def to_dict(self) -> dict:
        return {
            "sweep_id":      self.sweep_id,
            "job_ids":       self.job_ids,
            "geometry_text": self.geometry_text,
            "swept_params":  self.swept_params,
            "notes":         self.notes,
            "status":        self.status,
            "total":         self.total,
            "completed":     self.completed,
            "failed":        self.failed,
            "created_at":    self.created_at.isoformat(),
        }


# ---------------------------------------------------------------------------
# SweepRepository
# ---------------------------------------------------------------------------

class SweepRepository:
    """CRUD operations for parametric sweeps against the database."""

    def __init__(self, db: Session):
        self._db = db

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def save(
        self,
        sweep_id:      str,
        job_ids:       list[str],
        geometry_text: str,
        swept_params:  dict[str, list],
        notes:         str | None = None,
    ) -> SweepRecord:
        """Persist a new sweep record.

        Args:
            sweep_id:      UUID string for the sweep.
            job_ids:       IDs of all jobs in this sweep, in submission order.
            geometry_text: The YAML text used to generate the sweep.
            swept_params:  Which params were swept and their values.
                           e.g. {"fuel_pin.pellet_radius": [0.38, 0.39, 0.40]}
            notes:         Optional user label.

        Returns:
            SweepRecord with status derived from child jobs.
        """
        row = SweepRow(
            sweep_id=      sweep_id,
            job_ids=       job_ids,
            geometry_text= geometry_text,
            swept_params=  swept_params,
            notes=         notes,
            created_at=    datetime.now(timezone.utc),
        )
        self._db.add(row)
        self._db.commit()
        return self._row_to_record(row)

    def delete(self, sweep_id: str, delete_jobs: bool = False) -> bool:
        """Delete a sweep record.

        Args:
            sweep_id:     Sweep to delete.
            delete_jobs:  If True, also delete all child job records
                          and their working directories.
                          If False, child jobs are kept (orphaned).

        Returns:
            True if deleted, False if not found.
        """
        row = self._db.get(SweepRow, sweep_id)
        if row is None:
            return False

        if delete_jobs:
            job_repo = JobRepository(self._db)
            import shutil
            for job_id in row.job_ids:
                job = job_repo.get(job_id)
                if job and job.working_dir and job.working_dir.exists():
                    shutil.rmtree(job.working_dir, ignore_errors=True)
                job_repo.delete(job_id)

        self._db.delete(row)
        self._db.commit()
        return True

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def get(self, sweep_id: str) -> SweepRecord | None:
        """Get a sweep by ID, with derived status from child jobs."""
        row = self._db.get(SweepRow, sweep_id)
        return self._row_to_record(row) if row else None

    def list(self) -> list[SweepRecord]:
        """Return all sweeps, most recent first."""
        rows = (
            self._db.query(SweepRow)
            .order_by(SweepRow.created_at.desc())
            .all()
        )
        return [self._row_to_record(r) for r in rows]

    def get_job_statuses(self, sweep_id: str) -> list[str]:
        """Return the current status string for each job in a sweep.

        Queries the jobs table directly — avoids deserializing full job objects
        just to check status.
        """
        row = self._db.get(SweepRow, sweep_id)
        if row is None:
            return []

        job_rows = (
            self._db.query(JobRow.status)
            .filter(JobRow.id.in_(row.job_ids))
            .all()
        )
        return [r.status for r in job_rows]

    def get_jobs(self, sweep_id: str) -> list:
        """Return full SimulationJob domain objects for all jobs in a sweep.

        Used by the results endpoint to build per-point result sets.
        """
        row = self._db.get(SweepRow, sweep_id)
        if row is None:
            return []

        job_repo = JobRepository(self._db)
        return [
            job
            for job_id in row.job_ids
            if (job := job_repo.get(job_id)) is not None
        ]

    # ------------------------------------------------------------------
    # Private
    # ------------------------------------------------------------------

    def _row_to_record(self, row: SweepRow) -> SweepRecord:
        """Convert a SweepRow to a SweepRecord, deriving status from child jobs."""
        statuses  = self.get_job_statuses(row.sweep_id)
        status    = _derive_status(statuses)
        completed = statuses.count(JobStatus.COMPLETED)
        failed    = statuses.count(JobStatus.FAILED)

        return SweepRecord(
            sweep_id=      row.sweep_id,
            job_ids=       row.job_ids,
            geometry_text= row.geometry_text,
            swept_params=  row.swept_params or {},
            notes=         row.notes,
            status=        status,
            total=         len(row.job_ids),
            completed=     completed,
            failed=        failed,
            created_at=    row.created_at,
        )