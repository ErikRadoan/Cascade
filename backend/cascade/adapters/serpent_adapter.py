"""
Serpent adapter scaffold.

CHANGE (interface audit): was a bare `class SerpentAdapter: pass` — no
`name`, no methods. If anything ever used it through AdapterProtocol
(structural typing), it would have failed with an AttributeError on first
call instead of a clear "not implemented" message. Now shaped to match
AdapterProtocol (see adapters/base.py) with explicit NotImplementedError
stubs, so it fails loudly and specifically the moment it's actually used,
and can already sit in a simulator registry next to OpenMCAdapter.

TODO:
    - write_input_files(): Serpent uses a single flattened input deck
      (geometry `surf`/`cell` cards + `mat` cards + run options in one
      file), not separate geometry.xml/materials.xml/settings.xml —
      this needs its own card-based serializer, not a port of
      OpenMCAdapter's XML builders.
    - import_results(): Serpent writes _res.m/_det.m — MATLAB-style
      variable-assignment text, not an HDF5 statepoint — this needs its
      own text parser. None of OpenMCAdapter's h5py-based import_summary/
      import_tallies/import_mesh/import_spectra methods are reusable here.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ..domain.geometry import CascadeGeometry
from ..domain.material import Material


class SerpentAdapter:
    name = "serpent"

    def write_input_files(
        self,
        geometry: CascadeGeometry,
        materials: list[Material],
        output_dir: Path,
        settings: object | None = None,
        results_config: object | None = None,
    ) -> list[Path]:
        raise NotImplementedError(
            "SerpentAdapter.write_input_files() is not implemented yet."
        )

    def import_results(
        self,
        job_id: str,
        output_dir: Path,
        geometry: CascadeGeometry,
        materials: list[Material],
        results_config: object | None = None,
    ) -> dict[str, Any]:
        raise NotImplementedError(
            "SerpentAdapter.import_results() is not implemented yet."
        )