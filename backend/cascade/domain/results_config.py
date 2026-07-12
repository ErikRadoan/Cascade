"""Results capture configuration — what to ask OpenMC to compute and record.

This domain model carries the user's choices from the job submission form
all the way through to tallies.xml generation. It is a pure data structure
with no I/O, no XML, and no OpenMC dependency.

Design rationale:
    Tallies must be declared in tallies.xml *before* the simulation runs.
    You cannot retroactively extract data that wasn't tallied. So this
    config object is submitted alongside the geometry and run settings,
    and the adapter translates it into a tallies.xml file at staging time.

Groups:
    Group 1 — simulation_summary  (always on, zero overhead)
    Group 2 — scalar_tallies      (per-cell flux/fission/absorption, tiny cost)
    Group 3 — mesh_tally          (3D power/flux map, cost scales with resolution)
    Group 4 — energy_spectra      (flux vs energy, cost scales with groups)
    Group 5 — diagnostics         (stochastic volumes, particle tracks)

CHANGE (r2s restructure): scores and group structures are no longer one
global list. A ResultsConfig is now scoped to a `particle_type` and only
accepts the scores/group structures valid for that particle's transport
context (job-settings-model.md §5). For r2s, which has two distinct
transport legs, use R2SResultsConfig — never a single ResultsConfig.

Groups 4+ (MGXS, depletion) are intentionally excluded from v1 — they
require substantially more backend infrastructure and are better scoped
as a separate milestone.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from enum import StrEnum


# ---------------------------------------------------------------------------
# Particle type — the scoping key for everything in this module
# ---------------------------------------------------------------------------

class ParticleType(StrEnum):
    NEUTRON = "neutron"
    PHOTON  = "photon"


# ---------------------------------------------------------------------------
# Score types — what OpenMC measures per tally
# ---------------------------------------------------------------------------

class TallyScore(StrEnum):
    FLUX          = "flux"
    FISSION       = "fission"
    ABSORPTION    = "absorption"
    NU_FISSION    = "nu-fission"
    HEATING       = "heating"
    HEATING_LOCAL = "heating-local"
    SCATTER       = "scatter"
    CURRENT       = "current"


# Scores valid per particle type, independent of fissile-material presence.
# Fissile-presence gating (fission/nu-fission require an actual fissile
# material in geometry) is a separate, job-level check — see
# domain/job.py — because it needs the resolved material list, which this
# module deliberately knows nothing about.
_SCORES_BY_PARTICLE: dict[ParticleType, frozenset[TallyScore]] = {
    ParticleType.NEUTRON: frozenset({
        TallyScore.FLUX, TallyScore.FISSION, TallyScore.ABSORPTION,
        TallyScore.NU_FISSION, TallyScore.HEATING, TallyScore.HEATING_LOCAL,
        TallyScore.SCATTER, TallyScore.CURRENT,
    }),
    ParticleType.PHOTON: frozenset({
        # job-settings-model.md §5: fission/nu-fission are NEVER valid for
        # a photon leg — there is no fission reaction for photons to score.
        TallyScore.FLUX, TallyScore.HEATING, TallyScore.HEATING_LOCAL,
        TallyScore.ABSORPTION, TallyScore.SCATTER,
    }),
}


def valid_scores(particle_type: ParticleType) -> frozenset[TallyScore]:
    """Scores selectable for a given particle type's transport context."""
    return _SCORES_BY_PARTICLE[particle_type]


# ---------------------------------------------------------------------------
# Energy group structures — neutron-only
# ---------------------------------------------------------------------------

class EnergyGroupStructure(StrEnum):
    """Standard energy group structures for neutron flux spectra.

    BROAD_33:   33-group CASMO structure — good for LWR analysis
    FINE_69:    69-group WIMS structure — standard for PWR
    ULTRA_252:  252-group — high resolution, expensive
    CUSTOM:     User supplies breakpoints (not yet implemented in v1)

    These are neutron multigroup library names and are not meaningful for a
    photon leg (job-settings-model.md §5) — photon-leg spectra would need a
    photon-appropriate energy-bin scheme, which is not implemented yet.
    For now, energy spectra are simply unavailable on a photon leg; see
    EnergySpectraConfig.
    """
    BROAD_33  = "33"
    FINE_69   = "69"
    ULTRA_252 = "252"


