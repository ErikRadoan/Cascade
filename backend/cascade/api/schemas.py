"""Shared Pydantic request/response models for the Cascade API.

These are the HTTP-layer data contracts — separate from domain models.
Domain models (CascadeGeometry, SimulationJob, etc.) are internal.
These models are what FastAPI serializes to/from JSON.

Naming convention:
    *Request  — body of an incoming HTTP request
    *Response — body of an outgoing HTTP response
    *Summary  — lightweight list item (id + key fields, no nested objects)
    *Detail   — full object with all fields
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Shared primitives
# ---------------------------------------------------------------------------

class DeletedResponse(BaseModel):
    deleted: bool = True
    id: str


class ErrorResponse(BaseModel):
    error: str
    detail: str | None = None


# ---------------------------------------------------------------------------
# Geometry
# ---------------------------------------------------------------------------

class GeometryTextRequest(BaseModel):
    """Raw YAML text from the editor."""
    text: str = Field(..., description="YAML geometry definition text.")
    name: str | None = Field(None, description="Optional name for saving.")


class ValidationError(BaseModel):
    type:      str            # "yaml" | "structure" | "validation"
    message:   str
    component: str | None = None
    field:     str | None = None
    line:      int | None = None


class ValidationResponse(BaseModel):
    valid:  bool
    errors: list[ValidationError] = Field(default_factory=list)


class GeometrySummary(BaseModel):
    id:         str
    name:       str
    created_at: datetime
    n_surfaces: int
    n_cells:    int


class GeometryDetail(GeometrySummary):
    yaml_text:   str | None
    param_values: dict[str, float] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Scene (3D preview)
# ---------------------------------------------------------------------------

class SceneRequest(BaseModel):
    text: str = Field(..., description="YAML geometry definition text.")


class CylinderLayerOut(BaseModel):
    r_inner:     float
    r_outer:     float
    height:      float
    z_base:      float
    material_id: str
    color:       str
    opacity:     float
    label:       str


class WireframeBoxOut(BaseModel):
    x_size:           float
    y_size:           float
    z_size:           float
    z_base:           float
    color:            str
    boundary_type:    str
    fill_material_id: str
    fill_color:       str
    fill_opacity:     float


class SceneComponentOut(BaseModel):
    type:     str
    name:     str
    position: list[float]
    layers:   list[CylinderLayerOut] = Field(default_factory=list)
    box:      WireframeBoxOut | None = None


class BoundsOut(BaseModel):
    x_min: float
    x_max: float
    y_min: float
    y_max: float
    z_min: float
    z_max: float


class SceneResponse(BaseModel):
    components:      list[SceneComponentOut]
    material_colors: dict[str, str]
    bounds:          BoundsOut
    error:           str | None = None


# ---------------------------------------------------------------------------
# Materials
# ---------------------------------------------------------------------------

class MaterialCreateRequest(BaseModel):
    name:        str
    density:     float = Field(..., gt=0, description="Density in g/cm³.")
    composition: dict[str, float] = Field(
        ...,
        description="Nuclide name → atom fraction. e.g. {'U235': 0.03, 'U238': 0.97}",
    )


class MaterialSummary(BaseModel):
    id:   str
    name: str
    density: float | None


class MaterialDetail(MaterialSummary):
    composition: dict[str, float]


class MaterialImportResponse(BaseModel):
    imported: list[MaterialSummary]
    skipped:  list[str] = Field(default_factory=list)
    errors:   list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------

class JobSubmitRequest(BaseModel):
    """Submit a single simulation job."""
    geometry_text: str = Field(..., description="YAML geometry definition.")
    material_ids:  list[str] = Field(
        ...,
        description="Material IDs to include. Must exist in the material library.",
    )
    backend:       str  = Field("docker", description="Execution backend.")
    particles:     int  = Field(1000,  gt=0)
    inactive:      int  = Field(20,    gt=0)
    batches:       int  = Field(100,   gt=0)
    seed:          int  = Field(1)
    notes:         str | None = None


class SweepSubmitRequest(BaseModel):
    """Submit a parametric sweep — one job per parameter combination."""
    geometry_text: str
    material_ids:  list[str]
    backend:       str  = "docker"
    particles:     int  = Field(1000, gt=0)
    inactive:      int  = Field(20,   gt=0)
    batches:       int  = Field(100,  gt=0)
    seed:          int  = 1
    notes:         str | None = None


class JobSummary(BaseModel):
    id:           str
    status:       str
    backend:      str
    param_values: dict[str, float] = Field(default_factory=dict)
    created_at:   datetime
    notes:        str | None = None


class JobDetail(JobSummary):
    geometry_id:  str
    started_at:   datetime | None = None
    finished_at:  datetime | None = None
    error:        str | None = None
    working_dir:  str | None = None


class SweepResponse(BaseModel):
    sweep_id: str
    jobs:     list[JobSummary]
    total:    int


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------

class TallyResultOut(BaseModel):
    job_id:      str
    tally:       str
    value:       float
    uncertainty: float
    units:       str | None = None


class TallyResultSet(BaseModel):
    job_id:       str
    param_values: dict[str, float]
    tallies:      list[TallyResultOut]
    k_effective:  float | None = None
    k_uncertainty: float | None = None


class SweepResultsResponse(BaseModel):
    sweep_id: str
    points:   list[TallyResultSet]