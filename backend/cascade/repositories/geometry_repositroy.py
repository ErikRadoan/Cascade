"""Geometry repository — persists geometry projects to the database.

Replaces the in-memory `_geometry_store` dict that used to live in
api/geometry.py. Unlike JobRepository/ProfileRepository, there's no domain
object to convert to/from here (CascadeGeometry is the *expanded* form,
not what's stored) — geometry.py already keeps the "expand from YAML on
every read" pattern, this repository just persists the raw YAML text and
the summary counts computed at save/update time, exactly like the old
dict did.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy.orm import Session

from .models import GeometryRow


@dataclass
class GeometryRecord:
    """Lightweight geometry domain object — mirrors the old dict entries."""
    id:         str
    name:       str
    text:       str
    n_surfaces: int
    n_cells:    int
    created_at: datetime

    def to_dict(self) -> dict:
        return {
            "id":         self.id,
            "name":       self.name,
            "text":       self.text,
            "n_surfaces": self.n_surfaces,
            "n_cells":    self.n_cells,
            "created_at": self.created_at,
        }


class GeometryRepository:
    """CRUD operations for geometry projects against the database."""

    def __init__(self, db: Session):
        self._db = db

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def create(
        self,
        name: str,
        text: str,
        n_surfaces: int = 0,
        n_cells: int = 0,
    ) -> GeometryRecord:
        row = GeometryRow(
            id=uuid4().hex,
            name=name,
            text=text,
            n_surfaces=n_surfaces,
            n_cells=n_cells,
            created_at=datetime.now(timezone.utc),
        )
        self._db.add(row)
        self._db.commit()
        return self._row_to_record(row)

    def update(
        self,
        geometry_id: str,
        text: str,
        name: str | None = None,
        n_surfaces: int = 0,
        n_cells: int = 0,
    ) -> GeometryRecord | None:
        """Update text/summary in place. Returns None if not found.

        `created_at` is preserved (same convention as ProfileRepository.update).
        """
        row = self._db.get(GeometryRow, geometry_id)
        if row is None:
            return None
        row.text = text
        row.n_surfaces = n_surfaces
        row.n_cells = n_cells
        if name:
            row.name = name
        self._db.commit()
        return self._row_to_record(row)

    def delete(self, geometry_id: str) -> bool:
        row = self._db.get(GeometryRow, geometry_id)
        if row is None:
            return False
        self._db.delete(row)
        self._db.commit()
        return True

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def get(self, geometry_id: str) -> GeometryRecord | None:
        row = self._db.get(GeometryRow, geometry_id)
        return self._row_to_record(row) if row else None

    def list(self) -> list[GeometryRecord]:
        """Return all geometries, most recently created first."""
        rows = (
            self._db.query(GeometryRow)
            .order_by(GeometryRow.created_at.desc())
            .all()
        )
        return [self._row_to_record(r) for r in rows]

    def exists(self, geometry_id: str) -> bool:
        return self._db.get(GeometryRow, geometry_id) is not None

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def _row_to_record(self, row: GeometryRow) -> GeometryRecord:
        return GeometryRecord(
            id=row.id,
            name=row.name,
            text=row.text,
            n_surfaces=row.n_surfaces,
            n_cells=row.n_cells,
            created_at=row.created_at,
        )