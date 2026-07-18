"""Jobs routes — submit, monitor, and cancel simulation jobs."""

from __future__ import annotations

import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field, model_validator
from sqlalchemy.orm import Session

from ..repositories.db import get_db
from ..repositories.job_repository import JobRepository
from ..repositories.sweep_repository import SweepRepository
from ..domain.geometry import region_to_json
from ..domain.job import JobStatus, SimulationJob
from ..domain.results_config import (
    DiagnosticsConfig,
    EnergyGroupStructure,
    EnergySpectraConfig,
    MeshTallyConfig,
    MeshType,
    ParticleType,
    R2SResultsConfig,
    ResultsConfig,
    ScalarTallyConfig,
    SimulationSummaryConfig,
    TallyScore,
)
from ..domain.run_settings import (
    ActivationSettings,
    DepletionSettings,
    IrradiationSchedule,
    McSettings,
    R2SSettings,
    RunMode,
    SourceDef,
    SourceSpaceType,
    VrSettings,
)
from ..dsl import loader, expander
from ..dsl.sweep import expand_sweep, parse_sweep, validate_preview
from ..execution.backend_config import (
    BackendConfig,
    DockerBackendConfig,
    create_backend,
)
from ..domain.paths import JOBS_BASE_DIR, UPLOADS_DIR
from .schemas import (
    ActivationSettingsOut,
    DeletedResponse,
    DiagnosticsOut,
    EnergySpectraOut,
    IrradiationScheduleOut,
    JobDetail,
    JobSummary,
    McSettingsOut,
    MeshTallyOut,
    R2SResultsConfigOut,
    R2SSettingsOut,
    ResultsConfigOut,
    ScalarTallyOut,
    SceneResponse,
    SourceDefOut,
    SweepResponse,
    VrSettingsOut, DepletionSettingsOut,
)
from .geometry import build_scene_response

router = APIRouter(prefix="/jobs", tags=["jobs"])

_DEFAULT_BACKEND_CONFIG = DockerBackendConfig(
    cli="podman",
    image="cascade-openmc:latest",
    openmc_bin="/opt/miniconda/envs/openmc/bin/openmc",
    nuclear_data_path=str(Path.home() / ".cascade" / "data"),
    nuclear_data_container_path="/nuclear-data",
    jobs_base_dir=str(JOBS_BASE_DIR),
)


# ---------------------------------------------------------------------------
# Request models — Monte Carlo / source / mode-specific settings
#
# These mirror domain/run_settings.py at the Pydantic layer (request
# validation) — see that module's docstring for the reasoning behind the
# per-leg, per-mode shape. Each *Request model carries a to_domain() that
# bridges to the matching frozen dataclass.
# ---------------------------------------------------------------------------

class McSettingsRequest(BaseModel):
    """Particle/batch/seed settings for ONE transport leg.

    `inactive` is optional and mode-dependent (see RunMode docs on
    JobSubmitRequest below) — sending it for a fixed-source leg is now a
    422 validation error instead of a silently-ignored field.
    """
    particles: int       = Field(1000, gt=0)
    batches:   int        = Field(100, gt=0)
    seed:      int        = 1
    inactive:  int | None = Field(None, gt=0)

    def to_domain(self) -> McSettings:
        return McSettings(
            particles=self.particles, batches=self.batches,
            seed=self.seed, inactive=self.inactive,
        )


class SourceDefRequest(BaseModel):
    """Required for fixed_source mode and r2s's neutron leg
    (job-settings-model.md §3.2 — previously impossible to submit)."""
    particle:     Literal["neutron", "photon"]
    space_type:   Literal["point", "box"] = "point"
    space_params: list[float]             = Field(default_factory=lambda: [0.0, 0.0, 0.0])
    energy_mev:   float | None            = None

    def to_domain(self) -> SourceDef:
        return SourceDef(
            particle=self.particle,
            space_type=self.space_type,
            space_params=tuple(self.space_params),
            energy_mev=self.energy_mev,
        )


class DepletionSettingsRequest(BaseModel):
    power_W:    float        = Field(..., gt=0)
    timesteps:  list[float]  = Field(..., min_length=1)
    chain_file: str
    integrator: str          = "predictor"
    substeps:   int          = Field(1, gt=0)

    def to_domain(self) -> DepletionSettings:
        return DepletionSettings(
            power_W=self.power_W, timesteps=self.timesteps,
            chain_file=self.chain_file, integrator=self.integrator,
            substeps=self.substeps,
        )


class IrradiationScheduleRequest(BaseModel):
    power_W:   float       = Field(..., gt=0)
    timesteps: list[float] = Field(..., min_length=1)

    def to_domain(self) -> IrradiationSchedule:
        return IrradiationSchedule(power_W=self.power_W, timesteps=self.timesteps)


class ActivationSettingsRequest(BaseModel):
    irradiation_schedule: IrradiationScheduleRequest
    cooling_times:        list[float] = Field(..., min_length=1)
    decay_library:        str

    def to_domain(self) -> ActivationSettings:
        return ActivationSettings(
            irradiation_schedule=self.irradiation_schedule.to_domain(),
            cooling_times=self.cooling_times,
            decay_library=self.decay_library,
        )


