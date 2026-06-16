"""Result domain model."""
from __future__ import annotations
from dataclasses import dataclass, field
@dataclass(slots=True)
class TallyResult:
    job_id: str
    tally: str
    value: float
    uncertainty: float = 0.0
    units: str | None = None
    metadata: dict[str, str] = field(default_factory=dict)
    def to_dict(self) -> dict[str, object]:
        return {
            "job_id": self.job_id,
            "tally": self.tally,
            "value": self.value,
            "uncertainty": self.uncertainty,
            "units": self.units,
            "metadata": dict(self.metadata),
        }

