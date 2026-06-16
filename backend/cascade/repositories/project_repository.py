"""In-memory project repository scaffold."""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import uuid4
@dataclass(slots=True)
class ProjectRecord:
    id: str
    name: str
    description: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "created_at": self.created_at.isoformat(),
        }
class ProjectRepository:
    def __init__(self) -> None:
        self._projects: dict[str, ProjectRecord] = {}
    def create(self, name: str, description: str | None = None) -> ProjectRecord:
        project = ProjectRecord(id=uuid4().hex, name=name, description=description)
        self._projects[project.id] = project
        return project
    def get(self, project_id: str) -> ProjectRecord | None:
        return self._projects.get(project_id)
    def list(self) -> list[ProjectRecord]:
        return list(self._projects.values())