class VrSettingsRequest(BaseModel):
    weight_windows_enabled: bool = False

    def to_domain(self) -> VrSettings:
        return VrSettings(weight_windows_enabled=self.weight_windows_enabled)


class R2SSettingsRequest(BaseModel):
    neutron_leg_source: SourceDefRequest
    neutron_leg_mc:     McSettingsRequest
    activation:         ActivationSettingsRequest
    photon_leg_mc:      McSettingsRequest
    photon_leg_vr:      VrSettingsRequest = Field(default_factory=VrSettingsRequest)

    def to_domain(self) -> R2SSettings:
        return R2SSettings(
            neutron_leg_source=self.neutron_leg_source.to_domain(),
            neutron_leg_mc=self.neutron_leg_mc.to_domain(),
            activation=self.activation.to_domain(),
            photon_leg_mc=self.photon_leg_mc.to_domain(),
            photon_leg_vr=self.photon_leg_vr.to_domain(),
        )


# ---------------------------------------------------------------------------
# Request models — results capture
# ---------------------------------------------------------------------------

class ScalarTallyRequest(BaseModel):
    """Controls per-cell scalar tallies (flux, fission, absorption, heating)."""
    enabled:   bool            = True
    scores:    list[str]       = Field(
        default=["flux", "fission", "absorption", "heating"],
        description="TallyScore values to measure per cell. Valid values "
                     "depend on the leg's particle_type — see ResultsConfigRequest.",
    )
    all_cells: bool            = False


class MeshTallyRequest(BaseModel):
    """Controls the 3-D mesh tally (power/flux map)."""
    enabled:   bool       = False
    mesh_type: str        = "regular"   # "regular" | "cylindrical"
    nx:        int        = Field(20, gt=0)
    ny:        int        = Field(20, gt=0)
    nz:        int        = Field(20, gt=0)
    nr:        int        = Field(20, gt=0)   # cylindrical only
    nz_cyl:    int        = Field(20, gt=0)   # cylindrical only
    scores:    list[str]  = Field(default=["flux", "fission", "heating-local"])


class EnergySpectraRequest(BaseModel):
    """Controls flux-vs-energy spectra per material region. Neutron legs only
    — see ResultsConfigRequest.to_domain(), which rejects this for a photon leg."""
    enabled:         bool = False
    group_structure: str  = "69"    # "33" | "69" | "252"
    per_material:    bool = True


class DiagnosticsRequest(BaseModel):
    stochastic_volumes: bool = False
    particle_tracks:    bool = False
    n_tracks:           int  = Field(100, gt=0)


class ResultsConfigRequest(BaseModel):
    """What to ask OpenMC to capture, for ONE transport leg.

    `particle_type` (new) scopes which scores/group structures are valid —
    see domain/results_config.py §5's ParticleType restructure. Defaults
    to "neutron" so existing eigenvalue/fixed_source callers are unaffected.
    """
    particle_type: Literal["neutron", "photon"] = "neutron"
    scalars:     ScalarTallyRequest   = Field(default_factory=ScalarTallyRequest)
    mesh:        MeshTallyRequest     = Field(default_factory=MeshTallyRequest)
    spectra:     EnergySpectraRequest = Field(default_factory=EnergySpectraRequest)
    diagnostics: DiagnosticsRequest   = Field(default_factory=DiagnosticsRequest)
    apply_dose_conversion: bool       = False

    def to_domain(self) -> ResultsConfig:
        """Convert Pydantic request model → domain ResultsConfig.

        Raises pydantic.ValidationError-adjacent ValueError (converted to a
        422 by the route handler) if scores/group-structures are invalid
        for `particle_type` — this now fails at the API boundary instead of
        silently generating nonsensical tallies.xml downstream.
        """
        return ResultsConfig(
            particle_type=ParticleType(self.particle_type),
            summary=SimulationSummaryConfig(),  # always on
            scalars=ScalarTallyConfig(
                enabled=self.scalars.enabled,
                scores=[TallyScore(s) for s in self.scalars.scores],
                all_cells=self.scalars.all_cells,
            ),
            mesh=MeshTallyConfig(
                enabled=self.mesh.enabled,
                mesh_type=MeshType(self.mesh.mesh_type),
                nx=self.mesh.nx,
                ny=self.mesh.ny,
                nz=self.mesh.nz,
                nr=self.mesh.nr,
                nz_cyl=self.mesh.nz_cyl,
                scores=[TallyScore(s) for s in self.mesh.scores],
            ),
            spectra=EnergySpectraConfig(
                enabled=self.spectra.enabled,
                group_structure=EnergyGroupStructure(self.spectra.group_structure),
                per_material=self.spectra.per_material,
            ),
            diagnostics=DiagnosticsConfig(
                stochastic_volumes=self.diagnostics.stochastic_volumes,
                particle_tracks=self.diagnostics.particle_tracks,
                n_tracks=self.diagnostics.n_tracks,
            ),
            apply_dose_conversion=self.apply_dose_conversion,
        )


