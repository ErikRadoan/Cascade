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

Groups 4+ (MGXS, depletion) are intentionally excluded from v1 — they
require substantially more backend infrastructure and are better scoped
as a separate milestone.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


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
        k_effective:     Parse k-eff (all 3 estimators) + confidence intervals
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
        scores:     Which quantities to measure per cell.
        all_cells:  If True, tally every cell. If False, only fissile cells.
                    (Fissile = cells whose material contains U/Pu/Th nuclides.)
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

    Fields:
        enabled:     Whether to generate this tally.
        mesh_type:   Regular (Cartesian) or cylindrical.
        nx, ny, nz:  Number of mesh voxels (regular mesh).
        nr, nz_cyl: Number of radial/axial bins (cylindrical mesh).
        scores:      Which quantities to map.
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


# ---------------------------------------------------------------------------
# Group 4 — Energy spectra (flux vs energy, medium cost)
# ---------------------------------------------------------------------------

class EnergyGroupStructure(StrEnum):
    """Standard energy group structures for neutron flux spectra.

    BROAD_33:   33-group CASMO structure — good for LWR analysis
    FINE_69:    69-group WIMS structure — standard for PWR
    ULTRA_252:  252-group — high resolution, expensive
    CUSTOM:     User supplies breakpoints (not yet implemented in v1)
    """
    BROAD_33  = "33"
    FINE_69   = "69"
    ULTRA_252 = "252"


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


@dataclass(frozen=True)
class EnergySpectraConfig:
    """Flux energy spectrum per material region.

    Generates one tally per material with a MaterialFilter + EnergyFilter.
    Cost: small per material, scales with number of groups.

    Fields:
        enabled:        Whether to generate this tally group.
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
# Top-level config object
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ResultsConfig:
    """Complete results capture configuration for one simulation job.

    Carried by SimulationJob from submission through to adapter.
    The adapter translates this into tallies.xml and adjusts settings.xml
    (e.g. enabling stochastic volumes requires a <volume_calc> block).

    Default: simulation summary + scalar tallies only — the best
    cost/information tradeoff for typical pin-cell studies.
    """
    summary:  SimulationSummaryConfig = field(default_factory=SimulationSummaryConfig)
    scalars:  ScalarTallyConfig       = field(default_factory=ScalarTallyConfig)
    mesh:     MeshTallyConfig         = field(default_factory=MeshTallyConfig)
    spectra:  EnergySpectraConfig     = field(default_factory=EnergySpectraConfig)
    diagnostics: DiagnosticsConfig    = field(default_factory=DiagnosticsConfig)

    def needs_tallies_xml(self) -> bool:
        """Return True if any group requires tallies.xml to be written."""
        return (
            self.scalars.enabled
            or self.mesh.enabled
            or self.spectra.enabled
        )

    def to_dict(self) -> dict:
        """Serialise to a JSON-compatible dict for DB storage."""
        import dataclasses
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> ResultsConfig:
        """Reconstruct from the dict produced by to_dict()."""
        return cls(
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
        )

    @classmethod
    def default(cls) -> ResultsConfig:
        """Sensible defaults: summary + scalar tallies, nothing else."""
        return cls()