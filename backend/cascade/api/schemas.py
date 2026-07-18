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

from datetime import datetime, timezone

from pydantic import BaseModel, Field, field_serializer


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
    # Join key for matching a /results/{job_id}/tallies entry's `name` to
    # this specific layer — see openmc_adapter.py's _append_scalar_tallies
    # and expander.py's _build_fuel_pin_cells.
    cell_name:   str = ""


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
    # Join key — see CylinderLayerOut.cell_name and expander.py's
    # _build_fill_cell.
    cell_name:        str = ""


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

def _utc_iso(dt: datetime | None) -> str | None:
    """Serialize a datetime as an unambiguous UTC ISO-8601 string.

    BUG FIX: job.created_at/started_at/finished_at are always written as
    datetime.now(timezone.utc) — the value is always a UTC instant. But
    whatever's actually stored in the DB round-trips back tzinfo-naive
    (this is the standard SQLite DateTime behavior — it doesn't persist
    tzinfo at all, and depending on the Postgres column type this can
    happen there too), so `dt.tzinfo` was already gone by the time this
    reached the response layer. Pydantic then serialized it as e.g.
    "2026-07-18T05:48:03" — no offset. Per the ISO-8601 / JS Date spec, a
    date-time string with no offset is parsed as LOCAL time, not UTC — so
    `new Date(...)` on the frontend silently treated an already-UTC wall
    clock value as if it were local, and every displayed timestamp came
    out however many hours the browser's timezone sits ahead of UTC (2h
    for CEST) earlier than reality — which is also why elapsed/"time ago"
    math looked broken, since `finished_at - started_at` was fine (both
    shifted equally) but anything comparing against "now" wasn't.

    Explicitly stamping UTC here — treating a naive value as UTC (which
    it always is) rather than converting an already-aware one — fixes
    this regardless of whatever tzinfo state the value survived the DB
    round-trip with.
    """
    if dt is None:
        return None
    dt = dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)
    return dt.isoformat().replace("+00:00", "Z")


class JobSummary(BaseModel):
    id:           str
    status:       str
    backend:      str
    param_values: dict[str, float] = Field(default_factory=dict)
    created_at:   datetime
    notes:        str | None = None
    # CHANGE: SimulationJob has always carried sweep_id (job_repository.py
    # persists/reloads it — see JobRow.sweep_id, list_by_sweep()), and
    # jobs.py's _to_summary()/_to_detail() have always passed it in, but
    # this field didn't exist here — Pydantic's default extra="ignore"
    # on unrecognized constructor kwargs silently dropped it from every
    # response instead of raising. Same root cause as the config fields
    # below.
    sweep_id:     str | None = None

    @field_serializer("created_at", when_used="json")
    def _serialize_created_at(self, v: datetime) -> str | None:
        return _utc_iso(v)


# ---------------------------------------------------------------------------
# Job submission config — response-side mirrors of the *Request models in
# api/jobs.py's JobSubmitRequest. Naming follows the existing *Out
# convention (see CylinderLayerOut / WireframeBoxOut above) rather than
# reusing the *Request classes directly, since those live in the router
# module and carry request-only concerns (defaults, to_domain()).
# ---------------------------------------------------------------------------

class McSettingsOut(BaseModel):
    particles: int
    batches:   int
    seed:      int
    inactive:  int | None = None


class SourceDefOut(BaseModel):
    particle:     str
    space_type:   str
    space_params: list[float]
    energy_mev:   float | None = None


class DepletionSettingsOut(BaseModel):
    power_W:    float
    timesteps:  list[float]
    chain_file: str
    integrator: str
    substeps:   int


class ScalarTallyOut(BaseModel):
    enabled:   bool
    scores:    list[str]
    all_cells: bool


class MeshTallyOut(BaseModel):
    enabled:   bool
    mesh_type: str
    nx: int
    ny: int
    nz: int
    nr: int
    nz_cyl: int
    scores: list[str]


class EnergySpectraOut(BaseModel):
    enabled:         bool
    group_structure: str
    per_material:    bool


class DiagnosticsOut(BaseModel):
    stochastic_volumes: bool
    particle_tracks:    bool
    n_tracks:            int


class ResultsConfigOut(BaseModel):
    particle_type: str
    scalars:       ScalarTallyOut
    mesh:          MeshTallyOut
    spectra:       EnergySpectraOut | None = None
    diagnostics:   DiagnosticsOut | None = None
    apply_dose_conversion: bool = False


class IrradiationScheduleOut(BaseModel):
    power_W:   float
    timesteps: list[float]


class ActivationSettingsOut(BaseModel):
    irradiation_schedule: IrradiationScheduleOut
    cooling_times:        list[float]
    decay_library:        str


class VrSettingsOut(BaseModel):
    weight_windows_enabled: bool


class R2SSettingsOut(BaseModel):
    neutron_leg_source: SourceDefOut
    neutron_leg_mc:     McSettingsOut
    activation:         ActivationSettingsOut
    photon_leg_mc:      McSettingsOut
    photon_leg_vr:      VrSettingsOut


class R2SResultsConfigOut(BaseModel):
    neutron_leg: ResultsConfigOut
    photon_leg:  ResultsConfigOut


class JobDetail(JobSummary):
    geometry_id:  str
    started_at:   datetime | None = None
    finished_at:  datetime | None = None
    error:        str | None = None
    working_dir:  str | None = None

    @field_serializer("started_at", "finished_at", when_used="json")
    def _serialize_timing(self, v: datetime | None) -> str | None:
        return _utc_iso(v)

    # CHANGE: submission-time configuration (job-settings-model.md §2).
    # SimulationJob has always carried run_mode/monte_carlo/source/
    # mode_specific/results_config, and JobRepository has always
    # persisted+reloaded them correctly (job_repository.py's JobRow /
    # _row_to_domain) — but this response model never declared the
    # fields, so _to_detail()'s construction silently dropped them the
    # same way sweep_id was dropped above. All optional: older rows may
    # be missing individual pieces, and the shape is mode-dependent
    # (r2s uses `r2s`/`r2s_results_config`; every other mode uses
    # `monte_carlo`/`source`/`depletion`/`results_config` — never both).
    run_mode:            str | None = None
    material_ids:        list[str] | None = None
    monte_carlo:         McSettingsOut | None = None
    source:              SourceDefOut | None = None
    depletion:           DepletionSettingsOut | None = None
    results_config:      ResultsConfigOut | None = None
    r2s:                 R2SSettingsOut | None = None
    r2s_results_config:  R2SResultsConfigOut | None = None

    # Per-leg pipeline progress for stepped jobs (depletion/r2s) — same
    # silent-drop bug as everything above: _to_detail() has always passed
    # this, JobDetail never declared it. Not yet consumed by the frontend;
    # included for completeness since job.steps was already being computed
    # here for nothing. Shape is JobStep.to_dict()'s output — untyped dict
    # rather than a dedicated Out model since job_step.py wasn't in scope
    # for this change.
    steps: list[dict] | None = None


class SweepResponse(BaseModel):
    sweep_id: str
    jobs:     list[JobSummary]
    total:    int