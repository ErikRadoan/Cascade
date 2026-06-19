"""Project repository — persists projects to the database."""

from __future__ import annotations

from uuid import uuid4

from sqlalchemy.orm import Session

from .models import ProjectRow


class ProjectRecord:
    """Lightweight project domain object — no dependencies on other domain models."""

    def __init__(self, id: str, name: str, description: str | None, created_at):
        self.id          = id
        self.name        = name
        self.description = description
        self.created_at  = created_at

    def to_dict(self) -> dict:
        return {
            "id":          self.id,
            "name":        self.name,
            "description": self.description,
            "created_at":  self.created_at.isoformat(),
        }


class ProjectRepository:
    """CRUD operations for projects against the database."""

    def __init__(self, db: Session):
        self._db = db

    def create(self, name: str, description: str | None = None) -> ProjectRecord:
        row = ProjectRow(id=uuid4().hex, name=name, description=description)
        self._db.add(row)
        self._db.commit()
        return self._row_to_domain(row)

    def get(self, project_id: str) -> ProjectRecord | None:
        row = self._db.get(ProjectRow, project_id)
        return self._row_to_domain(row) if row else None

    def list(self) -> list[ProjectRecord]:
        rows = (
            self._db.query(ProjectRow)
            .order_by(ProjectRow.created_at.desc())
            .all()
        )
        return [self._row_to_domain(r) for r in rows]

    def delete(self, project_id: str) -> bool:
        row = self._db.get(ProjectRow, project_id)
        if row is None:
            return False
        self._db.delete(row)
        self._db.commit()
        return True

    def _row_to_domain(self, row: ProjectRow) -> ProjectRecord:
        return ProjectRecord(
            id=          row.id,
            name=        row.name,
            description= row.description,
            created_at=  row.created_at,
        )