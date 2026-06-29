"""Job repository — persists SimulationJob to the database.

Replaces the in-memory _jobs dict in api/jobs.py.
Converts between domain objects (SimulationJob, CascadeGeometry, Material)
and ORM rows (JobRow). Nothing outside this file should know about JobRow.

Usage:
    from ..repositories.job_repository import JobRepository
    from ..repositories.db import get_db

    @router.post("/submit")
    def submit(db: Session = Depends(get_db)):
        repo = JobRepository(db)
        repo.save(job, backend_config_dict)
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from ..domain.job import JobStatus, SimulationJob
from ..domain.geometry import CascadeGeometry, Surface, Cell, SurfaceType, BoundaryType
from ..domain.geometry import Inside, Outside, Intersection, Union, Complement
from ..domain.material import Material
from .models import JobRow
from ..domain.results_config import ResultsConfig


class JobRepository:
    """CRUD operations for SimulationJob against the database."""

    def __init__(self, db: Session):
        self._db = db

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def save(self, job: SimulationJob, backend_config: dict) -> SimulationJob:
        """Insert or update a job record.

        Args:
            job:            The domain job object.
            backend_config: Raw dict of the backend config used to submit.
                            Stored so the correct backend can be reconstructed
                            for status polling and cancellation.

        Returns:
            The job, unchanged (for call-chaining convenience).
        """
        existing = self._db.get(JobRow, job.id)

        if existing is None:
            row = JobRow(
                id=             job.id,
                backend=        job.backend,
                status=         job.status.value,
                param_values=   job.param_values,
                backend_config= backend_config,
                geometry_json=  job.geometry.to_dict(),
                materials_json= [m.to_dict() for m in job.materials],
                results_config= job.results_config.to_dict(),
                working_dir=    str(job.working_dir) if job.working_dir else None,
                notes=          job.notes,
                error=          job.error,
                created_at=     job.created_at,
                started_at=     job.started_at,
                finished_at=    job.finished_at,
            )
            self._db.add(row)
        else:
            existing.status = job.status.value
            existing.error = job.error
            existing.working_dir = str(job.working_dir) if job.working_dir else None
            existing.started_at = job.started_at
            existing.finished_at = job.finished_at
            existing.results_config = job.results_config.to_dict()

        self._db.commit()
        return job

    def update_status(self, job_id: str, status: JobStatus, error: str | None = None,
                      started_at: datetime | None = None,
                      finished_at: datetime | None = None) -> None:
        """Efficiently update just the mutable fields of a running job.

        Avoids re-serializing geometry/materials on every status poll.
        """
        row = self._db.get(JobRow, job_id)
        if row is None:
            return
        row.status = status.value
        if error is not None:
            row.error = error
        if started_at is not None:
            row.started_at = started_at
        if finished_at is not None:
            row.finished_at = finished_at
        self._db.commit()

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def get(self, job_id: str) -> SimulationJob | None:
        """Retrieve a job by ID, or None if not found."""
        row = self._db.get(JobRow, job_id)
        if row is None:
            return None
        return self._row_to_domain(row)

    def get_backend_config(self, job_id: str) -> dict | None:
        """Return the raw backend config dict for a job."""
        row = self._db.get(JobRow, job_id)
        return row.backend_config if row else None

    def list(self, limit: int = 100, offset: int = 0) -> list[SimulationJob]:
        """Return jobs ordered by created_at descending."""
        rows = (
            self._db.query(JobRow)
            .order_by(JobRow.created_at.desc())
            .limit(limit)
            .offset(offset)
            .all()
        )
        return [self._row_to_domain(r) for r in rows]

    def list_by_status(self, status: JobStatus) -> list[SimulationJob]:
        """Return all jobs with a given status."""
        rows = (
            self._db.query(JobRow)
            .filter(JobRow.status == status.value)
            .order_by(JobRow.created_at.desc())
            .all()
        )
        return [self._row_to_domain(r) for r in rows]

    def delete(self, job_id: str) -> bool:
        """Delete a job record. Returns True if deleted, False if not found."""
        row = self._db.get(JobRow, job_id)
        if row is None:
            return False
        self._db.delete(row)
        self._db.commit()
        return True

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def _row_to_domain(self, row: JobRow) -> SimulationJob:
        """Convert a JobRow back to a SimulationJob domain object."""
        return SimulationJob(
            id=row.id,
            geometry=_geometry_from_dict(row.geometry_json),
            materials=[_material_from_dict(m) for m in row.materials_json],
            param_values=row.param_values or {},
            backend=row.backend,
            status=JobStatus(row.status),
            results_config=(
                ResultsConfig.from_dict(row.results_config)
                if row.results_config
                else ResultsConfig.default()
            ),
            working_dir=Path(row.working_dir) if row.working_dir else None,
            notes=row.notes,
            error=row.error,
            created_at=row.created_at,
            started_at=row.started_at,
            finished_at=row.finished_at,
        )


# ---------------------------------------------------------------------------
# Deserialization helpers
# ---------------------------------------------------------------------------
# These reconstruct domain objects from the JSON stored in the DB.
# They mirror the to_dict() methods on the domain classes.

def _geometry_from_dict(d: dict) -> CascadeGeometry:
    """Reconstruct a CascadeGeometry from its to_dict() output."""
    surfaces = [_surface_from_dict(s) for s in d.get("surfaces", [])]
    cells    = [_cell_from_dict(c)    for c in d.get("cells", [])]
    return CascadeGeometry(
        id=           d["id"],
        name=         d.get("name", ""),
        surfaces=     surfaces,
        cells=        cells,
        param_values= d.get("param_values", {}),
    )


def _surface_from_dict(d: dict) -> Surface:
    return Surface(
        id=            d["id"],
        type_=         SurfaceType(d["type"]),
        params=        d.get("params", {}),
        boundary_type= BoundaryType(d.get("boundary_type", "none")),
    )


def _cell_from_dict(d: dict) -> Cell:
    return Cell(
        id=          d["id"],
        region=      _region_from_str(d.get("region", "")),
        material_id= d.get("material_id"),
        name=        d.get("name"),
    )


def _region_from_str(region_str: str):
    """Reconstruct a Region from its __repr__ string.

    The repr format is what Cell.to_dict() stores:
        "-s1"         → Inside("s1")
        "+s1"         → Outside("s1")
        "(-s1 +s2)"   → Intersection([Inside("s1"), Outside("s2")])

    This is a best-effort reconstruction sufficient for re-running jobs.
    For display purposes the string itself is usable directly.
    """
    s = region_str.strip()

    if s.startswith("(") and s.endswith(")"):
        inner = s[1:-1]
        if " | " in inner:
            parts = inner.split(" | ")
            return Union([_region_from_str(p) for p in parts])
        else:
            parts = inner.split()
            if len(parts) == 1:
                return _region_from_str(parts[0])
            return Intersection([_region_from_str(p) for p in parts])

    if s.startswith("~"):
        return Complement(_region_from_str(s[1:]))

    if s.startswith("-"):
        return Inside(s[1:])

    if s.startswith("+"):
        return Outside(s[1:])

    # Fallback — wrap as Inside
    return Inside(s)


def _material_from_dict(d: dict) -> Material:
    return Material(
        id=          d["id"],
        name=        d["name"],
        density=     d.get("density"),
        composition= d.get("composition", {}),
    )