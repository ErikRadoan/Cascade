"""Backend profile domain model.

A BackendProfile is a named, saved execution backend configuration.
The user creates profiles once and references them by name when submitting jobs.

This is a pure data class — no imports from execution/, no behaviour.
The profile stores the backend type and config as a raw dict so it can
be persisted to a database without knowing about Pydantic models.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass(slots=True)
class BackendProfile:
    """A named, saved backend configuration.

    Attributes:
        name:        Unique identifier chosen by the user.
                     e.g. "local_docker", "metacentrum", "aws_batch"
        backend_type: One of "docker", "local", "slurm".
                     Determines which BackendConfig subclass config_data maps to.
        config_data: Raw dict of backend configuration fields.
                     Serialised form of DockerBackendConfig / SlurmBackendConfig etc.
                     Stored as a plain dict so domain has no execution dependency.
        description: Optional human-readable note from the user.
        created_at:  UTC timestamp of profile creation.
        updated_at:  UTC timestamp of last edit.
    """
    name:         str
    backend_type: str
    config_data:  dict
    description:  str | None                = None
    created_at:   datetime                  = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    updated_at:   datetime                  = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    def to_dict(self) -> dict:
        return {
            "name":         self.name,
            "backend_type": self.backend_type,
            "config_data":  self.config_data,
            "description":  self.description,
            "created_at":   self.created_at.isoformat(),
            "updated_at":   self.updated_at.isoformat(),
        }