"""OpenMC adapter — converts CascadeGeometry to OpenMC XML input files.

Generates three XML files that OpenMC requires:
    geometry.xml    — surfaces and cells
    materials.xml   — material compositions and densities
    settings.xml    — run parameters (particles, batches, etc.)

Design notes:
    - No dependency on the openmc Python package. The XML is built as strings.
    - Material definitions are referenced by material_id strings from the
      domain model. Full material XML is generated from the project's
      material library (passed separately to export_materials).
    - The adapter is stateless. Every method is a pure function of its inputs.

OpenMC XML reference:
    https://docs.openmc.org/en/stable/usersguide/geometry.html
    https://docs.openmc.org/en/stable/usersguide/materials.html
    https://docs.openmc.org/en/stable/usersguide/settings.html
"""

from __future__ import annotations

import math
import re
import textwrap
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET
from xml.dom import minidom

import h5py
import numpy as np

from ..domain.geometry import (
    CascadeGeometry,
    Cell,
    Complement,
    Inside,
    Intersection,
    Outside,
    Region,
    Surface,
    SurfaceType,
    Union, BoundaryType,
)
from ..domain.material import Material
from ..domain.results_config import (
    DiagnosticsConfig,
    EnergySpectraConfig,
    MeshTallyConfig,
    MeshType,
    ParticleType,
    ResultsConfig,
    ScalarTallyConfig,
    TallyScore,
)
from ..domain.run_settings import DepletionSettings, McSettings, RunMode, SourceDef, SourceSpaceType


# ---------------------------------------------------------------------------
# Run settings dataclass — what a SINGLE OpenMC transport leg needs
# ---------------------------------------------------------------------------
#
# OpenMC itself only has two run modes at the settings.xml level:
# "eigenvalue" and "fixed source". Cascade's higher-level RunMode
# (eigenvalue / fixed_source / depletion / r2s — see domain/run_settings.py)
# decomposes into one or more calls into THIS adapter, each producing one
# OpenMCRunSettings for one transport leg:
#   - eigenvalue   -> one call, run_mode="eigenvalue"
#   - fixed_source -> one call, run_mode="fixed source", with a real source
#   - depletion    -> NOT expressible as repeated calls to this adapter.
#                     Each step's materials depend on the previous step's
#                     reaction rates, which requires OpenMC's Python
#                     depletion API (openmc.deplete), not XML files driven
#                     by the CLI binary. See execution/docker_backend.py.
#   - r2s          -> neutron leg: one call, run_mode="fixed source".
#                     photon leg: one call, run_mode="fixed source", with a
#                     source derived from the (not-yet-implemented)
#                     activation step. See execution/docker_backend.py.

@dataclass
class OpenMCRunSettings:
    """Parameters for ONE OpenMC transport leg.

    These map directly to OpenMC's settings.xml fields. Defaults are
    conservative — fast enough for geometry checking, not production
    quality. Users should increase for real results.

    Attributes:
        particles:      Neutrons (or photons) per batch.
        inactive:       Inactive (warmup) batches — discarded from tallies.
                        None for a fixed-source leg (job-settings-model.md §2).
        batches:        Total batches (inactive + active, if inactive is set).
        seed:           Random number seed. Fixed default for reproducibility.
        run_mode:       "eigenvalue" for criticality, "fixed source" for shielding.
                        These are OpenMC's own two values — see module docstring.
        source:         User-specified source. Required when run_mode is
                        "fixed source"; must be None for "eigenvalue" (the
                        criticality source is geometry-driven — see
                        `source_box` / `_compute_fissile_source_box`).
        energy_groups:  Number of energy groups for multi-group mode.
                        None = continuous energy (default, recommended).
        source_box:     Manual override for the eigenvalue mode's
                        auto-detected fissile source box. Ignored when
                        `source` is set.
    """
    particles: int = 1000
    inactive: int | None = 20
    batches: int = 100
    seed: int = 1
    run_mode: str = "eigenvalue"
    source: SourceDef | None = None
    energy_groups: int | None = None
    source_box: tuple[float, ...] | None = None

    def __post_init__(self):
        if self.run_mode not in ("eigenvalue", "fixed source"):
            raise ValueError(
                f"run_mode must be 'eigenvalue' or 'fixed source', "
                f"got '{self.run_mode}'. (Cascade's `depletion`/`r2s` modes "
                f"decompose into one or more single-leg calls with this "
                f"value — see module docstring.)"
            )
        if self.run_mode == "eigenvalue":
            if self.inactive is None:
                raise ValueError("eigenvalue run_mode requires `inactive` to be set.")
            if self.inactive >= self.batches:
                raise ValueError(
                    f"inactive batches ({self.inactive}) must be less than "
                    f"total batches ({self.batches})."
                )
            if self.source is not None:
                raise ValueError(
                    "eigenvalue run_mode's source is geometry-driven (auto-"
                    "detected from fissile cells, or `source_box` override) "
                    "— do not pass `source`."
                )
        else:  # fixed source
            if self.inactive is not None:
                raise ValueError(
                    "fixed source run_mode cannot have `inactive` batches — "
                    "there is no source convergence to discard warmup "
                    "batches for (job-settings-model.md §2)."
                )
            if self.source is None:
                raise ValueError(
                    "fixed source run_mode requires `source` to be set "
                    "(job-settings-model.md §3.2 — previously this was "
                    "impossible to submit at all)."
                )

    @classmethod
    def for_leg(
        cls,
        mc: McSettings,
        run_mode: str,
        *,
        source: SourceDef | None = None,
        source_box: tuple[float, ...] | None = None,
    ) -> OpenMCRunSettings:
        """Build single-leg settings from the higher-level McSettings/SourceDef.

        Bridges domain/run_settings.py (job-level, multi-mode-aware) to this
        adapter's OpenMCRunSettings (single-leg, OpenMC-XML-shaped).
        """
        openmc_run_mode = "eigenvalue" if run_mode == RunMode.EIGENVALUE else "fixed source"
        return cls(
            particles=mc.particles,
            inactive=mc.inactive,
            batches=mc.batches,
            seed=mc.seed,
            run_mode=openmc_run_mode,
            source=source,
            source_box=source_box,
        )


# ---------------------------------------------------------------------------
# Surface type mapping
# ---------------------------------------------------------------------------

# Maps our SurfaceType enum to OpenMC's surface type string.
# Reference: https://docs.openmc.org/en/stable/io_formats/geometry.html
_SURFACE_TYPE_MAP: dict[SurfaceType, str] = {
    SurfaceType.PLANE_X:    "x-plane",
    SurfaceType.PLANE_Y:    "y-plane",
    SurfaceType.PLANE_Z:    "z-plane",
    SurfaceType.CYLINDER_X: "x-cylinder",
    SurfaceType.CYLINDER_Y: "y-cylinder",
    SurfaceType.CYLINDER_Z: "z-cylinder",
    SurfaceType.SPHERE:     "sphere",
    SurfaceType.CONE_Z:     "z-cone",
    SurfaceType.TORUS:      "z-torus",
}

# Maps our SurfaceType to the ordered parameter names OpenMC expects in coeffs.
# The expander may use shorthand keys ("z", "r") — we normalize these below
# via _PARAM_ALIASES before building the coeffs string.
_SURFACE_PARAMS_MAP: dict[SurfaceType, list[str]] = {
    SurfaceType.PLANE_X:    ["x0"],
    SurfaceType.PLANE_Y:    ["y0"],
    SurfaceType.PLANE_Z:    ["z0"],
    SurfaceType.CYLINDER_X: ["y0", "z0", "r"],
    SurfaceType.CYLINDER_Y: ["x0", "z0", "r"],
    SurfaceType.CYLINDER_Z: ["x0", "y0", "r"],
    SurfaceType.SPHERE:     ["x0", "y0", "z0", "r"],
    SurfaceType.CONE_Z:     ["x0", "y0", "z0", "r2"],
    SurfaceType.TORUS:      ["x0", "y0", "z0", "a", "b", "c"],
}

# Aliases: the expander uses short param names; map them to canonical names.
# e.g. the expander writes params={"z": 0.0} but the canonical name is "z0".
_PARAM_ALIASES: dict[str, str] = {
    "x": "x0",
    "y": "y0",
    "z": "z0",
}

# Default parameter values when not explicitly specified.
_SURFACE_PARAM_DEFAULTS: dict[str, float] = {
    "x0": 0.0, "y0": 0.0, "z0": 0.0,
    "r": 1.0, "r2": 1.0,
    "a": 1.0, "b": 0.5, "c": 0.5,
}


# ---------------------------------------------------------------------------
# ID helpers
# ---------------------------------------------------------------------------

def _int_id(id_val: str | int) -> str:
    """Strip any leading alpha/underscore prefix and return the integer portion.

    The domain model uses prefixed string IDs ('s1', 'c6') for readability.
    OpenMC's XML parser requires bare integers everywhere an ID appears.

    Examples:
        's1'   -> '1'
        'c12'  -> '12'
        's_3'  -> '3'
        '42'   -> '42'

    Args:
        id_val: Surface or cell ID from the domain model.

    Returns:
        String containing only the integer digits.

    Raises:
        ValueError: If the result is empty (no digits found in id_val).
    """
    result = re.sub(r"^[a-zA-Z_]+", "", str(id_val))
    if not result:
        raise ValueError(
            f"Could not extract an integer ID from '{id_val}'. "
            f"IDs must contain at least one digit."
        )
    return result


