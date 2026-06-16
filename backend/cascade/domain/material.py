"""Material domain model."""
from __future__ import annotations
from dataclasses import dataclass, field
@dataclass(slots=True)
class Material:
    id: str
    name: str
    density: float | None = None
    composition: dict[str, float] = field(default_factory=dict)
    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "name": self.name,
            "density": self.density,
            "composition": dict(self.composition),
        }

