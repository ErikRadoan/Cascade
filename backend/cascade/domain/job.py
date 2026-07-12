"""Job domain model."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .geometry import CascadeGeometry
from .material import Material
from .job_status import JobStatus  # re-exported — see job_status.py docstring
from .job_step import JobStep, derive_job_status
from .results_config import ResultsConfig, R2SResultsConfig, TallyScore
from .run_settings import (
    ActivationSettings,
    DepletionSettings,
    McSettings,
    R2SSettings,
    RunMode,
    SourceDef,
    VrSettings,
)


# Nuclides that make a material fissile/fertile for the purposes of job-level
# gating (eigenvalue requires one present; fission/nu-fission scores require
# one present — job-settings-model.md §3.1, §6.3).
#
# NOTE: this mirrors `_is_fissile` in adapters/openmc_adapter.py. The two
# should be consolidated into a single domain/material.py helper — kept
# duplicated for now rather than having domain/job.py import from the
# adapters layer, which would invert the intended dependency direction.
_FISSILE_NUCLIDES = frozenset({
    "U233", "U235", "U238",
    "Pu238", "Pu239", "Pu240", "Pu241", "Pu242",
    "Th232",
    "Am241", "Cm244",
})


def _has_fissile_material(materials: list[Material]) -> bool:
    return any(
        nuc in _FISSILE_NUCLIDES
        for mat in materials
        for nuc in mat.composition
    )


@dataclass(slots=True)
class SimulationJob:
    """A single simulation job carrying everything needed to run it.

    The job is self-contained — the execution backend receives this object
    and has everything it needs to stage input files and submit the run.
    It never needs to query a database or service layer.

    Shape depends on `run_mode` (job-settings-model.md §0-§4). Validation
    happens in `__post_init__` rather than being left to the caller, because
    a job with mismatched run_mode/settings should never be constructible —
    this is the structural fix for "R2S with inactive batches doesn't make
    sense": it's now a ValueError at construction, not a UI affordance.

    Attributes:
        id:             Unique job identifier (UUID string).
        geometry:       Fully resolved CascadeGeometry for this job.
        materials:      All materials referenced by cells in geometry.
        geometry_text:  Raw YAML geometry text the job was submitted with,
                        kept alongside the already-expanded `geometry` so
                        the results view can rebuild a Viewport3D scene
                        (SceneBuilder works on pre-expansion schemas, not
                        the low-level CascadeGeometry surfaces/cells —
                        see api/jobs.py's /scene route). None for jobs
                        persisted before this field existed.
        run_mode:       One of RunMode.{EIGENVALUE,FIXED_SOURCE,DEPLETION,R2S}.
        monte_carlo:    Particle/batch/seed settings for a single transport
                        leg. Set for eigenvalue/fixed_source/depletion.
                        MUST be None for r2s — see `mode_specific.R2SSettings`,
                        which carries independent settings per leg.
        source:         User-specified source. Required for fixed_source,
                        forbidden for eigenvalue (geometry-driven), unused
                        for depletion, unused for r2s (see mode_specific).
        mode_specific:  DepletionSettings for depletion, R2SSettings for
                        r2s, None otherwise.
        variance_reduction: Optional VR settings. Most relevant for
                        fixed_source and (via mode_specific.photon_leg_vr)
                        r2s's photon leg.
        param_values:   Sweep parameter values that produced this geometry.
                        Empty dict for single (non-sweep) jobs.
        backend:        Which execution backend to use ("docker", "local", "slurm").
        results_config: What to capture. A single ResultsConfig for
                        eigenvalue/fixed_source/depletion. A R2SResultsConfig
                        (neutron_leg + photon_leg) for r2s — NEVER a single
                        global ResultsConfig (job-settings-model.md §1).
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
    run_mode:       str
    monte_carlo:    McSettings | None              = None
    source:         SourceDef | None                = None
    mode_specific:  DepletionSettings | R2SSettings | None = None
    variance_reduction: VrSettings | None           = None
    param_values:   dict[str, float | str]          = field(default_factory=dict)
    backend:        str                              = "docker"
    results_config: ResultsConfig | R2SResultsConfig = field(default_factory=ResultsConfig.default)
    status:         JobStatus                        = JobStatus.QUEUED
    # Execution steps (PLAN_depletion_r2s_execution.md Task 2). Empty for
    # eigenvalue/fixed_source jobs run the old way (DockerBackend still
    # sets `status` directly on those — see effective_status()). Populated
    # by DockerBackend for r2s's 3-step pipeline (and, in the future,
    # depletion's single driver-script step, once that's wired up too).
    steps:          list[JobStep]                    = field(default_factory=list)
    # Which sweep (if any) produced this job — previously generated in
    # api/jobs.py's submit_sweep() but never attached to the job itself,
    # so results views had no way to look it up (see PLAN doc's sweep_id
    # section). None for a standalone (non-sweep) job.
    sweep_id:       str | None                       = None
    working_dir:    Path | None                      = None
    created_at:     datetime                         = field(default_factory=lambda: datetime.now(timezone.utc))
    started_at:     datetime | None                  = None
    finished_at:    datetime | None                  = None
    error:          str | None                       = None
    notes:          str | None                       = None
    # Raw YAML the job was submitted with — see class docstring. Not part
    # of to_dict()/JobDetail (it can be large and most consumers only need
    # geometry_id); fetched explicitly via the /scene endpoint instead.
    geometry_text:  str | None                       = None

    def __post_init__(self) -> None:
        if self.run_mode not in RunMode.ALL:
            raise ValueError(f"Unknown run_mode '{self.run_mode}'. Must be one of {RunMode.ALL}.")

        if self.run_mode == RunMode.EIGENVALUE:
            self._validate_single_leg(needs_source=False, forbid_source=True, mode_specific_type=None)
            if not _has_fissile_material(self.materials):
                raise ValueError(
                    "eigenvalue mode requires geometry with a fissile material "
                    "present — the criticality source is geometry-driven and "
                    "has nothing to converge on otherwise (job-settings-model.md §3.1)."
                )

        elif self.run_mode == RunMode.FIXED_SOURCE:
            self._validate_single_leg(needs_source=True, forbid_source=False, mode_specific_type=None)

        elif self.run_mode == RunMode.DEPLETION:
            self._validate_single_leg(needs_source=False, forbid_source=True, mode_specific_type=DepletionSettings)
            self.mode_specific.validate()  # type: ignore[union-attr]
            if not _has_fissile_material(self.materials):
                raise ValueError(
                    "depletion mode requires geometry with a fissile material "
                    "present — it's both the criticality source for each "
                    "timestep's transport solve (same as eigenvalue mode) "
                    "and the material that actually gets marked depletable "
                    "in materials.xml. Without one, openmc.deplete.CoupledOperator "
                    "fails with 'No depletable materials were found in the model' "
                    "regardless of the chain file supplied."
                )

        elif self.run_mode == RunMode.R2S:
            if self.monte_carlo is not None:
                raise ValueError(
                    "r2s has no single `monte_carlo` — each leg has independent "
                    "settings under `mode_specific` (R2SSettings.neutron_leg_mc / "
                    "photon_leg_mc). job-settings-model.md §4."
                )
            if self.source is not None:
                raise ValueError(
                    "r2s has no single `source` — set `mode_specific.neutron_leg_source` "
                    "instead (the photon leg's source is derived, never user-entered)."
                )
            if not isinstance(self.mode_specific, R2SSettings):
                raise ValueError("r2s requires `mode_specific` to be R2SSettings.")
            self.mode_specific.validate()
            if not isinstance(self.results_config, R2SResultsConfig):
                raise ValueError(
                    "r2s requires a per-leg R2SResultsConfig (neutron_leg + "
                    "photon_leg), not a single ResultsConfig — this is the "
                    "core structural fix from job-settings-model.md §1."
                )
            self._validate_fissile_gating(self.results_config.neutron_leg)
            # Photon leg can never request fission/nu-fission at all — that's
            # already enforced by ResultsConfig.__post_init__ at construction
            # time (particle_type=PHOTON excludes those scores outright), so
            # no fissile check is needed for the photon leg here.

        # results_config shape + fissile-gating for the single-leg modes
        if self.run_mode != RunMode.R2S:
            if not isinstance(self.results_config, ResultsConfig):
                raise ValueError(
                    f"{self.run_mode} requires a single ResultsConfig, not "
                    f"R2SResultsConfig."
                )
            self._validate_fissile_gating(self.results_config)

    def _validate_single_leg(
        self,
        *,
        needs_source: bool,
        forbid_source: bool,
        mode_specific_type: type | None,
    ) -> None:
        if self.monte_carlo is None:
            raise ValueError(f"{self.run_mode} requires `monte_carlo` settings.")
        self.monte_carlo.validate(self.run_mode)

        if needs_source and self.source is None:
            raise ValueError(
                f"{self.run_mode} requires a `source` definition "
                f"(job-settings-model.md §3.2 — this was previously missing "
                f"entirely; fixed_source could not actually be submitted)."
            )
        if forbid_source and self.source is not None:
            raise ValueError(
                f"{self.run_mode}'s source is geometry-driven; do not set `source`."
            )

        if mode_specific_type is None:
            if self.mode_specific is not None:
                raise ValueError(f"{self.run_mode} must not set `mode_specific`.")
        elif not isinstance(self.mode_specific, mode_specific_type):
            raise ValueError(f"{self.run_mode} requires `mode_specific` to be {mode_specific_type.__name__}.")

    def _validate_fissile_gating(self, rc: ResultsConfig) -> None:
        """job-settings-model.md §6.3: fission/nu-fission scores require a
        fissile material in geometry — block, don't just warn."""
        fission_scores = {TallyScore.FISSION, TallyScore.NU_FISSION}
        requested = set()
        if rc.scalars.enabled:
            requested |= set(rc.scalars.scores) & fission_scores
        if rc.mesh.enabled:
            requested |= set(rc.mesh.scores) & fission_scores
        if requested and not _has_fissile_material(self.materials):
            raise ValueError(
                f"Scores {sorted(s.value for s in requested)} require a "
                f"fissile material in geometry, but none was found "
                f"(job-settings-model.md §6.3)."
            )

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

    def effective_status(self) -> JobStatus:
        """The job's real current status.

        Falls back to `self.status` (the plain field, set directly by
        DockerBackend today) when `steps` is empty — this is what keeps
        eigenvalue/fixed_source jobs working exactly as before without any
        changes to how DockerBackend handles them. Once `steps` is
        populated (r2s's 3-step pipeline; eventually depletion's single
        driver step too), status is *derived* from step statuses instead —
        the same pattern SweepRow already uses one level up (sweep status
        derived from child job statuses, never stored redundantly).

        Callers that read job status for display or polling (API layer,
        JobRepository) should call this instead of reading `.status`
        directly, so they get correct behavior for both step-based and
        legacy single-field jobs without needing to know which kind a
        given job is.
        """
        if self.steps:
            return derive_job_status(self.steps)
        return self.status

    def to_dict(self) -> dict[str, object]:
        return {
            "id":             self.id,
            "geometry_id":    self.geometry.id,
            "run_mode":       self.run_mode,
            "monte_carlo":    _opt_to_dict(self.monte_carlo),
            "source":         _opt_to_dict(self.source),
            "mode_specific":  _opt_to_dict(self.mode_specific),
            "variance_reduction": _opt_to_dict(self.variance_reduction),
            "param_values":   self.param_values,
            "backend":        self.backend,
            "results_config": self.results_config.to_dict(),
            "status":         self.effective_status().value,
            "steps":          [s.to_dict() for s in self.steps],
            "sweep_id":       self.sweep_id,
            "working_dir":    str(self.working_dir) if self.working_dir else None,
            "created_at":     self.created_at.isoformat(),
            "started_at":     self.started_at.isoformat() if self.started_at else None,
            "finished_at":    self.finished_at.isoformat() if self.finished_at else None,
            "error":          self.error,
            "notes":          self.notes,
        }


def _opt_to_dict(obj) -> dict | None:
    """dataclasses.asdict for an optional frozen dataclass, None-safe."""
    if obj is None:
        return None
    import dataclasses
    return dataclasses.asdict(obj)