class R2SResultsConfigRequest(BaseModel):
    """r2s's per-leg results config — job-settings-model.md §1's core fix.

    Never a single ResultsConfigRequest for r2s. `neutron_leg`/`photon_leg`
    each carry their own `particle_type`, which is enforced (not just
    defaulted) in to_domain().
    """
    neutron_leg: ResultsConfigRequest
    photon_leg:  ResultsConfigRequest

    def to_domain(self) -> R2SResultsConfig:
        neutron_leg = self.neutron_leg.model_copy(update={"particle_type": "neutron"})
        photon_leg  = self.photon_leg.model_copy(update={"particle_type": "photon"})
        return R2SResultsConfig(
            neutron_leg=neutron_leg.to_domain(),
            photon_leg=photon_leg.to_domain(),
        )


# ---------------------------------------------------------------------------
# Request models — top-level job/sweep submission
# ---------------------------------------------------------------------------

class JobSubmitRequest(BaseModel):
    """Submission payload for a single job. Shape depends on `run_mode`
    (job-settings-model.md §2's field-requirements matrix):

        eigenvalue:   monte_carlo (with inactive) + results_config.
        fixed_source: monte_carlo (no inactive) + source + results_config.
        depletion:    monte_carlo (with inactive, applies per timestep)
                      + depletion + results_config.
        r2s:          r2s (per-leg settings) + r2s_results_config.
                      monte_carlo/source/results_config/depletion must be
                      unset — see validate_shape().

    CHANGE: `run_mode` did not exist on this model before — the frontend
    sent it, FastAPI/Pydantic silently dropped it as an unknown field, and
    every job ran as whatever OpenMCRunSettings() defaulted to. It's a
    required field now.
    """
    geometry_text:  str
    material_ids:   list[str]
    backend_config: BackendConfig = Field(default=_DEFAULT_BACKEND_CONFIG)

    run_mode:       Literal["eigenvalue", "fixed_source", "depletion", "r2s"]
    monte_carlo:    McSettingsRequest | None       = None
    source:         SourceDefRequest | None        = None
    depletion:      DepletionSettingsRequest | None = None
    r2s:            R2SSettingsRequest | None       = None
    results_config: ResultsConfigRequest | None     = None
    r2s_results_config: R2SResultsConfigRequest | None = None

    notes: str | None = None

    @model_validator(mode="after")
    def validate_shape(self) -> "JobSubmitRequest":
        """job-settings-model.md §2: which fields are required/forbidden
        per run_mode. This is what makes 'r2s + inactive batches' a 422 at
        the API boundary instead of a UI affordance that reaches the backend."""
        mode = self.run_mode

        if mode == "r2s":
            if self.r2s is None:
                raise ValueError("run_mode='r2s' requires `r2s` settings.")
            if self.r2s_results_config is None:
                raise ValueError("run_mode='r2s' requires `r2s_results_config` (per-leg).")
            for forbidden, name in (
                (self.monte_carlo, "monte_carlo"),
                (self.source, "source"),
                (self.depletion, "depletion"),
                (self.results_config, "results_config"),
            ):
                if forbidden is not None:
                    raise ValueError(
                        f"run_mode='r2s' must not set `{name}` — r2s has two "
                        f"independent legs, set per-leg fields under `r2s` "
                        f"and `r2s_results_config` instead."
                    )
            return self

        # eigenvalue / fixed_source / depletion all need a single-leg
        # monte_carlo + results_config, and must not set r2s fields.
        if self.monte_carlo is None:
            raise ValueError(f"run_mode='{mode}' requires `monte_carlo`.")
        if self.results_config is None:
            raise ValueError(f"run_mode='{mode}' requires `results_config`.")
        if self.r2s is not None or self.r2s_results_config is not None:
            raise ValueError(f"run_mode='{mode}' must not set r2s fields.")

        if mode == "eigenvalue":
            if self.source is not None:
                raise ValueError(
                    "run_mode='eigenvalue' source is geometry-driven "
                    "(auto-detected from fissile cells) — do not set `source`."
                )
            if self.depletion is not None:
                raise ValueError("run_mode='eigenvalue' must not set `depletion`.")

        elif mode == "fixed_source":
            if self.source is None:
                raise ValueError(
                    "run_mode='fixed_source' requires `source` "
                    "(job-settings-model.md §3.2)."
                )
            if self.depletion is not None:
                raise ValueError("run_mode='fixed_source' must not set `depletion`.")

        elif mode == "depletion":
            if self.depletion is None:
                raise ValueError("run_mode='depletion' requires `depletion` settings.")
            if self.source is not None:
                raise ValueError("run_mode='depletion' must not set `source`.")

        return self

    def to_domain_kwargs(self) -> dict:
        """Fields to splat into SimulationJob(...), independent of geometry/
        materials/backend/job_id which the route handler resolves separately."""
        mode = self.run_mode  # RunMode's string constants match these literals exactly
        if self.run_mode == "r2s":
            return dict(
                run_mode=RunMode.R2S,
                mode_specific=self.r2s.to_domain(),
                results_config=self.r2s_results_config.to_domain(),
            )
        mode_specific = self.depletion.to_domain() if self.run_mode == "depletion" else None
        return dict(
            run_mode=mode,
            monte_carlo=self.monte_carlo.to_domain(),
            source=self.source.to_domain() if self.source else None,
            mode_specific=mode_specific,
            results_config=self.results_config.to_domain(),
        )