def _resolve_param(params: dict, canonical_name: str) -> float:
    """Look up a surface parameter, accepting both canonical and alias names.

    OpenMC canonical names are like 'x0', 'y0', 'z0'. The expander may
    write shorthand like 'x', 'y', 'z'. We check both before falling
    back to the default.

    Args:
        params:         Surface.params dict from the domain model.
        canonical_name: The canonical parameter name ('x0', 'z0', etc.).

    Returns:
        The parameter value as a float.
    """
    # Try canonical name first ('z0')
    if canonical_name in params:
        return float(params[canonical_name])

    # Try reverse-alias ('z0' -> check if 'z' is in params)
    for alias, canon in _PARAM_ALIASES.items():
        if canon == canonical_name and alias in params:
            return float(params[alias])

    # Fall back to default
    return _SURFACE_PARAM_DEFAULTS.get(canonical_name, 0.0)


# ---------------------------------------------------------------------------
# Region expression serializer
# ---------------------------------------------------------------------------

def _region_to_openmc(region: Region) -> str:
    """Recursively convert a Region expression tree to an OpenMC region string.

    OpenMC region syntax:
        -N   inside surface N  (negative halfspace)
        +N   outside surface N (positive halfspace)
        A B  intersection (space-separated, implicit AND)
        A | B  union
        ~A   complement

    Surface IDs are converted from prefixed strings ('s1') to bare integers
    ('1') since OpenMC's parser requires integer IDs throughout.

    Args:
        region: Any Region subclass from domain.geometry.

    Returns:
        OpenMC-compatible region string.

    Raises:
        TypeError: If an unknown Region subclass is encountered.
    """
    if isinstance(region, Inside):
        return f"-{_int_id(region.surface_id)}"

    elif isinstance(region, Outside):
        return f"+{_int_id(region.surface_id)}"

    elif isinstance(region, Intersection):
        if not region.regions:
            return ""
        parts = [_region_to_openmc(r) for r in region.regions]
        inner = " ".join(parts)
        return f"({inner})" if len(region.regions) > 1 else inner

    elif isinstance(region, Union):
        if not region.regions:
            return ""
        parts = [_region_to_openmc(r) for r in region.regions]
        inner = " | ".join(parts)
        return f"({inner})" if len(region.regions) > 1 else inner

    elif isinstance(region, Complement):
        inner = _region_to_openmc(region.region)
        return f"~{inner}"

    else:
        raise TypeError(
            f"Unknown Region type: {type(region).__name__}. "
            f"Add it to _region_to_openmc() in openmc_adapter.py."
        )


# ---------------------------------------------------------------------------
# XML building helpers
# ---------------------------------------------------------------------------

def _pretty_xml(element: ET.Element) -> str:
    """Serialize an ElementTree element to a pretty-printed XML string.

    Args:
        element: Root XML element.

    Returns:
        UTF-8 XML string with 2-space indentation and XML declaration.
    """
    raw = ET.tostring(element, encoding="unicode")
    reparsed = minidom.parseString(raw)
    pretty = reparsed.toprettyxml(indent="  ")
    # toprettyxml adds its own declaration — strip it, we add our own
    lines = pretty.split("\n")
    if lines[0].startswith("<?xml"):
        lines = lines[1:]
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + "\n".join(lines)


class _FilterRegistry:
    """Collects top-level <filter id="N" type="..."><bins>...</bins></filter>
    elements for a tallies.xml document and hands back the integer id for
    each one, so callers can reference it from a <tally>'s <filters> list.

    OpenMC's tallies.xml format requires filters to be declared as their
    own top-level elements under <tallies>, referenced by id from each
    <tally>'s <filters> list — NOT nested inline inside the tally with
    type/bins as attributes on a bare <filter> tag. The inline shape is
    silently ignored by OpenMC's parser (no error — it just treats the
    tally as filterless and scores the whole geometry), which is exactly
    the "n_filters: 0 on every tally" bug this registry fixes.
    """

    def __init__(self, root: ET.Element):
        self._root = root
        self._next_id = 1

    def add(self, filter_type: str, bins: str) -> int:
        """Append a new top-level <filter> element and return its id."""
        filter_id = self._next_id
        self._next_id += 1

        el = ET.Element("filter")
        el.set("id", str(filter_id))
        el.set("type", filter_type)
        bins_el = ET.SubElement(el, "bins")
        bins_el.text = bins
        self._root.append(el)

        return filter_id


def _surface_element(surface: Surface) -> ET.Element:
    """Build an XML <surface> element from a Surface domain object.

    OpenMC expects:
        - id:     bare integer (no prefix)
        - type:   OpenMC surface type string
        - coeffs: single space-separated string of parameter values
                  in the canonical order defined by _SURFACE_PARAMS_MAP

    Args:
        surface: Surface domain object.

    Returns:
        ET.Element for insertion into the geometry XML tree.

    Raises:
        KeyError: If surface.type_ is not in _SURFACE_TYPE_MAP.
    """
    openmc_type = _SURFACE_TYPE_MAP.get(surface.type_)
    if openmc_type is None:
        raise KeyError(
            f"Surface type '{surface.type_}' has no OpenMC mapping. "
            f"Add it to _SURFACE_TYPE_MAP in openmc_adapter.py."
        )

    el = ET.Element("surface")
    el.set("id", _int_id(surface.id))
    el.set("type", openmc_type)

    # Build coeffs: space-separated values in canonical parameter order.
    # Uses _resolve_param so both 'z' and 'z0' are accepted from the expander.
    expected_params = _SURFACE_PARAMS_MAP.get(surface.type_, [])
    if expected_params:
        coeffs = " ".join(
            str(_resolve_param(surface.params, p)) for p in expected_params
        )
        el.set("coeffs", coeffs)

    if surface.boundary_type != BoundaryType.NONE:
        el.set("boundary", surface.boundary_type.value)

    return el


def _cell_element(cell: Cell, material_id_map: dict[str, int]) -> ET.Element:
    """Build an XML <cell> element from a Cell domain object.

    Args:
        cell: Cell domain object.
        material_id_map: Maps material_id strings to integer IDs for OpenMC.
                         OpenMC requires integer material IDs in geometry.xml.

    Returns:
        ET.Element for insertion into the geometry XML tree.

    Raises:
        ValueError: If cell references a material not in material_id_map.
    """
    el = ET.Element("cell")
    el.set("id", _int_id(cell.id))

    if cell.name:
        el.set("name", cell.name)

    if cell.material_id is not None:
        mat_int_id = material_id_map.get(cell.material_id)
        if mat_int_id is None:
            raise ValueError(
                f"Cell '{cell.id}' references material '{cell.material_id}' "
                f"which is not in the material library. "
                f"Add it to the project's materials before exporting."
            )
        el.set("material", str(mat_int_id))
    else:
        el.set("material", "void")

    el.set("region", _region_to_openmc(cell.region))

    return el


_INTEGRATOR_CLASSES = {
    "predictor": "PredictorIntegrator",
    "cecm":      "CECMIntegrator",
    "celi":      "CELIIntegrator",
}


