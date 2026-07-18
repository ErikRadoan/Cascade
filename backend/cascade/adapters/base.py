"""Adapter protocol for converting between IR and simulator-specific formats.

CHANGE (interface audit): the previous version of this protocol declared
    export_geometry(self, geometry) -> dict[str, object]
    import_results(self, payload) -> list[TallyResult]
which matched neither method on OpenMCAdapter — export_geometry() there
takes an extra required `material_id_map` arg and returns an XML *string*,
not a dict, and import_results() didn't exist at all (OpenMCAdapter instead
exposes four granular import_summary/import_tallies/import_mesh/
import_spectra methods, each parsing one section of an open HDF5
statepoint). Nothing in the codebase actually type-checked against this
Protocol, so it had quietly drifted into fiction.

This version is scoped to what's genuinely common across simulator
adapters:
    - write_input_files(): every backend needs to stage input files the
      same way, regardless of simulator (see OpenMCAdapter.write_input_files,
      used directly by DockerBackend today).
    - import_results(): one common re-entry point for "give me this job's
      parsed results" without the caller needing to know the simulator's
      native result-file format.

Result import is NOT forced into a single internal shape beyond that.
OpenMC's statepoints are HDF5 and split naturally into summary/tallies/
mesh/spectra; Serpent's _res.m/_det.m output is MATLAB-style text with a
completely different structure. Each adapter is free to expose extra,
simulator-specific methods beyond this Protocol (write_depletion_driver,
find_statepoint, open_statepoint, the individual import_* methods, ...) —
callers that need those import the concrete class directly, the way
docker_backend.py already does for write_depletion_driver().
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

from ..domain.geometry import CascadeGeometry
from ..domain.material import Material


class AdapterProtocol(Protocol):
    name: str

    def write_input_files(
        self,
        geometry: CascadeGeometry,
        materials: list[Material],
        output_dir: Path,
        settings: object | None = None,
        results_config: object | None = None,
    ) -> list[Path]:
        """Export and write a complete set of simulator input files.

        `settings` and `results_config` are intentionally typed as `object`
        here rather than OpenMC's concrete OpenMCRunSettings/ResultsConfig —
        a Serpent (or other) adapter's equivalent run-settings shape won't
        be the same type. Concrete adapters narrow these in their own
        method signatures.
        """
        ...

    def import_results(
        self,
        job_id: str,
        output_dir: Path,
        geometry: CascadeGeometry,
        materials: list[Material],
        results_config: object | None = None,
    ) -> dict[str, Any]:
        """Locate, open, and parse a completed run's output.

        `geometry`/`materials` are required (not just `output_dir`) because
        result parsing can depend on them — e.g. OpenMCAdapter.import_tallies
        reconstructs each tally's name by replicating the same fissile-cell
        selection loop used at export time, since OpenMC's statepoint.h5
        doesn't persist the tally's XML `name=` attribute.

        Returns a dict keyed by result section (e.g. "summary", "tallies",
        "mesh", "spectra") — see OpenMCAdapter.import_results for the
        concrete shape.
        """
        ...