class SweepSubmitRequest(JobSubmitRequest):
    """Identical shape to JobSubmitRequest — a sweep is the same per-job
    settings applied across every point in the parameter sweep. Subclassing
    means the two can never drift apart the way the old flat field lists did."""
    pass


# ---------------------------------------------------------------------------
# Pure helpers — no DB access, no Depends
# ---------------------------------------------------------------------------

def _to_summary(job: SimulationJob) -> JobSummary:
    return JobSummary(
        id=job.id,
        status=job.effective_status().value,
        backend=job.backend,
        param_values=job.param_values,
        created_at=job.created_at,
        notes=job.notes,
        sweep_id=job.sweep_id,
    )


# ---------------------------------------------------------------------------
# Domain -> response conversion for submission config (job-settings-model.md
# §2). These mirror the *Request.to_domain() methods above, just inverted —
# each one is None-safe on its input since every sub-config here is
# optional/mode-dependent on SimulationJob.
#
# `.value if hasattr(..., "value") else ...` on enum-shaped fields (e.g.
# particle_type, mesh_type, scores) is deliberately defensive rather than
# assuming a specific enum type: ResultsConfig.from_dict() (domain-internal,
# not this module's concern) is what actually reconstructs those on load,
# and whether it hands back enum members or plain strings isn't a contract
# this response layer should have to track.
# ---------------------------------------------------------------------------

def _enum_value(v):
    return v.value if hasattr(v, "value") else v


def _mc_out(mc) -> McSettingsOut | None:
    if mc is None:
        return None
    return McSettingsOut(
        particles=mc.particles, batches=mc.batches,
        seed=mc.seed, inactive=mc.inactive,
    )


def _source_out(source) -> SourceDefOut | None:
    if source is None:
        return None
    return SourceDefOut(
        particle=_enum_value(source.particle),
        space_type=_enum_value(source.space_type),
        space_params=list(source.space_params),
        energy_mev=source.energy_mev,
    )


def _depletion_out(depletion) -> DepletionSettingsOut | None:
    if depletion is None:
        return None
    return DepletionSettingsOut(
        power_W=depletion.power_W, timesteps=list(depletion.timesteps),
        chain_file=depletion.chain_file, integrator=depletion.integrator,
        substeps=depletion.substeps,
    )


def _results_config_out(rc) -> ResultsConfigOut | None:
    if rc is None:
        return None
    return ResultsConfigOut(
        particle_type=_enum_value(rc.particle_type),
        scalars=ScalarTallyOut(
            enabled=rc.scalars.enabled,
            scores=[_enum_value(s) for s in rc.scalars.scores],
            all_cells=rc.scalars.all_cells,
        ),
        mesh=MeshTallyOut(
            enabled=rc.mesh.enabled,
            mesh_type=_enum_value(rc.mesh.mesh_type),
            nx=rc.mesh.nx, ny=rc.mesh.ny, nz=rc.mesh.nz,
            nr=rc.mesh.nr, nz_cyl=rc.mesh.nz_cyl,
            scores=[_enum_value(s) for s in rc.mesh.scores],
        ),
        spectra=EnergySpectraOut(
            enabled=rc.spectra.enabled,
            group_structure=_enum_value(rc.spectra.group_structure),
            per_material=rc.spectra.per_material,
        ) if rc.spectra is not None else None,
        diagnostics=DiagnosticsOut(
            stochastic_volumes=rc.diagnostics.stochastic_volumes,
            particle_tracks=rc.diagnostics.particle_tracks,
            n_tracks=rc.diagnostics.n_tracks,
        ) if rc.diagnostics is not None else None,
        apply_dose_conversion=rc.apply_dose_conversion,
    )


def _r2s_settings_out(r2s) -> R2SSettingsOut | None:
    if r2s is None:
        return None
    return R2SSettingsOut(
        neutron_leg_source=_source_out(r2s.neutron_leg_source),
        neutron_leg_mc=_mc_out(r2s.neutron_leg_mc),
        activation=ActivationSettingsOut(
            irradiation_schedule=IrradiationScheduleOut(
                power_W=r2s.activation.irradiation_schedule.power_W,
                timesteps=list(r2s.activation.irradiation_schedule.timesteps),
            ),
            cooling_times=list(r2s.activation.cooling_times),
            decay_library=r2s.activation.decay_library,
        ),
        photon_leg_mc=_mc_out(r2s.photon_leg_mc),
        photon_leg_vr=VrSettingsOut(
            weight_windows_enabled=r2s.photon_leg_vr.weight_windows_enabled,
        ),
    )


def _r2s_results_config_out(rc) -> R2SResultsConfigOut | None:
    if rc is None:
        return None
    return R2SResultsConfigOut(
        neutron_leg=_results_config_out(rc.neutron_leg),
        photon_leg=_results_config_out(rc.photon_leg),
    )