def valid_group_structures(particle_type: ParticleType) -> tuple[EnergyGroupStructure, ...]:
    """Group structures selectable for a given particle type.

    Empty for photon — see EnergyGroupStructure docstring. Callers (API
    layer, frontend) should hide the picker entirely for a photon leg
    rather than show an empty list with no explanation.
    """
    if particle_type is ParticleType.PHOTON:
        return ()
    return tuple(EnergyGroupStructure)


# Group structures as eV breakpoints — a representative subset
_GROUP_BOUNDARIES: dict[EnergyGroupStructure, list[float]] = {
    EnergyGroupStructure.BROAD_33: [
        1e-5, 3e-3, 1.7e-2, 1e-1, 5.8e-1, 1.4, 4, 9.1, 1.86e1,
        6.76e1, 1.48e2, 3.6e2, 1.3e3, 8.32e3, 2.04e4, 6.57e4,
        1.5e5, 2.48e5, 4.98e5, 8.21e5, 1.35e6, 1.65e6, 2e6,
        2.47e6, 3.01e6, 3.68e6, 4.97e6, 6.07e6, 7.41e6, 8.61e6,
        1e7, 1.2e7, 1.4e7, 2e7,
    ],
    EnergyGroupStructure.FINE_69: [
        # Representative 69-group WIMS boundaries (abbreviated here —
        # full list loaded from data file in production)
        1e-5, 1e-4, 5e-4, 1e-3, 5e-3, 1e-2, 5e-2,
        1e-1, 2e-1, 4e-1, 6e-1, 8e-1, 1.0, 1.5, 2.0,
        3.0, 4.0, 5.0, 6.25, 6.5, 7.5, 8.5, 9.25, 10.0,
        1.2e1, 1.5e1, 1.9e1, 2.55e1, 3.2e1, 4e1, 5.2e1,
        6.7e1, 8.5e1, 1e2, 1.28e2, 1.6e2, 2e2, 2.6e2,
        3.75e2, 5e2, 7e2, 1e3, 1.5e3, 2.25e3, 3.5e3, 5e3,
        7e3, 1e4, 1.5e4, 2e4, 3e4, 4e4, 5.2e4, 6e4, 7.5e4,
        1e5, 1.4e5, 1.8e5, 2.2e5, 2.8e5, 3.5e5, 5e5, 8.2e5,
        1.4e6, 1.8e6, 2.2e6, 3.3e6, 6e6, 1e7, 2e7,
    ],
    EnergyGroupStructure.ULTRA_252: [],   # populated from data file
}


# ---------------------------------------------------------------------------
# Group 1 — Simulation summary (always captured, zero extra overhead)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SimulationSummaryConfig:
    """Always-on. Parsed from run.log and statepoint.

    These are free — OpenMC computes them regardless of tallies.xml.
    No additional XML is needed. Parsing is done post-run from the
    statepoint HDF5 file.

    Fields:
        k_effective:     Parse k-eff (all 3 estimators) + confidence intervals.
                          Only meaningful for an eigenvalue-mode leg — a
                          fixed-source leg has no k-eff. Adapters should
                          skip k-eff parsing for fixed-source legs rather
                          than report a meaningless value.
        entropy_history: Per-batch Shannon entropy (source convergence plot)
        batch_keff:      Per-batch k-eff (convergence history plot)
        neutron_balance: Absorbed/leaked/fission-produced fractions
        timing:          Total, transport, I/O breakdown
    """
    k_effective:     bool = True   # always True, never toggleable
    entropy_history: bool = True
    batch_keff:      bool = True
    neutron_balance: bool = True
    timing:          bool = True