def _render_depletion_driver_script(
    mc: McSettings,
    depletion: DepletionSettings,
    chain_file_container_path: str,
) -> str:
    """Render a standalone Python script that runs one depletion job.

    The script loads geometry.xml/materials.xml/settings.xml (written
    alongside it by write_depletion_driver) via openmc.Model.from_xml(),
    wraps that model in an openmc.deplete.CoupledOperator against the
    chain file, and hands the timestep loop to the chosen integrator.
    This is the ENTIRE depletion calculation — Cascade does not call back
    into this process or re-invoke it per timestep; the integrator's
    `.integrate()` call owns that loop internally and blocks until every
    timestep is done, exactly like a single `openmc` CLI invocation blocks
    until a transport run finishes.

    Depletable materials need a `.volume` set (OpenMC computes total atom
    inventory as density × volume, and has no way to infer volume from
    geometry on its own — this is a separate requirement from just marking
    a material `depletable="true"` in materials.xml). Rather than require
    the user to supply an analytic volume for every fissile material (hard
    to get right for anything but trivial shapes, and Cascade's geometry
    model supports arbitrary surface unions/intersections/complements —
    see domain/geometry.py — where "analytic volume" isn't generally
    tractable), the generated script runs OpenMC's own stochastic volume
    calculation over the model's bounding box before constructing the
    CoupledOperator, and lets OpenMC assign `.volume` on each depletable
    material automatically. This needs no new input from the user and no
    new Cascade-side geometry math.

    Raises inside the generated script (not here) if `depletion.integrator`
    doesn't match a known openmc.deplete integrator class — fails loudly
    inside the container log rather than silently picking a default. Also
    raises inside the generated script if the geometry has no finite
    bounding box (e.g. missing a vacuum-bounded outer surface) — the
    volume calculation needs a finite region to sample within.
    """
    integrator_cls = _INTEGRATOR_CLASSES.get(depletion.integrator)
    if integrator_cls is None:
        raise ValueError(
            f"Unknown depletion integrator '{depletion.integrator}'. "
            f"Supported: {sorted(_INTEGRATOR_CLASSES)}."
        )

    # power_W is held constant across all timesteps (see DepletionSettings
    # docstring — time-varying power is a documented future extension, not
    # supported here). openmc.deplete wants one power value per timestep.
    power_list = [depletion.power_W] * len(depletion.timesteps)

    # Stochastic volume calculation sample count. Fixed for now rather
    # than user-configurable — 100k samples is enough for a reasonable
    # volume estimate on typical pin-cell/assembly-scale geometry without
    # adding meaningfully to total runtime. Revisit if larger/more complex
    # geometries need more samples for acceptable volume uncertainty.
    volume_calc_samples = 100_000

    return f'''"""Auto-generated by Cascade's OpenMCAdapter — do not edit by hand.

Runs one depletion job end-to-end: builds the model from the sibling
geometry.xml/materials.xml/settings.xml, computes volumes for depletable
materials (required by openmc.deplete but not set by materials.xml alone),
then hands the timestep loop to openmc.deplete's {integrator_cls}. This
process owns the ENTIRE depletion calculation; nothing outside this script
calls back into it mid-run.
"""
import numpy as np
import openmc
import openmc.deplete

model = openmc.Model.from_xml(
    geometry="geometry.xml",
    materials="materials.xml",
    settings="settings.xml",
)

# --- Volume calculation for depletable materials ---------------------------
# openmc.deplete needs material.volume set to convert atom density into a
# total atom inventory; it cannot infer this from geometry on its own.
# Cascade marks fissile materials depletable="true" in materials.xml at
# export time but does not compute an analytic volume (not generally
# tractable for arbitrary surface unions/intersections), so we run OpenMC's
# own stochastic volume calculation here instead.
depletable_mats = [m for m in model.materials if m.depletable]
if not depletable_mats:
    raise RuntimeError(
        "No depletable materials found after loading the model — this "
        "should not happen (Cascade marks fissile materials depletable "
        "at export time). Check materials.xml."
    )

bbox = model.geometry.bounding_box
if not (np.all(np.isfinite(bbox.lower_left)) and np.all(np.isfinite(bbox.upper_right))):
    raise RuntimeError(
        "Geometry has no finite bounding box, so the stochastic volume "
        "calculation depletion requires cannot sample within a region. "
        "Add a vacuum-bounded outer surface to the geometry."
    )

vol_calc = openmc.VolumeCalculation(
    depletable_mats, {volume_calc_samples!r}, bbox.lower_left, bbox.upper_right,
)
model.settings.volume_calculations = [vol_calc]
model.calculate_volumes()  # runs OpenMC in volume-calc mode, sets .volume on each material in-place

# --- Depletion ---------------------------------------------------------
chain_file = {chain_file_container_path!r}

operator = openmc.deplete.CoupledOperator(model, chain_file)

timesteps = {depletion.timesteps!r}
power     = {power_list!r}

integrator = openmc.deplete.{integrator_cls}(
    operator,
    timesteps,
    power=power,
    timestep_units="d",
)

integrator.integrate()
'''


def _source_element(source: SourceDef) -> ET.Element:
    """Build an XML <source> element from a SourceDef.

    Used for `fixed source` run_mode (job-settings-model.md §3.2) — both
    Cascade's `fixed_source` mode and r2s's neutron/photon legs route
    through this once they reach the adapter as a single transport leg.

    Handles both analytic sources (point/box, user-specified) and file
    sources (r2s's photon leg — the activation step writes a source
    distribution file, and DockerBackend builds a SourceDef(space_type=
    'file', file_path=...) pointing at it before staging the photon leg;
    see execution/docker_backend.py).

    Args:
        source: SourceDef domain object (domain/run_settings.py).

    Returns:
        ET.Element for insertion as a child of <settings>.
    """
    if source.space_type == SourceSpaceType.FILE:
        # OpenMC file source: <source strength="1.0" file="source.h5"/> —
        # no <space>/<energy> sub-elements; the file carries the full
        # phase-space distribution (position, direction, energy, weight).
        source_el = ET.Element("source")
        source_el.set("file", source.file_path)
        return source_el

    source_el = ET.Element("source")
    source_el.set("particle", source.particle)

    space_el = ET.SubElement(source_el, "space")
    if source.space_type == SourceSpaceType.POINT:
        space_el.set("type", "point")
        # OpenMC point source: <parameters>x y z</parameters>
        params_el = ET.SubElement(space_el, "parameters")
        params_el.text = " ".join(str(v) for v in source.space_params)
    else:  # box
        space_el.set("type", "box")
        params_el = ET.SubElement(space_el, "parameters")
        params_el.text = " ".join(str(v) for v in source.space_params)

    if source.energy_mev is not None:
        # Monoenergetic source — OpenMC's <energy type="discrete"> with a
        # single energy bin (eV) and probability 1.0.
        energy_el = ET.SubElement(source_el, "energy")
        energy_el.set("type", "discrete")
        x_el = ET.SubElement(energy_el, "parameters")
        x_el.text = f"{source.energy_mev * 1.0e6}"
        p_el = ET.SubElement(energy_el, "p")
        p_el.text = "1.0"
    # else: OpenMC default spectrum (Watt fission spectrum for neutrons).
    # __post_init__ on SourceDef already forbids this for photon sources.

    return source_el


# Nuclides that make a material fissile
_FISSILE_NUCLIDES = frozenset({
    "U233", "U235", "U238",  # uranium
    "Pu238", "Pu239", "Pu240", "Pu241", "Pu242",  # plutonium
    "Th232",  # thorium (relevant for your LFTR work too)
    "Am241", "Cm244",  # minor actinides
})


def _is_fissile(material: Material) -> bool:
    return any(nuc in _FISSILE_NUCLIDES for nuc in material.composition)


def _compute_fissile_source_box(
        geometry: CascadeGeometry,
        materials: list[Material],
) -> tuple[float, ...]:
    """Compute a source box bounding all fissile cells.

    Finds every cell whose material is fissile, then computes the
    bounding box of those cells from their surface parameters — including
    each cylinder's (x0, y0) center, not just its radius. A lattice's
    fissile cylinders are NOT all centered at the origin (see expander.py's
    _place_fuel_pin, which offsets each pin's radial surfaces by that
    pin's placement position) — bounding only by radius silently collapses
    the box onto whichever pin happens to sit at (0, 0) and starves every
    other pin of source neutrons in the run's initial fission-source guess.
    Contracts the box by 1% on each side to ensure particles are
    born strictly inside surfaces, never on them.
    """
    fissile_ids = {m.id for m in materials if _is_fissile(m)}
    fissile_cells = [c for c in geometry.cells if c.material_id in fissile_ids]

    if not fissile_cells:
        raise ValueError(
            "No fissile cells found in geometry. Cannot auto-compute "
            "source distribution. Specify source_box in OpenMCRunSettings."
        )

    # Collect cylinder and plane surfaces that bound fissile cells
    surface_map = {s.id: s for s in geometry.surfaces}

    x_min = float('inf')
    x_max = float('-inf')
    y_min = float('inf')
    y_max = float('-inf')
    z_min = float('inf')
    z_max = float('-inf')

    for cell in fissile_cells:
        for sid in _surface_ids_in_region(cell.region):
            surf = surface_map.get(sid)
            if surf is None:
                continue
            if surf.type_ == SurfaceType.CYLINDER_Z:
                x0 = float(surf.params.get("x0", surf.params.get("x", 0.0)))
                y0 = float(surf.params.get("y0", surf.params.get("y", 0.0)))
                r  = float(surf.params.get("r", 0.0))
                x_min = min(x_min, x0 - r)
                x_max = max(x_max, x0 + r)
                y_min = min(y_min, y0 - r)
                y_max = max(y_max, y0 + r)
            elif surf.type_ == SurfaceType.PLANE_Z:
                z = float(surf.params.get("z", surf.params.get("z0", 0)))
                z_min = min(z_min, z)
                z_max = max(z_max, z)

    if x_min == float('inf') or z_min == float('inf'):
        raise ValueError(
            "Could not determine fissile cell bounds from geometry surfaces. "
            "Specify source_box in OpenMCRunSettings."
        )

    # Contract by 1% to keep particles off surfaces — shrink each axis
    # toward its OWN bounding box center, not toward the origin, since
    # the box is generally not centered on (0, 0) for a lattice.
    shrink = 0.99
    cx, cy = (x_min + x_max) / 2, (y_min + y_max) / 2
    hx, hy = (x_max - x_min) / 2 * shrink, (y_max - y_min) / 2 * shrink
    dz = (z_max - z_min) * 0.01  # 1% inset from each z face

    return (cx - hx, cy - hy, z_min + dz, cx + hx, cy + hy, z_max - dz)


def _surface_ids_in_region(region) -> list[str]:
    """Recursively collect all surface IDs referenced in a region."""
    from ..domain.geometry import Inside, Outside, Intersection, Union, Complement
    if isinstance(region, (Inside, Outside)):
        return [region.surface_id]
    elif isinstance(region, (Intersection, Union)):
        ids = []
        for r in region.regions:
            ids.extend(_surface_ids_in_region(r))
        return ids
    elif isinstance(region, Complement):
        return _surface_ids_in_region(region.region)
    return []

def _geometry_z_bounds(geometry: CascadeGeometry) -> tuple[float, float]:
    """Return (z_min, z_max) from PLANE_Z surfaces."""
    zvals = [
        float(s.params.get("z", s.params.get("z0", 0.0)))
        for s in geometry.surfaces
        if s.type_ == SurfaceType.PLANE_Z
    ]
    if not zvals:
        return (-10.0, 10.0)
    return (min(zvals), max(zvals))