def _to_detail(job: SimulationJob) -> JobDetail:
    is_r2s = job.run_mode == RunMode.R2S

    return JobDetail(
        id=job.id,
        status=job.effective_status().value,
        backend=job.backend,
        param_values=job.param_values,
        created_at=job.created_at,
        notes=job.notes,
        geometry_id=job.geometry.id,
        started_at=job.started_at,
        finished_at=job.finished_at,
        error=job.error,
        working_dir=str(job.working_dir) if job.working_dir else None,
        sweep_id=job.sweep_id,
        steps=[s.to_dict() for s in job.steps],
        # --- submission config — see block comment above _mc_out() ---
        run_mode=_enum_value(job.run_mode),
        material_ids=[m.id for m in job.materials],
        monte_carlo=_mc_out(job.monte_carlo),
        source=_source_out(job.source),
        depletion=_depletion_out(job.mode_specific) if job.run_mode == RunMode.DEPLETION else None,
        results_config=None if is_r2s else _results_config_out(job.results_config),
        r2s=_r2s_settings_out(job.mode_specific) if is_r2s else None,
        r2s_results_config=_r2s_results_config_out(job.results_config) if is_r2s else None,
    )


def _resolve_materials(material_ids: list[str]):
    from .materials import get_materials_by_ids
    return get_materials_by_ids(material_ids)


def _extract_swept_params(geometry_text: str) -> dict[str, list[float]]:
    """Parse sweep(...) expressions from YAML, returning param name -> value list."""
    import yaml as _yaml
    try:
        raw = _yaml.safe_load(geometry_text)
    except Exception:
        return {}

    result: dict[str, list[float]] = {}
    for comp_name, comp_data in raw.items():
        if not isinstance(comp_data, dict):
            continue
        for field_name, field_value in comp_data.items():
            if field_name == "type":
                continue
            values = parse_sweep(field_value)
            if values is not None:
                result[f"{comp_name}.{field_name}"] = values
    return result


# ---------------------------------------------------------------------------
# DB helpers — these are called from route handlers that already hold a
# session, so they accept db as a parameter rather than opening their own.
# The two exceptions (_backend_for_job, _get_job_or_404) are called from
# within route handlers *after* the injected session may have closed, so
# they open their own short-lived sessions via SessionLocal().
# ---------------------------------------------------------------------------

def _backend_for_job(job_id: str, db: Session):
    """Reconstruct the correct backend for a job using its stored config.

    Accepts the caller's session so we never open a competing SessionLocal()
    while the outer session is still active — that caused SQLite 'database is
    locked' errors which were swallowed by the bare except, leaving jobs stuck
    in RUNNING forever.
    """
    raw_config = JobRepository(db).get_backend_config(job_id)

    if raw_config is None:
        raise HTTPException(
            status_code=500,
            detail=f"No backend config found for job '{job_id}'.",
        )
    try:
        from pydantic import TypeAdapter
        config = TypeAdapter(BackendConfig).validate_python(raw_config)
        return create_backend(config)
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Could not reconstruct backend for job '{job_id}': {e}",
        )


def _get_job_or_404(job_id: str, db: Session) -> SimulationJob:
    """Fetch a job from the DB or raise 404."""
    job = JobRepository(db).get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found.")
    return job


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post("/files")
async def upload_job_file(
    file: UploadFile = File(...),
    kind: str = Form(...),
) -> dict:
    """Accept a file upload from the browser's native file picker.

    Used for depletion chain files and r2s decay/activation libraries
    (see JobSubmitModal.svelte — the file input's onchange handler calls
    api.jobs.uploadFile() immediately on selection, before the job is
    submitted). Returns a `file_id` that the frontend stores as the
    `chain_file`/`decay_library` string value in the submission payload;
    DockerBackend resolves that reference back to actual file bytes at
    staging time via `_stage_uploaded_file()`, copying them directly into
    the relevant execution step's input_dir (the same directory mounted
    into the container at /work) — no separate host-path volume mount
    needed, unlike the provisional approach this replaces.

    `kind` ("chain" | "decay_library") isn't currently used to change
    storage behavior — both are staged identically — but is accepted and
    could later gate validation (e.g. requiring a .xml extension for
    chain files) without a client-side contract change.

    Returns:
        {"file_id": "<uuid>", "filename": "<original filename>"}
        Store the two joined as "{file_id}/{filename}" wherever a
        DepletionSettings.chain_file / ActivationSettings.decay_library
        string reference is expected.
    """
    file_id = str(uuid.uuid4())
    dest_dir = UPLOADS_DIR / file_id
    dest_dir.mkdir(parents=True, exist_ok=True)

    dest_path = dest_dir / file.filename
    with dest_path.open("wb") as f:
        shutil.copyfileobj(file.file, f)

    return {"file_id": file_id, "filename": file.filename}