# ---------------------------------------------------------------------------
# Group 2 — Scalar tallies (per-cell, low cost)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ScalarTallyConfig:
    """One number per cell — no spatial resolution, no energy bins.

    Generates a simple tallies.xml with one CellFilter per tally.
    Cost: kilobytes of statepoint data, negligible runtime overhead.

    Fields:
        enabled:    Whether to generate this tally group at all.
        scores:     Which quantities to measure per cell. Must be a subset
                    of valid_scores(particle_type) of the owning ResultsConfig
                    — enforced there, not here, since this object doesn't
                    know its own particle type.
        all_cells:  If True, tally every cell. If False, only fissile cells.
                    (Fissile = cells whose material contains U/Pu/Th nuclides.)
                    Meaningless for a photon leg — see ResultsConfig.
    """
    enabled:   bool              = True
    scores:    list[TallyScore]  = field(default_factory=lambda: [
        TallyScore.FLUX,
        TallyScore.FISSION,
        TallyScore.ABSORPTION,
        TallyScore.HEATING,
    ])
    all_cells: bool = False   # False = fissile cells only (faster + more useful)


# ---------------------------------------------------------------------------
# Group 3 — Mesh tally (3D spatial distribution, medium cost)
# ---------------------------------------------------------------------------

class MeshType(StrEnum):
    REGULAR     = "regular"
    CYLINDRICAL = "cylindrical"


@dataclass(frozen=True)
class MeshTallyConfig:
    """3D field of flux/power/heating on a regular or cylindrical mesh.

    Cost scales with nx*ny*nz and number of scores. A 50×50×50 mesh
    with 3 scores is ~3 MB of statepoint data. A 200×200×200 mesh is ~200 MB.
    Default 20×20×20 is a good starting point for pin-cell geometry.

    For r2s, photon dose meshes are commonly *larger* than the neutron flux
    mesh (job-settings-model.md §6.2) — the voxel-count warning at the UI
    layer must be applied per-leg, not just once.

    Fields:
        enabled:     Whether to generate this tally.
        mesh_type:   Regular (Cartesian) or cylindrical.
        nx, ny, nz:  Number of mesh voxels (regular mesh).
        nr, nz_cyl: Number of radial/axial bins (cylindrical mesh).
        scores:      Which quantities to map. Subset of valid_scores(particle_type).
    """
    enabled:   bool             = False
    mesh_type: MeshType         = MeshType.REGULAR
    nx:        int              = 20
    ny:        int              = 20
    nz:        int              = 20
    nr:        int              = 20   # cylindrical only
    nz_cyl:    int              = 20   # cylindrical only
    scores:    list[TallyScore] = field(default_factory=lambda: [
        TallyScore.FLUX,
        TallyScore.FISSION,
        TallyScore.HEATING_LOCAL,
    ])

    def voxel_count(self) -> int:
        if self.mesh_type == MeshType.REGULAR:
            return self.nx * self.ny * self.nz
        return self.nr * self.nz_cyl


# ---------------------------------------------------------------------------
# Group 4 — Energy spectra (flux vs energy, medium cost) — neutron-only
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class EnergySpectraConfig:
    """Flux energy spectrum per material region. Neutron legs only.

    Generates one tally per material with a MaterialFilter + EnergyFilter.
    Cost: small per material, scales with number of groups.

    Fields:
        enabled:        Whether to generate this tally group. Forced False
                         and rejected at construction for a photon leg —
                         see ResultsConfig.__post_init__.
        group_structure: Which standard group structure to use.
        per_material:   If True, one spectrum per material. If False, global.
    """
    enabled:         bool                 = False
    group_structure: EnergyGroupStructure = EnergyGroupStructure.FINE_69
    per_material:    bool                 = True

    def boundaries(self) -> list[float]:
        """Return energy boundaries in eV for the selected group structure."""
        return _GROUP_BOUNDARIES.get(self.group_structure, [])


# ---------------------------------------------------------------------------
# Group 5 — Diagnostics (situational, low cost)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DiagnosticsConfig:
    """Debugging and verification quantities.

    Fields:
        stochastic_volumes:  Estimate cell volumes stochastically.
                             Useful when analytical volume is unknown.
        particle_tracks:     Record individual neutron histories.
                             WARNING: can produce large files (MBs per thousand particles).
                             Set n_tracks carefully.
        n_tracks:            Number of particle tracks to record.
    """
    stochastic_volumes: bool = False
    particle_tracks:    bool = False
    n_tracks:           int  = 100


# ---------------------------------------------------------------------------
# Top-level config object — scoped to ONE transport leg's particle type
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ResultsConfig:
    """Complete results capture configuration for one transport leg.

    Carried by SimulationJob (single-leg modes) or nested inside
    R2SResultsConfig (r2s's two legs) through to the adapter, which
    translates it into tallies.xml.

    `particle_type` is the scoping key introduced by the r2s restructure
    (job-settings-model.md §5): it determines which scores and group
    structures are legal, and is validated at construction time so an
    invalid combination (e.g. `fission` on a photon leg) fails immediately
    rather than producing nonsensical tallies.xml at staging time.

    Default: neutron leg, simulation summary + scalar tallies only — the
    best cost/information tradeoff for typical pin-cell studies.
    """
    particle_type: ParticleType        = ParticleType.NEUTRON
    summary:  SimulationSummaryConfig  = field(default_factory=SimulationSummaryConfig)
    scalars:  ScalarTallyConfig        = field(default_factory=ScalarTallyConfig)
    mesh:     MeshTallyConfig          = field(default_factory=MeshTallyConfig)
    spectra:  EnergySpectraConfig      = field(default_factory=EnergySpectraConfig)
    diagnostics: DiagnosticsConfig     = field(default_factory=DiagnosticsConfig)
    # Dose-conversion-weighted flux (job-settings-model.md §5's "dose
    # conversion factors" row) — only relevant score for shutdown-dose work
    # on a photon leg. Modelled as a flag rather than a TallyScore because
    # OpenMC implements it via an EnergyFunctionFilter wrapping a flux
    # score, not as a literal score name — see adapter for emission.
    apply_dose_conversion: bool = False

    def __post_init__(self) -> None:
        allowed = valid_scores(self.particle_type)

        bad_scalar = set(self.scalars.scores) - allowed
        if self.scalars.enabled and bad_scalar:
            raise ValueError(
                f"Scalar tally scores {sorted(s.value for s in bad_scalar)} "
                f"are not valid for a {self.particle_type.value} leg. "
                f"Valid scores: {sorted(s.value for s in allowed)}."
            )

        bad_mesh = set(self.mesh.scores) - allowed
        if self.mesh.enabled and bad_mesh:
            raise ValueError(
                f"Mesh tally scores {sorted(s.value for s in bad_mesh)} "
                f"are not valid for a {self.particle_type.value} leg. "
                f"Valid scores: {sorted(s.value for s in allowed)}."
            )

        if self.spectra.enabled and self.particle_type is ParticleType.PHOTON:
            raise ValueError(
                "Energy spectra use neutron multigroup library names and "
                "are not available on a photon leg (job-settings-model.md §5)."
            )

        if self.apply_dose_conversion:
            if self.particle_type is not ParticleType.PHOTON:
                raise ValueError(
                    "apply_dose_conversion is only meaningful on a photon leg."
                )
            if TallyScore.FLUX not in self.scalars.scores and TallyScore.FLUX not in self.mesh.scores:
                raise ValueError(
                    "apply_dose_conversion requires a `flux` score in "
                    "scalars or mesh — dose conversion wraps a flux tally, "
                    "it isn't a tally on its own."
                )

    def needs_tallies_xml(self) -> bool:
        """Return True if any group requires tallies.xml to be written."""
        return (
            self.scalars.enabled
            or self.mesh.enabled
            or self.spectra.enabled
        )

    def to_dict(self) -> dict:
        """Serialise to a JSON-compatible dict for DB storage."""
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> ResultsConfig:
        """Reconstruct from the dict produced by to_dict()."""
        return cls(
            particle_type=ParticleType(d.get("particle_type", "neutron")),
            summary=SimulationSummaryConfig(**d.get("summary", {})),
            scalars=ScalarTallyConfig(
                enabled=d["scalars"]["enabled"],
                scores=[TallyScore(s) for s in d["scalars"].get("scores", [])],
                all_cells=d["scalars"].get("all_cells", False),
            ),
            mesh=MeshTallyConfig(
                enabled=d["mesh"]["enabled"],
                mesh_type=MeshType(d["mesh"].get("mesh_type", "regular")),
                nx=d["mesh"].get("nx", 20),
                ny=d["mesh"].get("ny", 20),
                nz=d["mesh"].get("nz", 20),
                nr=d["mesh"].get("nr", 20),
                nz_cyl=d["mesh"].get("nz_cyl", 20),
                scores=[TallyScore(s) for s in d["mesh"].get("scores", [])],
            ),
            spectra=EnergySpectraConfig(
                enabled=d["spectra"]["enabled"],
                group_structure=EnergyGroupStructure(
                    d["spectra"].get("group_structure", "69")
                ),
                per_material=d["spectra"].get("per_material", True),
            ),
            diagnostics=DiagnosticsConfig(**d.get("diagnostics", {})),
            apply_dose_conversion=d.get("apply_dose_conversion", False),
        )

    @classmethod
    def default(cls, particle_type: ParticleType = ParticleType.NEUTRON) -> ResultsConfig:
        """Sensible defaults: summary + scalar tallies, nothing else."""
        return cls(particle_type=particle_type)