def _geometry_r_max(geometry: CascadeGeometry) -> float:
    """Return the maximum radius from CYLINDER_Z surfaces."""
    radii = [
        float(s.params.get("r", 1.0))
        for s in geometry.surfaces
        if s.type_ == SurfaceType.CYLINDER_Z
    ]
    return max(radii) if radii else 10.0


def _geometry_bounds(geometry: CascadeGeometry) -> tuple[float, ...]:
    """Return (xmin, ymin, zmin, xmax, ymax, zmax) enclosing all surfaces."""
    r = _geometry_r_max(geometry)
    z_min, z_max = _geometry_z_bounds(geometry)
    return (-r, -r, z_min, r, r, z_max)


def _linspace_str(start: float, stop: float, n: int) -> str:
    """Return n evenly-spaced values from start to stop as a space-separated string."""
    if n < 2:
        return f"{start} {stop}"
    step = (stop - start) / (n - 1)
    return " ".join(f"{start + i * step:.6g}" for i in range(n))


# ---------------------------------------------------------------------------
# Main adapter class
# ---------------------------------------------------------------------------

class OpenMCAdapter:
    """Converts CascadeGeometry and materials to OpenMC XML input files.

    Usage:
        adapter = OpenMCAdapter()
        files = adapter.export(geometry, materials, settings)
        # files is a dict: {"geometry.xml": "...", "materials.xml": "...", "settings.xml": "..."}
        # Write each value to the job working directory before running OpenMC.
    """

    name = "openmc"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def export(
        self,
        geometry: CascadeGeometry,
        materials: list[Material],
        settings: OpenMCRunSettings | None = None,
        results_config: ResultsConfig | None = None,
        depletable_ids: set[str] | None = None,
    ) -> dict[str, str]:
        """Export a complete set of OpenMC input files.

        Args:
            geometry: Resolved CascadeGeometry (surfaces + cells).
            materials: All materials referenced by cells in this geometry.
                       Any material_id used in a cell must appear here.
            settings: Run parameters. Uses conservative defaults if None.
            results_config: What to capture. If None, uses ResultsConfig.default()
                            (summary + scalar tallies). Produces tallies.xml when
                            any tally group is enabled.
            depletable_ids: See export_materials(). None for every run mode
                            except depletion — see write_depletion_driver().

        Returns:
            Dict mapping filename to XML string content:
                "geometry.xml"  — surface and cell definitions
                "materials.xml" — material compositions
                "settings.xml"  — Monte Carlo run parameters
                "tallies.xml"   — tally definitions (omitted if nothing to tally)

        Raises:
            ValueError: If a cell references a material not in the materials list.
            KeyError: If a surface type has no OpenMC mapping.
        """
        if settings is None:
            settings = OpenMCRunSettings()
        if results_config is None:
            results_config = ResultsConfig.default()

        if getattr(results_config, "apply_dose_conversion", False):
            # TODO: implement via an EnergyFunctionFilter wrapping the flux
            # score with ICRP-116 (or similar) dose conversion factors.
            # Not yet implemented — fail loudly rather than silently
            # producing a plain flux tally that LOOKS like a dose result
            # but isn't weighted. This is exactly the failure mode that
            # caused the original "settings dropped on the floor" bug;
            # we don't want to reintroduce it for a new field.
            raise NotImplementedError(
                "ResultsConfig.apply_dose_conversion is set but dose-"
                "conversion XML emission (EnergyFunctionFilter + ICRP dose "
                "factors) is not implemented yet. Disable it or implement "
                "_append_dose_conversion_filter() before submitting."
            )

        material_id_map = {mat.id: i + 1 for i, mat in enumerate(materials)}

        files: dict[str, str] = {
            "geometry.xml": self.export_geometry(geometry, material_id_map),
            "materials.xml": self.export_materials(materials, material_id_map, depletable_ids),
            "settings.xml": self.export_settings(
                settings, geometry, materials, results_config
            ),
        }

        if results_config.needs_tallies_xml():
            files["tallies.xml"] = self.export_tallies(
                results_config, geometry, materials, material_id_map
            )

        return files

    # ------------------------------------------------------------------
    # Tallies XML
    # ------------------------------------------------------------------

    def export_tallies(
        self,
        config: ResultsConfig,
        geometry: CascadeGeometry,
        materials: list[Material],
        material_id_map: dict[str, int] | None = None,
    ) -> str:
        """Serialize ResultsConfig to OpenMC tallies.xml.

        Tally IDs are assigned in fixed order so that result parsing can
        reconstruct which tally corresponds to which config group without
        storing a mapping in the DB:
            1xx — scalar cell tallies   (101, 102, … one per cell)
            200 — mesh tally
            3xx — energy spectra        (301, 302, … one per material)

        Filter IDs are assigned separately (starting at 1) via a shared
        _FilterRegistry, and each <tally> references its filter(s) by id
        through its <filters> element — this is the top-level-filter shape
        OpenMC's parser actually requires (see _FilterRegistry docstring).

        Args:
            config:          ResultsConfig carrying user tally choices.
            geometry:        CascadeGeometry — needed for cell IDs and bounds.
            materials:       Material list — needed for spectra filters.
            material_id_map: String material ID → integer OpenMC ID.
                             Generated from materials if None.

        Returns:
            tallies.xml content as a string.
        """
        if material_id_map is None:
            material_id_map = {mat.id: i + 1 for i, mat in enumerate(materials)}

        root = ET.Element("tallies")
        filters = _FilterRegistry(root)

        # --- Group 2: scalar cell tallies -----------------------------------
        if config.scalars.enabled:
            self._append_scalar_tallies(
                root, config.scalars, geometry, materials, material_id_map, filters
            )

        # --- Group 3: mesh tally --------------------------------------------
        if config.mesh.enabled:
            self._append_mesh_tally(root, config.mesh, geometry, filters)

        # --- Group 4: energy spectra ----------------------------------------
        if config.spectra.enabled:
            self._append_spectra_tallies(
                root, config.spectra, materials, material_id_map, filters
            )

        return _pretty_xml(root)

    def _append_scalar_tallies(
        self,
        root: ET.Element,
        cfg: ScalarTallyConfig,
        geometry: CascadeGeometry,
        materials: list[Material],
        material_id_map: dict[str, int],
        filters: _FilterRegistry,
    ) -> None:
        """Append one tally per cell (or fissile cell) to *root*."""
        fissile_ids: set[str] = set()
        if not cfg.all_cells:
            fissile_ids = {m.id for m in materials if _is_fissile(m)}

        tally_id = 101
        for cell in geometry.cells:
            # Skip void cells — they carry no material
            if cell.material_id is None:
                continue
            # If restricted to fissile cells, skip non-fissile
            if not cfg.all_cells and cell.material_id not in fissile_ids:
                continue

            tally_el = ET.SubElement(root, "tally")
            tally_el.set("id", str(tally_id))
            # The tally's `name` attribute is free text (unlike `id`, which
            # OpenMC requires as a bare int) — use cell.name directly rather
            # than wrapping cell.id, since expander.py now sets cell.name to
            # exactly the string SceneBuilder uses for this cell's scene
            # component (e.g. "pin_3_layer0"). That's what lets
            # import_tallies()'s result be matched back to a specific
            # component in the 3D viewer. Falls back to the old
            # id-based label for any cell expander didn't name (shouldn't
            # happen for fuel pin / box cells, but cheap insurance).
            tally_el.set("name", cell.name or f"cell_{_int_id(cell.id)}")

            # CellFilter — registered as a top-level <filter> element and
            # referenced here by id (see _FilterRegistry docstring for why
            # the old inline <filters><filter type=... bins=.../></filters>
            # shape was silently ignored by OpenMC's parser).
            filter_id = filters.add("cell", _int_id(cell.id))
            filters_el = ET.SubElement(tally_el, "filters")
            filters_el.text = str(filter_id)

            # Scores
            scores_el = ET.SubElement(tally_el, "scores")
            scores_el.text = " ".join(s.value for s in cfg.scores)

            tally_id += 1

    def _append_mesh_tally(
        self,
        root: ET.Element,
        cfg: MeshTallyConfig,
        geometry: CascadeGeometry,
        filters: _FilterRegistry,
    ) -> None:
        """Append mesh definition + mesh tally (ID 200) to *root*."""
        # --- Mesh definition ------------------------------------------------
        mesh_el = ET.SubElement(root, "mesh")
        mesh_el.set("id", "1")

        if cfg.mesh_type == MeshType.REGULAR:
            mesh_el.set("type", "regular")
            dimension_el = ET.SubElement(mesh_el, "dimension")
            dimension_el.text = f"{cfg.nx} {cfg.ny} {cfg.nz}"

            # Derive bounds from geometry surfaces
            bounds = _geometry_bounds(geometry)
            lower_el = ET.SubElement(mesh_el, "lower_left")
            lower_el.text = f"{bounds[0]} {bounds[1]} {bounds[2]}"
            upper_el = ET.SubElement(mesh_el, "upper_right")
            upper_el.text = f"{bounds[3]} {bounds[4]} {bounds[5]}"

        else:  # CYLINDRICAL
            mesh_el.set("type", "cylindrical")
            r_grid_el = ET.SubElement(mesh_el, "r_grid")
            r_grid_el.text = _linspace_str(0.0, _geometry_r_max(geometry), cfg.nr + 1)
            z_grid_el = ET.SubElement(mesh_el, "z_grid")
            z_bounds = _geometry_z_bounds(geometry)
            z_grid_el.text = _linspace_str(z_bounds[0], z_bounds[1], cfg.nz_cyl + 1)

        # --- Tally referencing the mesh -------------------------------------
        tally_el = ET.SubElement(root, "tally")
        tally_el.set("id", "200")
        tally_el.set("name", "mesh_tally")

        # MeshFilter — top-level <filter type="mesh"> referencing mesh id "1",
        # not the mesh element itself. Registered via the shared registry so
        # its id doesn't collide with cell/material/energy filter ids from
        # the other tally groups in this document.
        filter_id = filters.add("mesh", "1")
        filters_el = ET.SubElement(tally_el, "filters")
        filters_el.text = str(filter_id)

        scores_el = ET.SubElement(tally_el, "scores")
        scores_el.text = " ".join(s.value for s in cfg.scores)

    def _append_spectra_tallies(
        self,
        root: ET.Element,
        cfg: EnergySpectraConfig,
        materials: list[Material],
        material_id_map: dict[str, int],
        filters: _FilterRegistry,
    ) -> None:
        """Append energy spectrum tally/tallies (IDs 301+) to *root*."""
        boundaries = cfg.boundaries()
        if not boundaries:
            return  # ULTRA_252 not yet populated — skip silently

        bounds_str = " ".join(str(b) for b in boundaries)

        if cfg.per_material:
            tally_id = 301
            for mat in materials:
                int_id = material_id_map.get(mat.id)
                if int_id is None:
                    continue

                tally_el = ET.SubElement(root, "tally")
                tally_el.set("id", str(tally_id))
                tally_el.set("name", f"spectrum_{mat.id}")

                # MaterialFilter + EnergyFilter — two top-level filters,
                # referenced together as a space-separated id list.
                mat_filter_id = filters.add("material", str(int_id))
                energy_filter_id = filters.add("energy", bounds_str)
                filters_el = ET.SubElement(tally_el, "filters")
                filters_el.text = f"{mat_filter_id} {energy_filter_id}"

                scores_el = ET.SubElement(tally_el, "scores")
                scores_el.text = TallyScore.FLUX.value

                tally_id += 1
        else:
            # Single global spectrum — no material filter
            tally_el = ET.SubElement(root, "tally")
            tally_el.set("id", "301")
            tally_el.set("name", "spectrum_global")

            energy_filter_id = filters.add("energy", bounds_str)
            filters_el = ET.SubElement(tally_el, "filters")
            filters_el.text = str(energy_filter_id)

            scores_el = ET.SubElement(tally_el, "scores")
            scores_el.text = TallyScore.FLUX.value

    def export_geometry(
        self,
        geometry: CascadeGeometry,
        material_id_map: dict[str, int] | None = None,
    ) -> str:
        """Serialize geometry to OpenMC geometry.xml format.

        Args:
            geometry: CascadeGeometry with surfaces and cells.
            material_id_map: Maps string material IDs to integers.
                             If None, void is used for all cells (useful
                             for geometry-only validation runs).

        Returns:
            geometry.xml content as a string.
        """
        if material_id_map is None:
            material_id_map = {}

        root = ET.Element("geometry")

        # Surfaces first — OpenMC requires surfaces declared before cells
        for surface in geometry.surfaces:
            root.append(_surface_element(surface))

        # Then cells
        for cell in geometry.cells:
            root.append(_cell_element(cell, material_id_map))

        return _pretty_xml(root)

    def export_materials(
        self,
        materials: list[Material],
        material_id_map: dict[str, int] | None = None,
        depletable_ids: set[str] | None = None,
    ) -> str:
        """Serialize materials to OpenMC materials.xml format.

        Material composition is expressed as atom fractions (sum = 1.0)
        or mass fractions (sum = -1.0 in OpenMC convention for mass).
        We use atom fractions — the composition dict on Material is
        expected to contain {nuclide: atom_fraction} pairs.

        Nuclide names must match OpenMC's library naming convention:
            "U235", "U238", "O16", "Zr90", "H1", etc.

        Args:
            materials: List of Material domain objects.
            material_id_map: Maps material.id strings to integer IDs.
                             Generated from enumerate(materials) if None.
            depletable_ids: material.id values to mark `depletable="true"`.
                             BUG FIX: this attribute was never emitted at
                             all before, for any run mode — meaning every
                             depletion job's materials.xml declared zero
                             depletable materials, and
                             openmc.deplete.CoupledOperator would
                             unconditionally raise "No depletable materials
                             were found in the model" regardless of chain
                             file content. See write_depletion_driver(),
                             which computes this set from which materials
                             are fissile (the same heuristic already used
                             for eigenvalue-mode source placement — see
                             _is_fissile()/_compute_fissile_source_box()).
                             None/empty for eigenvalue/fixed_source/r2s,
                             which never invoke openmc.deplete and don't
                             need this attribute at all.

        Returns:
            materials.xml content as a string.
        """
        if material_id_map is None:
            material_id_map = {mat.id: i + 1 for i, mat in enumerate(materials)}
        depletable_ids = depletable_ids or set()

        root = ET.Element("materials")

        for mat in materials:
            mat_el = ET.Element("material")
            mat_el.set("id", str(material_id_map[mat.id]))
            mat_el.set("name", mat.name)

            if mat.id in depletable_ids:
                mat_el.set("depletable", "true")

            if mat.density is not None:
                density_el = ET.SubElement(mat_el, "density")
                density_el.set("value", str(mat.density))
                density_el.set("units", "g/cm3")

            for nuclide, fraction in mat.composition.items():
                nuclide_el = ET.SubElement(mat_el, "nuclide")
                nuclide_el.set("name", nuclide)
                nuclide_el.set("ao", str(fraction))

            root.append(mat_el)

        return _pretty_xml(root)

    def export_settings(self,
                        settings: OpenMCRunSettings,
                        geometry: CascadeGeometry | None = None,
                        materials: list[Material] | None = None,
                        results_config: ResultsConfig | None = None,
                        ) -> str:
        """Serialize run settings to OpenMC settings.xml format.

        Args:
            settings:       OpenMCRunSettings dataclass.
            geometry:       Used for automatic source-box detection.
            materials:      Used for fissile-cell detection.
            results_config: When diagnostics.stochastic_volumes is True, emits
                            a <volume_calc> block required by OpenMC.

        Returns:
            settings.xml content as a string.
        """

        root = ET.Element("settings")

        run_mode_el = ET.SubElement(root, "run_mode")
        run_mode_el.text = settings.run_mode

        particles_el = ET.SubElement(root, "particles")
        particles_el.text = str(settings.particles)

        batches_el = ET.SubElement(root, "batches")
        batches_el.text = str(settings.batches)

        # inactive is None for a fixed-source leg (job-settings-model.md §2)
        # — OpenMCRunSettings.__post_init__ already guarantees this is
        # consistent with run_mode, so no extra branching is needed here.
        if settings.inactive is not None:
            inactive_el = ET.SubElement(root, "inactive")
            inactive_el.text = str(settings.inactive)

        seed_el = ET.SubElement(root, "seed")
        seed_el.text = str(settings.seed)

        # Source definition.
        if settings.run_mode == "eigenvalue":
            # Criticality source: auto-detected fissile-cell bounding box
            # (or an explicit source_box override), never user-entered.
            source_box = settings.source_box
            if source_box is None and geometry is not None and materials is not None:
                source_box = _compute_fissile_source_box(geometry, materials)
            if source_box is None:
                raise ValueError(
                    "Cannot determine source distribution. Either provide "
                    "source_box in OpenMCRunSettings, or pass geometry and "
                    "materials to export_settings() for automatic detection."
                )
            source_el = ET.SubElement(root, "source")
            space_el = ET.SubElement(source_el, "space")
            space_el.set("type", "box")
            params_el = ET.SubElement(space_el, "parameters")
            params_el.text = " ".join(str(v) for v in source_box)

        else:  # fixed source — real, user-specified source
            # __post_init__ already guarantees settings.source is not None
            # whenever run_mode == "fixed source".
            root.append(_source_element(settings.source))

        # Diagnostics — particle tracks
        if results_config is not None and results_config.diagnostics.particle_tracks:
            tracks_el = ET.SubElement(root, "track")
            tracks_el.text = str(results_config.diagnostics.n_tracks)

        # Diagnostics — stochastic volume calculation
        # OpenMC requires a <volume_calc> block in settings.xml; it cannot
        # appear in tallies.xml. Only emitted when explicitly requested.
        if results_config is not None and results_config.diagnostics.stochastic_volumes:
            bounds = _geometry_bounds(geometry) if geometry else (-10, -10, -10, 10, 10, 10)
            vol_el = ET.SubElement(root, "volume_calc")
            domain_type_el = ET.SubElement(vol_el, "domain_type")
            domain_type_el.text = "cell"
            # Tally all cells that carry material
            cell_ids = [
                _int_id(c.id) for c in (geometry.cells if geometry else [])
                if c.material_id is not None
            ]
            if cell_ids:
                domain_ids_el = ET.SubElement(vol_el, "domain_ids")
                domain_ids_el.text = " ".join(cell_ids)
            lower_el = ET.SubElement(vol_el, "lower_left")
            lower_el.text = f"{bounds[0]} {bounds[1]} {bounds[2]}"
            upper_el = ET.SubElement(vol_el, "upper_right")
            upper_el.text = f"{bounds[3]} {bounds[4]} {bounds[5]}"
            samples_el = ET.SubElement(vol_el, "samples")
            samples_el.text = "100000"

        return _pretty_xml(root)

    def write_input_files(
        self,
        geometry: CascadeGeometry,
        materials: list[Material],
        output_dir: Path,
        settings: OpenMCRunSettings | None = None,
        results_config: ResultsConfig | None = None,
    ) -> list[Path]:
        """Export and write all input files to a directory.

        Convenience method used by the LocalBackend and SlurmBackend
        to stage input files before submission.

        Args:
            geometry: Resolved geometry.
            materials: Material library entries referenced by this geometry.
            output_dir: Directory to write files into. Created if absent.
            settings: Run parameters.
            results_config: Tally capture config. Produces tallies.xml when
                            any tally group is enabled.

        Returns:
            List of Path objects for the written files.
        """
        output_dir.mkdir(parents=True, exist_ok=True)
        files = self.export(geometry, materials, settings, results_config)

        written: list[Path] = []
        for filename, content in files.items():
            path = output_dir / filename
            path.write_text(content, encoding="utf-8")
            written.append(path)

        return written

    # ------------------------------------------------------------------
    # Depletion — a Python driver script, not XML+CLI
    #
    # See module docstring's OpenMCRunSettings section and
    # PLAN_depletion_r2s_execution.md Task 3: openmc.deplete's integrator
    # (Predictor, CE/CM, etc.) owns its own timestep loop internally —
    # it recomputes materials from the previous step's reaction rates
    # itself, inside one Python process. Cascade must NOT reimplement that
    # loop by repeatedly calling write_input_files()/export() and shelling
    # out to the `openmc` CLI per timestep; that would duplicate what
    # OpenMC already does, incorrectly (each step's materials.xml would
    # need to be hand-rebuilt from the previous step's statepoint, which
    # is exactly the coupled-operator bookkeeping openmc.deplete exists
    # to handle correctly).
    #
    # So depletion is still exactly ONE execution unit (one JobStep,
    # kind=DEPLETION_DRIVER — see domain/job_step.py) — just one that runs
    # `python run_depletion.py` inside the container instead of `openmc`.
    # ------------------------------------------------------------------

    def write_depletion_driver(
        self,
        geometry: CascadeGeometry,
        materials: list[Material],
        output_dir: Path,
        mc: McSettings,
        depletion: DepletionSettings,
        chain_file_container_path: str,
        results_config: ResultsConfig | None = None,
    ) -> list[Path]:
        """Write geometry/materials/settings XML + a depletion driver script.

        Each depletion timestep's transport solve is a k-eigenvalue
        calculation (to get reaction rates for burnup) — so settings.xml
        is built exactly like a normal eigenvalue run, using `mc`
        (McSettings.inactive is required for depletion — see
        domain/run_settings.py's McSettings.validate()). The driver script
        then wraps that same geometry/materials in openmc.deplete and owns
        the timestep loop itself; it does NOT re-read settings.xml's
        particles/batches/inactive by parsing XML — those are re-emitted
        directly into the script from `mc`, since openmc.deplete's
        CoupledOperator takes an openmc.Model object, not file paths, and
        constructing that Model from Python is simpler and more robust
        than having the generated script parse its own sibling XML files.

        Args:
            geometry, materials: Same as export().
            output_dir: Directory to write files into. Created if absent.
            mc: Particles/batches/seed/inactive for EACH timestep's
                transport solve (job-settings-model.md §3.3: these apply
                per timestep, not once — the integrator calls the
                transport solver `len(depletion.timesteps)` times).
            depletion: power_W/timesteps/chain_file/integrator/substeps.
            chain_file_container_path: Absolute path, INSIDE the
                container, to the depletion chain XML file referenced by
                `depletion.chain_file`. Resolving the user-facing
                `chain_file` reference (currently just a filename picked
                in the UI — see JobSubmitModal.svelte) to an actual
                container-mounted path is execution/docker_backend.py's
                job, not this adapter's — see that file's
                `_resolve_chain_file_path()` for the current (flagged as
                provisional) resolution strategy.
            results_config: Optional per-step tally capture. Same
                scalar/mesh tallies as any other transport leg — useful
                for inspecting flux/heating at each depletion step, in
                addition to the nuclide inventory openml.deplete tracks
                on its own.

        Returns:
            List of written file paths (geometry.xml, materials.xml,
            settings.xml, tallies.xml if applicable, run_depletion.py).
        """
        output_dir.mkdir(parents=True, exist_ok=True)

        # BUG FIX: materials.xml previously never marked ANY material as
        # depletable="true", for any run mode — so openmc.deplete.
        # CoupledOperator would always raise "No depletable materials were
        # found in the model", regardless of chain file content. Reuses
        # the same fissile-detection heuristic already used for
        # eigenvalue-mode source placement (_is_fissile() /
        # _compute_fissile_source_box()) — fuel is depletable, structural/
        # moderator materials are not (they're not in the fissile check,
        # and depleting them would need chain data for their nuclides,
        # which most chain files don't provide anyway).
        depletable_ids = {mat.id for mat in materials if _is_fissile(mat)}
        if not depletable_ids:
            # Defensive backstop — SimulationJob.__post_init__ already
            # requires a fissile material for depletion mode (same check
            # as eigenvalue), so this should be unreachable via normal
            # submission. Kept here in case this method is ever called
            # directly, bypassing job construction.
            raise ValueError(
                "depletion requires at least one fissile material in "
                "geometry to mark as depletable in materials.xml — none found."
            )

        # settings.xml: eigenvalue-shaped, one transport leg's worth of
        # particles/batches/inactive — reused identically at each timestep
        # by the driver script below (it does NOT read this file; it's
        # written for operator/debugging visibility and because
        # export_geometry/export_materials are bundled with it in export()).
        eigen_settings = OpenMCRunSettings.for_leg(mc, RunMode.EIGENVALUE)
        files = self.export(geometry, materials, eigen_settings, results_config, depletable_ids)

        written: list[Path] = []
        for filename, content in files.items():
            path = output_dir / filename
            path.write_text(content, encoding="utf-8")
            written.append(path)

        driver_script = _render_depletion_driver_script(
            mc=mc,
            depletion=depletion,
            chain_file_container_path=chain_file_container_path,
        )
        driver_path = output_dir / "run_depletion.py"
        driver_path.write_text(driver_script, encoding="utf-8")
        written.append(driver_path)

        return written

    # ------------------------------------------------------------------
    # Legacy stubs
    # ------------------------------------------------------------------

    def export_geometry_stub(self, geometry: CascadeGeometry) -> dict[str, object]:
        """Legacy stub — returns geometry as dict. Use export() instead."""
        return {"simulator": self.name, "geometry": geometry.to_dict()}

    # ------------------------------------------------------------------
    # Result import — statepoint HDF5 parsing
    # ------------------------------------------------------------------
    #
    # This is the read-side counterpart of export_tallies()/export_settings():
    # it owns the same OpenMC format conventions (tally id ranges, mesh
    # layout, sum/sum_sq encoding) that the write side produced, so the two
    # stay in sync in one place instead of drifting between a writer here
    # and a hand-rolled parser in the API layer.
    #
    # These methods are framework-agnostic (no FastAPI/HTTP concerns) and
    # raise plain Python exceptions; the API router is responsible for
    # translating those into HTTP responses.
    #
    # HDF5 layout reference:
    #     https://docs.openmc.org/en/stable/io_formats/statepoint.html
    #
    # Key HDF5 facts that bit us during development:
    #   - global_tallies shape is (4, 3): columns are [sum, sum_sq, ???].
    #     Mean = sum / n_realizations. The four rows are:
    #     [0] leakage, [1] absorption, [2] fission, [3] nu-fission.
    #   - k_combined = [mean, std_dev] (already computed by OpenMC).
    #   - k_col_abs, k_abs_tra, k_col_tra are pairwise combined estimators,
    #     each [mean, std_dev]. There is no separate k_col/k_abs/k_tracklength.
    #   - Individual batch k-eff values live in k_generation (length = n_batches).
    #   - Shannon entropy lives in source_shannon_entropy (not "entropy").
    #   - results datasets shape: (n_filter_bins, n_score_bins, 2) where
    #     dim-2 is [sum, sum_sq]. NOT [mean, rel_err].
    #     mean    = sum / n_realizations
    #     std_dev = sqrt(max(0, sum_sq/n - mean^2) / (n - 1))

    def find_statepoint(self, search_dirs: list[Path]) -> Path:
        """Locate the final statepoint HDF5 file across candidate directories.

        OpenMC writes statepoint.<batch>.h5 to the directory it runs in.
        Callers resolve WHICH directories that might be (job-level vs. a
        job step's directory — see docker_backend.py / results.py); this
        method only knows the OpenMC file-naming convention itself, and
        returns the file with the highest batch number (the final result).

        Raises:
            FileNotFoundError: If no statepoint file exists in any of the
                given directories.
        """
        candidates: list[Path] = []
        for d in search_dirs:
            if d.exists():
                candidates.extend(d.glob("statepoint.*.h5"))

        if not candidates:
            raise FileNotFoundError(
                "No statepoint file found in: "
                + ", ".join(str(d) for d in search_dirs)
            )

        def _batch_num(p: Path) -> int:
            m = re.search(r"statepoint\.(\d+)\.h5", p.name)
            return int(m.group(1)) if m else 0

        return max(candidates, key=_batch_num)

    def open_statepoint(self, path: Path) -> h5py.File:
        """Open a statepoint file, raising a clear error if it's unreadable."""
        try:
            return h5py.File(path, "r")
        except Exception as exc:
            raise OSError(f"Could not open statepoint '{path}': {exc}") from exc

    @staticmethod
    def _kstat(arr: np.ndarray) -> dict[str, float]:
        """Unpack a 2-element [mean, std_dev] k-eff estimator array."""
        return {"mean": float(arr[0]), "std_dev": float(arr[1])}

    @staticmethod
    def _tally_mean_stddev(total: float, sum_sq: float, n: int) -> tuple[float, float]:
        """Convert OpenMC's raw sum and sum-of-squares to mean and std dev.

        OpenMC stores per-batch running sums, not means. Conversion:
            mean    = total / n
            std_dev = sqrt(max(0, sum_sq/n - mean^2) / (n - 1))

        The max(0, ...) guard prevents tiny negative values due to floating
        point cancellation when variance is near zero.
        """
        if n <= 0:
            return 0.0, 0.0
        mean = total / n
        variance_estimate = (sum_sq / n - mean ** 2) / max(1, n - 1)
        std_dev = math.sqrt(max(0.0, variance_estimate))
        return mean, std_dev

    def import_summary(self, job_id: str, sp: h5py.File) -> dict[str, Any]:
        """Parse k-eff, neutron balance, timing, and convergence history.

        Response schema::

            {
              "job_id": "...",
              "batches": 100,
              "inactive": 20,
              "particles_per_batch": 1000,
              "n_realizations": 80,
              "k_effective": {
                "combined":   {"mean": 1.3437, "std_dev": 0.0034},
                "col_abs":    {"mean": 1.3438, "std_dev": 0.0035},
                "abs_tra":    {"mean": 1.4413, "std_dev": 0.0001},
                "col_tra":    {"mean": 1.4352, "std_dev": 0.0001}
              },
              "entropy_history":   [0.91, 0.93, ...],
              "keff_history":      [1.40, 1.37, ...],
              "neutron_balance":   {
                "leakage":     0.0,
                "absorption":  107.25,
                "fission":     143.99,
                "nu_fission":  0.0
              },
              "timing":            {}
            }
        """
        batches        = int(sp["n_batches"][()]) if "n_batches" in sp else 0
        inactive       = int(sp["n_inactive"][()]) if "n_inactive" in sp else 0
        pps            = int(sp["n_particles"][()]) if "n_particles" in sp else 0
        n_realizations = (
            int(sp["n_realizations"][()])
            if "n_realizations" in sp
            else max(1, batches - inactive)
        )

        k_effective: dict[str, dict] = {}
        for key, label in [
            ("k_combined", "combined"),
            ("k_col_abs",  "col_abs"),
            ("k_abs_tra",  "abs_tra"),
            ("k_col_tra",  "col_tra"),
        ]:
            if key in sp:
                arr = sp[key][()]
                if arr.ndim == 1 and len(arr) >= 2:
                    k_effective[label] = self._kstat(arr)

        keff_history: list[float] = []
        entropy_history: list[float] = []

        if "k_generation" in sp:
            keff_history = sp["k_generation"][()].tolist()

        # OpenMC uses "source_shannon_entropy" in recent versions; fall back
        # to "entropy" for older builds.
        for entropy_key in ("source_shannon_entropy", "entropy"):
            if entropy_key in sp:
                entropy_history = sp[entropy_key][()].tolist()
                break

        neutron_balance: dict[str, float] = {}
        if "global_tallies" in sp:
            gt = sp["global_tallies"][()]
            labels = ["leakage", "absorption", "fission", "nu_fission"]
            for i, label in enumerate(labels):
                if i < gt.shape[0]:
                    raw_sum = float(gt[i, 0])
                    neutron_balance[label] = (
                        raw_sum / n_realizations if n_realizations > 0 else 0.0
                    )

        timing: dict[str, float] = {}
        if "runtime" in sp:
            rt = sp["runtime"]
            for key in rt.keys():
                try:
                    timing[key] = float(rt[key][()])
                except Exception:
                    pass

        return {
            "job_id":              job_id,
            "batches":             batches,
            "inactive":            inactive,
            "particles_per_batch": pps,
            "n_realizations":      n_realizations,
            "k_effective":         k_effective,
            "entropy_history":     entropy_history,
            "keff_history":        keff_history,
            "neutron_balance":     neutron_balance,
            "timing":              timing,
        }

    def import_tallies(
        self,
        job_id: str,
        sp: h5py.File,
        geometry: CascadeGeometry,
        materials: list[Material],
        scalars_cfg: ScalarTallyConfig,
    ) -> dict[str, Any]:
        """Parse scalar cell tallies — mean + std dev per cell per score.

        `name` is NOT read from the statepoint — OpenMC's statepoint.h5
        does not persist a tally's XML `name=` attribute onto its HDF5
        group (that metadata lives only in the run's XML/summary, not the
        statepoint), so `t.attrs.get("name", ...)` silently always fell
        through to the group key ("tally 101") no matter what
        _append_scalar_tallies wrote into the XML. export_tallies's own
        docstring already documents the actual intended mechanism: tally
        IDs are assigned in a FIXED ORDER over geometry.cells (skip voids,
        skip non-fissile cells when not all_cells) — so `name` is
        reconstructed here by replicating that exact same selection loop
        and zipping it against sorted tally IDs (101 -> 1st selected cell,
        102 -> 2nd, ...), rather than trusting anything the statepoint may
        or may not carry.

        `geometry`/`materials`/`scalars_cfg` MUST be the same job's
        geometry/materials/results_config.scalars that export_tallies was
        originally called with — this only works because the ordering is
        deterministic given the same inputs.

        Response schema::

            {
              "job_id": "...",
              "tallies": [
                {
                  "tally_id": 101,
                  "name": "pin_3_layer0",
                  "scores": {
                    "flux":    {"mean": 3.14e12, "std_dev": 7.2e10, "rel_err": 0.023},
                    "fission": {"mean": 1.02e11, "std_dev": 3.2e9,  "rel_err": 0.031}
                  }
                }
              ]
            }

        `name` matches the corresponding SceneComponentOut's layer/box
        `cell_name` (see scene_builder_service.py, expander.py's
        _build_fuel_pin_cells/_build_fill_cell) — that's the join key for
        mapping a tally result onto a specific object in the 3D viewer.
        """
        tallies_grp = sp.get("tallies")
        if tallies_grp is None:
            return {"job_id": job_id, "tallies": []}

        n_realizations = int(sp["n_realizations"][()]) if "n_realizations" in sp else 1

        # Replicate _append_scalar_tallies's exact cell selection/order so
        # tally_id 101 lines up with ordered_names[0], 102 with [1], etc.
        fissile_ids: set[str] = set()
        if not scalars_cfg.all_cells:
            fissile_ids = {m.id for m in materials if _is_fissile(m)}

        ordered_names: list[str] = []
        for cell in geometry.cells:
            if cell.material_id is None:
                continue
            if not scalars_cfg.all_cells and cell.material_id not in fissile_ids:
                continue
            ordered_names.append(cell.name or f"cell_{_int_id(cell.id)}")

        result: list[dict] = []
        for tally_key in tallies_grp.keys():
            t = tallies_grp[tally_key]
            if not isinstance(t, h5py.Group):
                continue
            if not tally_key.startswith("tally"):
                continue
            # OpenMC statepoints don't store an "id" attr on tally groups;
            # the id is embedded in the group name, e.g. "tally 101"
            try:
                tally_id = int(tally_key.split()[1])
            except (IndexError, ValueError):
                continue

            # Only scalar tallies (101–199); mesh and spectra have dedicated methods.
            if not (100 < tally_id < 200):
                continue

            idx = tally_id - 101
            name = ordered_names[idx] if 0 <= idx < len(ordered_names) else tally_key

            scores_list: list[str] = []
            if "score_bins" in t:
                scores_list = [
                    s.decode() if isinstance(s, bytes) else str(s)
                    for s in t["score_bins"][()]
                ]

            results_data = t.get("results")
            if results_data is None:
                continue
            # arr shape: (n_filter_bins, n_score_bins, 2) — [sum, sum_sq]
            arr = results_data[()]

            scores_out: dict[str, dict] = {}
            for si, score in enumerate(scores_list):
                if si >= arr.shape[1]:
                    break
                total  = float(arr[0, si, 0])
                sum_sq = float(arr[0, si, 1]) if arr.shape[2] > 1 else 0.0
                mean, std_dev = self._tally_mean_stddev(total, sum_sq, n_realizations)
                rel_err = (std_dev / mean) if mean != 0.0 else 0.0
                scores_out[score] = {
                    "mean":    mean,
                    "std_dev": std_dev,
                    "rel_err": rel_err,
                }

            result.append({
                "tally_id": tally_id,
                "name":     name,
                "scores":   scores_out,
            })

        result.sort(key=lambda x: x["tally_id"])
        return {"job_id": job_id, "tallies": result}

    def import_mesh(self, job_id: str, sp: h5py.File, mesh_type: MeshType) -> dict[str, Any]:
        """Parse the 3-D mesh tally (tally id 200) as a structured response.

        Response schema::

            {
              "job_id": "...",
              "tally_id": 200,
              "mesh": {
                "type": "regular",
                "shape": [nx, ny, nz],
                "lower_left":  [x0, y0, z0],
                "upper_right": [x1, y1, z1]
              },
              "scores": ["flux", "fission", "heating-local"],
              "data": [
                {"ix": 0, "iy": 0, "iz": 0,
                 "flux_mean": 1.2e13, "flux_std_dev": 3.1e11, ...},
                ...
              ]
            }
        """
        tallies_grp = sp.get("tallies")
        if tallies_grp is None:
            raise ValueError("No tallies in statepoint.")

        n_realizations = int(sp["n_realizations"][()]) if "n_realizations" in sp else 1

        mesh_tally = None
        for key in tallies_grp.keys():
            t = tallies_grp[key]
            if not isinstance(t, h5py.Group) or not key.startswith("tally"):
                continue
            try:
                tally_id = int(key.split()[1])
            except (IndexError, ValueError):
                continue
            if tally_id == 200:
                mesh_tally = t
                break

        if mesh_tally is None:
            raise ValueError("Mesh tally (id=200) not found.")

        # Mesh metadata lives under /tallies/meshes/<mesh id>.
        mesh_meta: dict[str, Any] = {}
        meshes_grp = tallies_grp.get("meshes")
        if meshes_grp is not None:
            for mesh_key in meshes_grp.keys():
                m = meshes_grp[mesh_key]
                if not isinstance(m, h5py.Group):
                    continue
                shape       = m["dimension"][()] if "dimension" in m else np.array([1, 1, 1])
                lower_left  = m["lower_left"][()].tolist() if "lower_left" in m else [0, 0, 0]
                upper_right = m["upper_right"][()].tolist() if "upper_right" in m else [1, 1, 1]
                mesh_meta   = {
                    "type":        mesh_type.value,
                    "shape":       shape.tolist() if hasattr(shape, "tolist") else list(shape),
                    "lower_left":  lower_left,
                    "upper_right": upper_right,
                }
                break  # use the first mesh found

        scores_list: list[str] = []
        if "score_bins" in mesh_tally:
            scores_list = [
                s.decode() if isinstance(s, bytes) else str(s)
                for s in mesh_tally["score_bins"][()]
            ]

        results_data = mesh_tally.get("results")
        if results_data is None:
            return {"job_id": job_id, "tally_id": 200, "mesh": mesh_meta,
                    "scores": scores_list, "data": []}

        # arr shape: (nx*ny*nz, n_scores, 2) — [sum, sum_sq]
        arr = results_data[()]
        shape = mesh_meta.get("shape", [1, 1, 1])
        nx, ny, nz = (list(shape) + [1, 1, 1])[:3]

        data: list[dict] = []
        idx = 0
        for ix in range(nx):
            for iy in range(ny):
                for iz in range(nz):
                    if idx >= arr.shape[0]:
                        break
                    row: dict[str, Any] = {"ix": ix, "iy": iy, "iz": iz}
                    for si, score in enumerate(scores_list):
                        if si >= arr.shape[1]:
                            break
                        total  = float(arr[idx, si, 0])
                        sum_sq = float(arr[idx, si, 1]) if arr.shape[2] > 1 else 0.0
                        mean, std_dev = self._tally_mean_stddev(total, sum_sq, n_realizations)
                        row[f"{score}_mean"]    = mean
                        row[f"{score}_std_dev"] = std_dev
                    data.append(row)
                    idx += 1

        return {
            "job_id":   job_id,
            "tally_id": 200,
            "mesh":     mesh_meta,
            "scores":   scores_list,
            "data":     data,
        }

    def import_spectra(self, job_id: str, sp: h5py.File, results_config: ResultsConfig) -> dict[str, Any]:
        """Parse energy flux spectra per material (tally ids 301+).

        Response schema::

            {
              "job_id": "...",
              "group_structure": "69",
              "spectra": [
                {
                  "tally_id": 301,
                  "name": "spectrum_H2O",
                  "group_boundaries_ev": [1e-5, ...],
                  "flux_mean":    [3.1e12, ...],
                  "flux_std_dev": [7.2e10, ...]
                }
              ]
            }
        """
        tallies_grp = sp.get("tallies")
        if tallies_grp is None:
            return {"job_id": job_id, "spectra": []}

        n_realizations = int(sp["n_realizations"][()]) if "n_realizations" in sp else 1
        group_structure = results_config.spectra.group_structure.value
        boundaries      = results_config.spectra.boundaries()
        spectra_out: list[dict] = []

        for key in tallies_grp.keys():
            t = tallies_grp[key]
            if not isinstance(t, h5py.Group):
                continue
            if not key.startswith("tally"):
                continue
            try:
                tally_id = int(key.split()[1])
            except (IndexError, ValueError):
                continue

            name = t.attrs.get("name", key)
            if isinstance(name, bytes):
                name = name.decode()

            results_data = t.get("results")
            if results_data is None:
                continue

            # arr shape: (n_material_bins * n_energy_bins, n_scores, 2)
            # Each tally has exactly one material bin, so shape[0] == n_energy_bins.
            # OpenMC stores one value per *group* (interval between boundaries),
            # so n_energy_bins == len(boundaries) - 1.
            arr = results_data[()]
            n_energy_bins = arr.shape[0]

            flux_mean:    list[float] = []
            flux_std_dev: list[float] = []
            for i in range(n_energy_bins):
                total  = float(arr[i, 0, 0])
                sum_sq = float(arr[i, 0, 1]) if arr.shape[2] > 1 else 0.0
                mean, std_dev = self._tally_mean_stddev(total, sum_sq, n_realizations)
                flux_mean.append(mean)
                flux_std_dev.append(std_dev)

            # group_boundaries_ev has len = n_energy_bins + 1 (fence-post values).
            # flux_mean/flux_std_dev have len = n_energy_bins (one per group).
            spectra_out.append({
                "tally_id":            tally_id,
                "name":                name,
                "group_boundaries_ev": boundaries,
                "group_midpoints_ev":  [
                    math.sqrt(boundaries[i] * boundaries[i + 1])
                    for i in range(len(boundaries) - 1)
                ],
                "flux_mean":           flux_mean,
                "flux_std_dev":        flux_std_dev,
            })

        spectra_out.sort(key=lambda x: x["tally_id"])
        return {
            "job_id":          job_id,
            "group_structure": group_structure,
            "spectra":         spectra_out,
        }

    # ------------------------------------------------------------------
    # AdapterProtocol conformance
    # ------------------------------------------------------------------

    def import_results(
        self,
        job_id: str,
        output_dir: Path,
        geometry: CascadeGeometry,
        materials: list[Material],
        results_config: ResultsConfig | None = None,
    ) -> dict[str, Any]:
        """AdapterProtocol entry point — locate, open, and parse a
        completed run's statepoint into every result section
        `results_config` asks for.

        This is a convenience aggregator over import_summary/import_tallies/
        import_mesh/import_spectra for callers that only have a job_id and
        an output directory (e.g. a generic ExecutionBackend.fetch_results()
        path) and don't want to know OpenMC's statepoint/tally-id layout.
        results.py's panel-by-panel endpoints can keep calling the
        individual import_* methods directly against an already-open
        statepoint when they only need one section — this method composes
        those, it doesn't replace them.

        Args:
            job_id: Job identifier, threaded through into every section.
            output_dir: Directory to search for the run's statepoint file
                        (job.output_dir() for single-leg runs).
            geometry: The job's resolved geometry — import_tallies needs
                      this to reconstruct tally names (see its docstring).
            materials: The job's materials — same reason.
            results_config: What was captured. Defaults to
                            ResultsConfig.default() (summary + scalars).

        Returns:
            {
              "job_id": "...",
              "summary": {...},             # always present
              "tallies": {...},             # if results_config.scalars.enabled
              "mesh": {...},                # if results_config.mesh.enabled
              "spectra": {...},             # if results_config.spectra.enabled
            }

        Raises:
            FileNotFoundError: If no statepoint file exists in output_dir.
            OSError: If the statepoint file exists but can't be opened.
        """
        if results_config is None:
            results_config = ResultsConfig.default()

        statepoint_path = self.find_statepoint([output_dir])
        sp = self.open_statepoint(statepoint_path)
        try:
            out: dict[str, Any] = {
                "job_id": job_id,
                "summary": self.import_summary(job_id, sp),
            }
            if results_config.scalars.enabled:
                out["tallies"] = self.import_tallies(
                    job_id, sp, geometry, materials, results_config.scalars
                )
            if results_config.mesh.enabled:
                out["mesh"] = self.import_mesh(
                    job_id, sp, results_config.mesh.mesh_type
                )
            if results_config.spectra.enabled:
                out["spectra"] = self.import_spectra(job_id, sp, results_config)
            return out
        finally:
            sp.close()