@router.post(
    "/submit",
    response_model=JobSummary | SweepResponse,
    status_code=202,
)
async def submit_job(
    body: JobSubmitRequest,
    db: Session = Depends(get_db),
):
    swept_params = _extract_swept_params(body.geometry_text)

    if swept_params:
        # SweepSubmitRequest is a subclass of JobSubmitRequest with an
        # identical shape, so this is a straight passthrough — no more
        # hand-copying each individual field (and no more risk of the two
        # request shapes drifting apart, which is how `run_mode` got lost
        # here in the first place).
        sweep_request = SweepSubmitRequest(**body.model_dump())
        return await submit_sweep(sweep_request, db)

    errors = loader.validate(body.geometry_text)
    if errors:
        raise HTTPException(status_code=422, detail=errors)

    schemas = loader.load(body.geometry_text)
    try:
        geometry = expander.expand(schemas)
    except Exception as e:
        raise HTTPException(status_code=422, detail=str(e))

    materials = _resolve_materials(body.material_ids)
    backend   = create_backend(body.backend_config)

    job_id = str(uuid.uuid4())
    try:
        job = SimulationJob(
            id=job_id,
            geometry=geometry,
            geometry_text=body.geometry_text,
            materials=materials,
            param_values={},
            backend=body.backend_config.type,
            working_dir=Path(body.backend_config.jobs_base_dir) / job_id,
            notes=body.notes,
            **body.to_domain_kwargs(),
        )
    except ValueError as e:
        # Domain-level cross-field validation (job-settings-model.md §6) —
        # e.g. fission scores requested without a fissile material, or
        # eigenvalue mode on a geometry with no fissile cells at all.
        raise HTTPException(status_code=422, detail=str(e))

    try:
        job = backend.submit(job)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Submission failed: {e}")

    JobRepository(db).save(job, body.backend_config.model_dump())
    return _to_summary(job)


@router.post("/sweep", response_model=SweepResponse, status_code=202)
async def submit_sweep(
    body: SweepSubmitRequest,
    db:   Session = Depends(get_db),
) -> SweepResponse:
    """Submit a parametric sweep — one job per parameter combination."""
    errors = validate_preview(body.geometry_text)
    if errors:
        raise HTTPException(status_code=422, detail=errors)

    try:
        sweep_points = expand_sweep(body.geometry_text)
    except Exception as e:
        raise HTTPException(status_code=422, detail=str(e))

    if not sweep_points:
        raise HTTPException(
            status_code=422,
            detail="No sweep parameters found. Add sweep(...) expressions to the geometry.",
        )

    materials    = _resolve_materials(body.material_ids)
    backend      = create_backend(body.backend_config)
    sweep_id     = str(uuid.uuid4())
    raw_config   = body.backend_config.model_dump()
    swept_params = _extract_swept_params(body.geometry_text)
    job_repo     = JobRepository(db)

    job_ids:   list[str]        = []
    summaries: list[JobSummary] = []

    for param_values, geometry in sweep_points:
        job_id = str(uuid.uuid4())
        try:
            job = SimulationJob(
                id=job_id,
                geometry=geometry,
                # NOTE: this is the sweep *template* (with sweep(...) exprs),
                # the same text for every point — there's no per-point
                # resolved YAML available here, only the resolved
                # CascadeGeometry (`geometry`, already-expanded surfaces/
                # cells, not schema form). /jobs/{id}/scene will render the
                # template's nominal preview for any job in this sweep, not
                # that job's actual swept dimensions. Good enough for "what
                # does the geometry look like", not for per-point accuracy.
                geometry_text=body.geometry_text,
                materials=materials,
                param_values=param_values,
                backend=body.backend_config.type,
                working_dir=Path(body.backend_config.jobs_base_dir) / job_id,
                notes=body.notes,
                sweep_id=sweep_id,
                **body.to_domain_kwargs(),
            )
        except ValueError as e:
            # e.g. this sweep point's geometry has no fissile material for
            # eigenvalue mode — fail just this point, not the whole sweep.
            import datetime as _dt
            summaries.append(JobSummary(
                id=job_id, status=JobStatus.FAILED.value, backend=body.backend_config.type,
                param_values=param_values, created_at=_dt.datetime.now(_dt.timezone.utc),
                notes=f"Rejected: {e}", sweep_id=sweep_id,
            ))
            job_ids.append(job_id)
            continue

        try:
            job = backend.submit(job)
        except Exception as e:
            job.status = JobStatus.FAILED
            job.error  = str(e)

        job_repo.save(job, raw_config)
        job_ids.append(job_id)
        summaries.append(_to_summary(job))

    SweepRepository(db).save(
        sweep_id=      sweep_id,
        job_ids=       job_ids,
        geometry_text= body.geometry_text,
        swept_params=  swept_params,
        notes=         body.notes,
    )

    return SweepResponse(sweep_id=sweep_id, jobs=summaries, total=len(summaries))


@router.get("/sweeps", response_model=list[dict])
async def list_sweeps(db: Session = Depends(get_db)) -> list[dict]:
    """List all parametric sweeps, most recent first."""
    return [r.to_dict() for r in SweepRepository(db).list()]