# ---------------------------------------------------------------------------
# r2s — per-leg results config (job-settings-model.md §1's key design decision)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class R2SResultsConfig:
    """Results capture for an r2s job — one config per transport leg.

    This is the single biggest structural fix in the r2s restructure: r2s
    must NEVER use a single global ResultsConfig, because its two legs
    have different particle types and therefore different valid scores
    (job-settings-model.md §1, §5).
    """
    neutron_leg: ResultsConfig
    photon_leg:  ResultsConfig

    def __post_init__(self) -> None:
        if self.neutron_leg.particle_type is not ParticleType.NEUTRON:
            raise ValueError("R2SResultsConfig.neutron_leg must have particle_type=NEUTRON.")
        if self.photon_leg.particle_type is not ParticleType.PHOTON:
            raise ValueError("R2SResultsConfig.photon_leg must have particle_type=PHOTON.")

    def to_dict(self) -> dict:
        return {
            "neutron_leg": self.neutron_leg.to_dict(),
            "photon_leg":  self.photon_leg.to_dict(),
        }

    @classmethod
    def from_dict(cls, d: dict) -> R2SResultsConfig:
        return cls(
            neutron_leg=ResultsConfig.from_dict(d["neutron_leg"]),
            photon_leg=ResultsConfig.from_dict(d["photon_leg"]),
        )

    @classmethod
    def default(cls) -> R2SResultsConfig:
        """Sensible r2s defaults: neutron-leg mesh (reaction rates feed
        the activation step) + photon-leg mesh with dose conversion on
        (the whole point of r2s is a dose map)."""
        neutron_leg = ResultsConfig(
            particle_type=ParticleType.NEUTRON,
            mesh=MeshTallyConfig(enabled=True, scores=[TallyScore.FLUX, TallyScore.HEATING_LOCAL]),
        )
        photon_leg = ResultsConfig(
            particle_type=ParticleType.PHOTON,
            scalars=ScalarTallyConfig(enabled=False),
            mesh=MeshTallyConfig(enabled=True, scores=[TallyScore.FLUX, TallyScore.HEATING]),
            apply_dose_conversion=True,
        )
        return cls(neutron_leg=neutron_leg, photon_leg=photon_leg)