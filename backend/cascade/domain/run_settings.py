"""Run-mode and Monte Carlo settings domain model.

job-settings-model.md §0: a "job" is one or more OpenMC transport runs, and
the shape of valid settings depends entirely on `run_mode`. This module is
the single source of truth for that shape — it replaces the single flat
`OpenMCRunSettings(particles, inactive, batches, seed, run_mode)` that
previously tried to represent all four run modes with one set of fields.

Key structural facts encoded here (see job-settings-model.md §2-§4):
    - `inactive` batches only make sense for a k-eigenvalue source
      convergence scheme: eigenvalue mode, and each depletion timestep.
      A fixed-source transport leg — fixed_source mode, and BOTH r2s legs
      — never has inactive batches. This was previously a single
      `needsInactive` boolean keyed only on top-level run_mode, which is
      why the UI could offer "inactive batches" for r2s in the first place.
    - r2s is a *pipeline*, not a parameterized single run: neutron leg
      (transport) -> activation (NOT transport) -> photon leg (transport).
      Each leg gets independent McSettings — never a shared `particles`
      field (photon legs typically need far more particles).
    - depletion's `inactive`/`batches` apply *per timestep*.

OpenMC itself only knows two run modes at the settings.xml level:
"eigenvalue" and "fixed source". Cascade's `depletion` and `r2s` modes are
higher-level concepts that decompose into one or more eigenvalue/fixed-source
transport legs plus, for r2s, a non-transport activation step. That
decomposition is an execution-orchestration concern (see execution/), not
a settings-shape concern — this module only describes the *shape*.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .results_config import ParticleType


class RunMode:
    """Cascade-level run mode. Plain string constants (not StrEnum) so this
    file has no enum/import surprises when mirrored by the Pydantic layer —
    see api/jobs.py, which uses the literal values directly.
    """
    EIGENVALUE   = "eigenvalue"
    FIXED_SOURCE = "fixed_source"
    DEPLETION    = "depletion"
    R2S          = "r2s"

    ALL = (EIGENVALUE, FIXED_SOURCE, DEPLETION, R2S)


# ---------------------------------------------------------------------------
# Source definitions
# ---------------------------------------------------------------------------

class SourceSpaceType:
    POINT = "point"
    BOX   = "box"


@dataclass(frozen=True)
class SourceDef:
    """A user-specified particle source.

    Required for `fixed_source` mode — job-settings-model.md §3.2 flags this
    as currently missing entirely; a fixed_source run cannot be submitted
    with a real source today. Also required for r2s's neutron leg. Never
    used for `eigenvalue` (source is geometry-driven, auto-detected from
    fissile cells) and never user-entered for r2s's photon leg (derived
    from the activation step — see R2SSettings).

    Fields:
        particle:     Which particle type this source emits.
        space_type:   Spatial distribution shape.
        space_params: Parameters for the spatial distribution.
                      `point`: (x, y, z).
                      `box`:   (xmin, ymin, zmin, xmax, ymax, zmax).
        energy_mev:   Monoenergetic source energy in MeV. None = OpenMC
                      default (Watt fission spectrum for neutron sources;
                      must be set explicitly for a photon source since
                      there is no default photon spectrum).
    """
    particle:     str
    space_type:   str = SourceSpaceType.POINT
    space_params: tuple[float, ...] = (0.0, 0.0, 0.0)
    energy_mev:   float | None = None

    def __post_init__(self) -> None:
        if self.particle not in (ParticleType.NEUTRON, ParticleType.PHOTON):
            raise ValueError(f"SourceDef.particle must be 'neutron' or 'photon', got {self.particle!r}.")
        if self.space_type not in (SourceSpaceType.POINT, SourceSpaceType.BOX):
            raise ValueError(f"SourceDef.space_type must be 'point' or 'box', got {self.space_type!r}.")
        expected_len = 3 if self.space_type == SourceSpaceType.POINT else 6
        if len(self.space_params) != expected_len:
            raise ValueError(
                f"SourceDef.space_params for space_type={self.space_type!r} "
                f"must have {expected_len} values, got {len(self.space_params)}."
            )
        if self.particle == ParticleType.PHOTON and self.energy_mev is None:
            raise ValueError(
                "A photon source has no default energy spectrum — "
                "energy_mev is required when particle='photon'."
            )


# ---------------------------------------------------------------------------
# Monte Carlo settings — particles/batches/seed, scoped per transport leg
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class McSettings:
    """One transport leg's particle/batch/seed configuration.

    `inactive` is intentionally optional rather than always-present: it is
    only meaningful for a k-eigenvalue source-convergence scheme
    (eigenvalue mode, and each depletion timestep). Fixed-source legs —
    fixed_source mode, and BOTH r2s legs — have no inactive batches at
    all; setting one there is a modelling error, not a style choice, so
    `validate()` rejects it rather than silently ignoring it the way the
    old UI's `needsInactive` boolean did.

    Fields:
        particles: Particles started per batch.
        batches:   Total batches (inactive + active, if `inactive` is set;
                   otherwise all batches are active).
        seed:      Random number seed.
        inactive:  Inactive (warmup) batches discarded from tallies.
    """
    particles: int = 1000
    batches:   int = 100
    seed:      int = 1
    inactive:  int | None = None

    def validate(self, run_mode: str, *, leg: str | None = None) -> None:
        """Raise ValueError if invalid for the given run mode.

        `leg` is an optional label ("neutron leg" / "photon leg") used only
        to make r2s error messages legible.
        """
        where = run_mode + (f" ({leg})" if leg else "")
        needs_inactive = run_mode in (RunMode.EIGENVALUE, RunMode.DEPLETION)

        if self.particles < 1:
            raise ValueError(f"{where}: particles must be >= 1.")
        if self.batches < 1:
            raise ValueError(f"{where}: batches must be >= 1.")

        if needs_inactive and self.inactive is None:
            raise ValueError(f"{where} requires `inactive` batches to be set.")
        if not needs_inactive and self.inactive is not None:
            raise ValueError(
                f"{where} is a fixed-source transport leg and cannot have "
                f"`inactive` batches — there is no source convergence to "
                f"discard warmup batches for. (job-settings-model.md §2)"
            )
        if self.inactive is not None and self.inactive >= self.batches:
            raise ValueError(
                f"{where}: inactive batches ({self.inactive}) must be less "
                f"than total batches ({self.batches})."
            )


# ---------------------------------------------------------------------------
# Depletion-specific settings
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DepletionSettings:
    """Settings specific to a `depletion` run.

    Fields:
        power_W:    Reactor power in watts, held constant across all
                    timesteps (time-varying power is a future extension).
        timesteps:  Step durations in days. `inactive`/`batches` on the
                    job's McSettings apply to EACH of these, not once
                    (job-settings-model.md §3.3) — cost displayed to the
                    user should multiply by len(timesteps).
        chain_file: Reference to the depletion chain file. Previously only
                    a free-text UI warning string; now a required,
                    validated field (job-settings-model.md §3.3, §7.3).
        integrator: Depletion solver/integrator scheme (e.g. predictor,
                    CE/CI).
        substeps:   Substeps per timestep interval (integrator-dependent).
    """
    power_W:    float
    timesteps:  list[float]
    chain_file: str
    integrator: str = "predictor"
    substeps:   int = 1

    def validate(self) -> None:
        if self.power_W <= 0:
            raise ValueError("depletion.power_W must be > 0.")
        if not self.timesteps:
            raise ValueError("depletion.timesteps must be non-empty.")
        if any(t <= 0 for t in self.timesteps):
            raise ValueError("depletion.timesteps must all be > 0 days.")
        if not self.chain_file:
            raise ValueError(
                "depletion requires a depletion chain file reference "
                "(job-settings-model.md §3.3 — previously only a UI warning, "
                "now enforced)."
            )
        if self.substeps < 1:
            raise ValueError("depletion.substeps must be >= 1.")


# ---------------------------------------------------------------------------
# R2S-specific settings — a pipeline, not a single parameterized run
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class IrradiationSchedule:
    """Power-time history feeding an activation calculation.

    Deliberately the same shape as DepletionSettings.power_W/timesteps
    (job-settings-model.md §6.5 — they're the same physical concept and
    should share one input component at the UI layer, not drift apart).
    """
    power_W:   float
    timesteps: list[float]

    def validate(self) -> None:
        if self.power_W <= 0:
            raise ValueError("irradiation_schedule.power_W must be > 0.")
        if not self.timesteps:
            raise ValueError("irradiation_schedule.timesteps must be non-empty.")
        if any(t <= 0 for t in self.timesteps):
            raise ValueError("irradiation_schedule.timesteps must all be > 0 days.")


@dataclass(frozen=True)
class ActivationSettings:
    """The non-transport activation/decay step between r2s's two legs.

    Consumes the neutron leg's reaction-rate mesh + irradiation_schedule,
    produces a photon source distribution. This step is NOT a transport
    run (job-settings-model.md §4), so it has no McSettings of its own.

    Fields:
        irradiation_schedule: Power-time history during neutron irradiation.
        cooling_times: Decay times in seconds after irradiation stops, each
                       >= 0. One dose result is produced per cooling time
                       (job-settings-model.md §6.1), so this is a list, not
                       a single number.
        decay_library: Reference to the decay/activation data library.
                       Required — previously missing entirely from the form.
    """
    irradiation_schedule: IrradiationSchedule
    cooling_times:        list[float]
    decay_library:        str

    def validate(self) -> None:
        self.irradiation_schedule.validate()
        if not self.cooling_times:
            raise ValueError("r2s.activation.cooling_times must be non-empty.")
        if any(t < 0 for t in self.cooling_times):
            raise ValueError("r2s.activation.cooling_times must all be >= 0.")
        if not self.decay_library:
            raise ValueError("r2s.activation.decay_library is required.")


@dataclass(frozen=True)
class VrSettings:
    """Variance reduction settings.

    Optional everywhere it appears, strongly recommended for r2s's photon
    leg (job-settings-model.md §2 — deep-shielding/dose problems are
    usually unusable without it). Intentionally minimal for v1; extend with
    survival biasing / weight-window-generator params as needed.
    """
    weight_windows_enabled: bool = False


@dataclass(frozen=True)
class R2SSettings:
    """The full r2s pipeline configuration (job-settings-model.md §4).

    neutron_leg_mc and photon_leg_mc are independent McSettings — never a
    shared `particles` field, since photon legs usually need far more
    particles than the neutron leg.
    """
    neutron_leg_source: SourceDef
    neutron_leg_mc:     McSettings
    activation:          ActivationSettings
    photon_leg_mc:        McSettings
    photon_leg_vr:        VrSettings = field(default_factory=VrSettings)

    def validate(self) -> None:
        if self.neutron_leg_source.particle != ParticleType.NEUTRON:
            raise ValueError("r2s.neutron_leg_source must be a neutron source.")
        self.neutron_leg_mc.validate(RunMode.R2S, leg="neutron leg")
        self.activation.validate()
        self.photon_leg_mc.validate(RunMode.R2S, leg="photon leg")