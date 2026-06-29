"""Job domain model."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path

from .geometry import CascadeGeometry
from .material import Material
from .results_config import ResultsConfig


class JobStatus(StrEnum):
    QUEUED    = "queued"
    RUNNING   = "running"
    COMPLETED = "completed"
    FAILED    = "failed"
    CANCELLED = "cancelled"


@dataclass(slots=True)
class SimulationJob:
    """A single simulation job carrying everything needed to run it.

    The job is self-contained — the execution backend receives this object
    and has everything it needs to stage input files and submit the run.
    It never needs to query a database or service layer.

    Attributes:
        id:             Unique job identifier (UUID string).
        geometry:       Fully resolved CascadeGeometry for this job.
        materials:      All materials referenced by cells in geometry.
        param_values:   Sweep parameter values that produced this geometry.
                        Empty dict for single (non-sweep) jobs.
        backend:        Which execution backend to use ("docker", "local", "slurm").
        results_config: What to capture — tallies, mesh, spectra, diagnostics.
                        Translated to tallies.xml by the adapter at staging time.
        status:         Current lifecycle state.
        working_dir:    Where the backend stages input/output files.
        created_at:     UTC timestamp of job creation.
        started_at:     UTC timestamp when the backend began execution.
        finished_at:    UTC timestamp when the job completed or failed.
        error:          Error message if status is FAILED, else None.
        notes:          Optional human-readable label.
    """
    id:             str
    geometry:       CascadeGeometry
    materials:      list[Material]
    param_values:   dict[str, float | str]  = field(default_factory=dict)
    backend:        str                     = "docker"
    results_config: ResultsConfig           = field(default_factory=ResultsConfig.default)
    status:         JobStatus               = JobStatus.QUEUED
    working_dir:    Path | None             = None
    created_at:     datetime                = field(default_factory=lambda: datetime.now(timezone.utc))
    started_at:     datetime | None         = None
    finished_at:    datetime | None         = None
    error:          str | None              = None
    notes:          str | None              = None

    def input_dir(self) -> Path:
        if self.working_dir is None:
            raise RuntimeError(
                f"Job '{self.id}' has no working_dir set. "
                "JobService must set working_dir before calling submit()."
            )
        return self.working_dir / "input"

    def output_dir(self) -> Path:
        if self.working_dir is None:
            raise RuntimeError(f"Job '{self.id}' has no working_dir set.")
        return self.working_dir / "output"

    def to_dict(self) -> dict[str, object]:
        return {
            "id":             self.id,
            "geometry_id":    self.geometry.id,
            "param_values":   self.param_values,
            "backend":        self.backend,
            "results_config": self.results_config.to_dict(),
            "status":         self.status.value,
            "working_dir":    str(self.working_dir) if self.working_dir else None,
            "created_at":     self.created_at.isoformat(),
            "started_at":     self.started_at.isoformat() if self.started_at else None,
            "finished_at":    self.finished_at.isoformat() if self.finished_at else None,
            "error":          self.error,
            "notes":          self.notes,
        }