@router.get("/sweeps/{sweep_id}", response_model=dict)
async def get_sweep(
    sweep_id: str,
    db:       Session = Depends(get_db),
) -> dict:
    """Get sweep metadata, derived status, and per-job results summaries.

    CHANGE: previously returned only `record.to_dict()` — sweep metadata
    plus a bare `job_ids` list, with no way to render per-job results
    (status, param_values, results_config) without N follow-up requests to
    GET /jobs/{job_id}. Now attaches full job summaries via
    JobRepository.list_by_sweep(), which is possible because job.sweep_id
    is now actually persisted (previously sweep_id was generated at submit
    time but never attached to the job record at all — see job.py's
    `sweep_id` field and job_repository.py's `list_by_sweep()`).
    """
    record = SweepRepository(db).get(sweep_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Sweep '{sweep_id}' not found.")
    jobs = JobRepository(db).list_by_sweep(sweep_id)
    return {
        **record.to_dict(),
        "jobs": [_to_summary(j).model_dump(mode="json") for j in jobs],
    }


@router.delete("/sweeps/{sweep_id}", response_model=DeletedResponse)
async def delete_sweep(
    sweep_id:    str,
    delete_jobs: bool    = False,
    db:          Session = Depends(get_db),
) -> DeletedResponse:
    """Delete a sweep record.

    Query params:
        delete_jobs: Also delete child job records and working directories.
    """
    deleted = SweepRepository(db).delete(sweep_id, delete_jobs=delete_jobs)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Sweep '{sweep_id}' not found.")
    return DeletedResponse(id=sweep_id)


@router.get("/backends/available")
async def list_available_backends() -> list[dict]:
    """List available backend types with their configuration schemas."""
    from ..execution.backend_config import (
        DockerBackendConfig, LocalBackendConfig, SlurmBackendConfig,
    )
    return [
        {
            "type":        "docker",
            "label":       "Docker / Podman (local container)",
            "description": "Run simulations in a local container. Best for development.",
            "schema":      DockerBackendConfig.model_json_schema(),
            "default":     DockerBackendConfig().model_dump(),
        },
        {
            "type":        "local",
            "label":       "Local process (OpenMC installed directly)",
            "description": "Run OpenMC directly. Requires OpenMC on PATH or at openmc_bin.",
            "schema":      LocalBackendConfig.model_json_schema(),
            "default":     LocalBackendConfig().model_dump(),
        },
        {
            "type":        "slurm",
            "label":       "SLURM HPC cluster",
            "description": "Submit via SSH to a university cluster. Supports MetaCentrum.",
            "schema":      SlurmBackendConfig.model_json_schema(),
            "default":     None,
        },
    ]


@router.get("/", response_model=list[JobSummary])
async def list_jobs(db: Session = Depends(get_db)) -> list[JobSummary]:
    """List all jobs, most recent first."""
    return [_to_summary(j) for j in JobRepository(db).list()]


@router.get("/{job_id}", response_model=JobDetail)
async def get_job(
    job_id: str,
    db:     Session = Depends(get_db),
) -> JobDetail:
    """Get current status and detail for a job.

    Polls the correct backend if still running, then persists any status change.

    BUG FIX: previously gated on `job.status == JobStatus.RUNNING` — the
    raw field, which stepped jobs (depletion/r2s) never set at all (only
    `job.steps` is populated/mutated for those; `job.effective_status()`
    derives RUNNING from step state instead). That meant `backend.status()`
    was never called again after the first poll for any depletion/r2s job
    — its step state, and therefore its displayed status, froze at
    whatever it was right after submission and never advanced, regardless
    of what actually happened in the container. Fixed to check
    `effective_status()`, which is correct for both legacy single-leg jobs
    (falls back to the raw field) and stepped jobs (derives from steps).

    Also fixed: previously only persisted when the *overall* derived
    status changed. A stepped job can move from one running step to the
    next (e.g. r2s's neutron leg finishing and activation starting) while
    effective_status() stays RUNNING throughout that transition — under
    the old `if new_status != old_status` gate, that step progress was
    silently dropped instead of saved. Now always persists (including
    `job.steps`) after every poll where backend.status() was actually
    called.
    """
    job = _get_job_or_404(job_id, db)

    if job.effective_status() == JobStatus.RUNNING:
        try:
            backend    = _backend_for_job(job_id, db)
            new_status = backend.status(job)   # mutates job.status and/or job.steps in-place

            if new_status in (JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED):
                if not job.finished_at:
                    job.finished_at = datetime.now(timezone.utc)

            JobRepository(db).update_status(
                job_id=      job_id,
                status=      new_status,
                error=       job.error,
                started_at=  job.started_at,
                finished_at= job.finished_at,
                steps=       job.steps,
            )
            db.expire_all()
            db.commit()
        except HTTPException:
            raise
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(
                "Failed to persist status update for job %s: %s", job_id, e
            )

    return _to_detail(job)


@router.get("/{job_id}/scene", response_model=SceneResponse)
async def get_job_scene(
    job_id: str,
    db:     Session = Depends(get_db),
) -> SceneResponse:
    """Build a Viewport3D-renderable scene for a job's geometry.

    Reuses the exact same YAML-text -> SceneDescription -> SceneResponse
    pipeline as POST /geometry/scene (see build_scene_response), just
    sourcing the text from the job record instead of a live editor buffer.
    This is deliberate: job.geometry (the persisted CascadeGeometry) is
    already-expanded surfaces/cells, not the mid-level component schemas
    SceneBuilder needs, so there's no shortcut that skips re-parsing
    geometry_text.

    404s (not a 200 with `error` set) when the job predates the
    geometry_text column — that's "no data available", not a YAML
    validation error, and the two shouldn't be conflated in one field.

    For a sweep job, geometry_text is the sweep *template* (still
    containing sweep(...) expressions), not that job's per-point resolved
    dimensions — see the comment in submit_sweep(). The scene shown here
    is the sweep's nominal preview, same as what the editor shows before
    submission, not a point-accurate render.
    """
    job = _get_job_or_404(job_id, db)

    if not job.geometry_text:
        raise HTTPException(
            status_code=404,
            detail=(
                f"Job '{job_id}' has no stored geometry text — it was "
                "submitted before scene preview support was added."
            ),
        )

    return build_scene_response(job.geometry_text)

@router.get("/{job_id}/csg")
async def get_job_csg(job_id: str, db: Session = Depends(get_db)) -> dict:
    """Return the fully-expanded CSG geometry (surfaces + cells + region
    trees) for a job — structured JSON, not XML — so the frontend can
    rasterize material-colored slices itself without invoking OpenMC.

    This is the exact same CascadeGeometry OpenMCAdapter.export_geometry()
    serializes to geometry.xml (job.geometry, already-expanded — not the
    mid-level component schemas /scene works from). Unlike /scene, this
    is agnostic to component type — it's raw surfaces/cells, so it works
    for any geometry the expander can produce, present or future.
    """
    job = _get_job_or_404(job_id, db)
    geom = job.geometry
    return {
        "surfaces": [
            {
                "id": s.id,
                "type": s.type_.value,
                "params": s.params,
                "boundary_type": s.boundary_type.value,
            }
            for s in geom.surfaces
        ],
        "cells": [
            {
                "id": c.id,
                "material_id": c.material_id,
                "name": c.name,
                "region": region_to_json(c.region),
            }
            for c in geom.cells
        ],
    }

@router.post("/{job_id}/cancel", response_model=JobSummary)
async def cancel_job(
    job_id: str,
    db:     Session = Depends(get_db),
) -> JobSummary:
    """Cancel a queued or running job using the correct backend."""
    job = _get_job_or_404(job_id, db)

    if job.status in (JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED):
        raise HTTPException(
            status_code=409,
            detail=f"Job is already {job.status.value} — cannot cancel.",
        )

    backend = _backend_for_job(job_id, db)

    try:
        job = backend.cancel(job)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Cancel failed: {e}")

    JobRepository(db).update_status(
        job_id=      job_id,
        status=      job.status,
        finished_at= job.finished_at,
    )

    return _to_summary(job)


@router.delete("/{job_id}", response_model=DeletedResponse)
async def delete_job(
    job_id: str,
    db:     Session = Depends(get_db),
) -> DeletedResponse:
    """Delete a job record and its working directory."""
    job = _get_job_or_404(job_id, db)

    if job.status == JobStatus.RUNNING:
        raise HTTPException(
            status_code=409,
            detail="Cannot delete a running job. Cancel it first.",
        )

    if job.working_dir and job.working_dir.exists():
        shutil.rmtree(job.working_dir, ignore_errors=True)

    JobRepository(db).delete(job_id)
    return DeletedResponse(id=job_id)

@router.get("/{job_id}/stdout")
async def get_job_stdout(
    job_id: str,
    db:     Session = Depends(get_db),
) -> dict:
    """Return the raw stdout from a job's run.log file(s).

    This is the actual OpenMC terminal output — particle transport
    progress, k-eff per batch, timing, and errors.
    Polled every 2s by the frontend while a job is running.

    BUG FIX: this previously only ever read `job.working_dir / "run.log"`.
    That's correct for eigenvalue/fixed_source (job.steps is empty, and
    DockerBackend writes run.log directly there — see
    `_submit_single_leg`), but depletion and r2s jobs write run.log
    per-step, at `step.working_dir / "run.log"` (see `_launch_step()` in
    docker_backend.py) — `job.working_dir / "run.log"` never exists for
    them at all. The console silently showed nothing for every depletion/
    r2s job as a result. Now: for a job with steps, concatenate each
    step's log (in sequence order, with a header per step) so the console
    shows the full pipeline's progress, not just whichever step happens to
    be running right now.

    Returns:
        { "lines": str, "available": bool }
    """
    job = _get_job_or_404(job_id, db)

    if not job.working_dir:
        return {"lines": "", "available": False}

    if job.steps:
        return _read_stepped_stdout(job)

    log_path = job.working_dir / "run.log"
    if not log_path.exists():
        return {"lines": "", "available": False}

    try:
        content = log_path.read_text(encoding="utf-8", errors="replace")
        return {"lines": content, "available": True}
    except OSError:
        return {"lines": "", "available": False}


def _read_stepped_stdout(job: SimulationJob) -> dict:
    """Concatenate each step's run.log in sequence order.

    Only includes steps that have actually started (QUEUED steps have no
    working_dir contents yet — nothing to show). A header line names each
    step so the pipeline's progress is legible, e.g. for r2s:
        === neutron leg ===
        <openmc output>
        === photon leg ===
        <openmc output>
    """
    chunks: list[str] = []
    for step in sorted(job.steps, key=lambda s: s.sequence):
        if step.working_dir is None:
            continue
        log_path = step.working_dir / "run.log"
        if not log_path.exists():
            continue
        try:
            content = log_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        chunks.append(f"=== {step.label} ===\n{content}")

    if not chunks:
        return {"lines": "", "available": False}
    return {"lines": "\n\n".join(chunks), "available": True}