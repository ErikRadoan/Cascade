"""ORM table definitions.

One table per persisted domain concept:
    jobs            — SimulationJob records
    backend_profiles — named backend configuration profiles
    projects         — top-level project groupings (future)

All tables use string UUIDs as primary keys for portability.
JSON columns store structured data (config dicts, param_values)
using SQLAlchemy's built-in JSON type — works with both SQLite and Postgres.

These are persistence models only — domain objects are converted to/from
these rows by the repository classes. Nothing outside repositories/
should import from this file.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from .db import Base


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------

class JobRow(Base):
    """Persisted SimulationJob.

    geometry and materials are stored as JSON so we don't need
    separate geometry/material tables at this stage. Add foreign keys
    when those concepts get their own persistent storage.
    """

    __tablename__ = "jobs"

    id:             Mapped[str]           = mapped_column(String, primary_key=True)
    backend:        Mapped[str]           = mapped_column(String, nullable=False)
    status:         Mapped[str]           = mapped_column(String, nullable=False)
    param_values:   Mapped[dict]          = mapped_column(JSON,   nullable=False, default=dict)
    backend_config: Mapped[dict]          = mapped_column(JSON,   nullable=False, default=dict)
    geometry_json:  Mapped[dict]          = mapped_column(JSON,   nullable=False)
    materials_json: Mapped[list]          = mapped_column(JSON,   nullable=False)
    results_config: Mapped[dict]          = mapped_column(JSON,   nullable=False, default=dict)
    working_dir:    Mapped[str | None]    = mapped_column(String, nullable=True)
    notes:          Mapped[str | None]    = mapped_column(Text,   nullable=True)
    error:          Mapped[str | None]    = mapped_column(Text,   nullable=True)
    created_at:     Mapped[datetime]      = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    started_at:     Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at:    Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


# ---------------------------------------------------------------------------
# Backend profiles
# ---------------------------------------------------------------------------

class BackendProfileRow(Base):
    """Persisted BackendProfile — named execution backend configuration."""

    __tablename__ = "backend_profiles"

    name:         Mapped[str]          = mapped_column(String, primary_key=True)
    backend_type: Mapped[str]          = mapped_column(String, nullable=False)
    config_data:  Mapped[dict]         = mapped_column(JSON,   nullable=False)
    description:  Mapped[str | None]   = mapped_column(Text,   nullable=True)
    created_at:   Mapped[datetime]     = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at:   Mapped[datetime]     = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------

class ProjectRow(Base):
    """Top-level project grouping — associates jobs and geometries."""

    __tablename__ = "projects"

    id:          Mapped[str]          = mapped_column(String, primary_key=True)
    name:        Mapped[str]          = mapped_column(String, nullable=False)
    description: Mapped[str | None]   = mapped_column(Text,   nullable=True)
    created_at:  Mapped[datetime]     = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )


# ---------------------------------------------------------------------------
# Sweeps
# ---------------------------------------------------------------------------

class SweepRow(Base):
    """A parametric sweep — groups related jobs sharing a geometry template.

    Stores everything needed to reconstruct the sweep after a server restart:
    the original YAML, which parameters were swept, and the resulting job IDs.
    Status is derived at read time from child job statuses — not stored.
    """

    __tablename__ = "sweeps"

    sweep_id:      Mapped[str]  = mapped_column(String, primary_key=True)
    job_ids:       Mapped[list] = mapped_column(JSON, nullable=False, default=list)

    # Original YAML text — stored so the frontend can show what was swept
    geometry_text: Mapped[str]  = mapped_column(Text, nullable=False)

    # Which parameters were swept and their full value lists.
    # e.g. {"fuel_pin.pellet_radius": [0.38, 0.39, 0.40]}
    swept_params:  Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    # Human-readable label from the submit request
    notes:         Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at:    Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )