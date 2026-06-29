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

import re
import textwrap
from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree as ET
from xml.dom import minidom

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
from ..domain.result import TallyResult
from ..domain.results_config import (
    DiagnosticsConfig,
    EnergySpectraConfig,
    MeshTallyConfig,
    MeshType,
    ResultsConfig,
    ScalarTallyConfig,
    TallyScore,
)


# ---------------------------------------------------------------------------
# Run settings dataclass — what the user configures per job
# ---------------------------------------------------------------------------

@dataclass
class OpenMCRunSettings:
    """Parameters for an OpenMC Monte Carlo run.

    These map directly to OpenMC's settings.xml fields.
    Defaults are conservative — fast enough for geometry checking,
    not production quality. Users should increase for real results.

    Attributes:
        particles:      Neutrons per batch.
        inactive:       Inactive (warmup) batches — discarded from tallies.
        batches:        Total batches (inactive + active).
        seed:           Random number seed. Fixed default for reproducibility.
        run_mode:       "eigenvalue" for criticality, "fixed source" for shielding.
        energy_groups:  Number of energy groups for multi-group mode.
                        None = continuous energy (default, recommended).
    """
    particles: int = 1000
    inactive: int = 20
    batches: int = 100
    seed: int = 1
    run_mode: str = "eigenvalue"
    energy_groups: int | None = None
    source_box: tuple[float, ...] | None = None

    def __post_init__(self):
        if self.inactive >= self.batches:
            raise ValueError(
                f"inactive batches ({self.inactive}) must be less than "
                f"total batches ({self.batches})."
            )
        if self.run_mode not in ("eigenvalue", "fixed source"):
            raise ValueError(
                f"run_mode must be 'eigenvalue' or 'fixed source', "
                f"got '{self.run_mode}'."
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


# Nuclides that make a material fissile
_FISSILE_NUCLIDES = frozenset({
    "U233", "U235", "U238",  # uranium
    "Pu238", "Pu239", "Pu240", "Pu241", "Pu242",  # plutonium
    "Th232",  # thorium (relevant for your LFTR work too)
    "Am241", "Cm244",  # minor actinides
})


def _material_is_fissile(mat: Material) -> bool:
    """Return True if a material contains any fissile or fertile nuclide."""
    return any(
        any(nuc.startswith(prefix) for prefix in _FISSILE_NUCLIDES)
        for nuc in mat.composition
    )


def _compute_fissile_source_box(
        geometry: CascadeGeometry,
        materials: list[Material],
) -> tuple[float, ...]:
    """Compute a source box bounding all fissile cells.

    Finds every cell whose material is fissile, then computes the
    bounding box of those cells from their surface parameters.
    Contracts the box by 1% on each side to ensure particles are
    born strictly inside surfaces, never on them.
    """
    fissile_ids = {m.id for m in materials if _material_is_fissile(m)}
    fissile_cells = [c for c in geometry.cells if c.material_id in fissile_ids]

    if not fissile_cells:
        raise ValueError(
            "No fissile cells found in geometry. Cannot auto-compute "
            "source distribution. Specify source_box in OpenMCRunSettings."
        )

    # Collect cylinder and plane surfaces that bound fissile cells
    surface_map = {s.id: s for s in geometry.surfaces}

    r_max = 0.0
    z_min = float('inf')
    z_max = float('-inf')

    for cell in fissile_cells:
        for sid in _surface_ids_in_region(cell.region):
            surf = surface_map.get(sid)
            if surf is None:
                continue
            if surf.type_ == SurfaceType.CYLINDER_Z:
                r = float(surf.params.get("r", 0))
                r_max = max(r_max, r)
            elif surf.type_ == SurfaceType.PLANE_Z:
                z = float(surf.params.get("z", surf.params.get("z0", 0)))
                z_min = min(z_min, z)
                z_max = max(z_max, z)

    if r_max == 0 or z_min == float('inf'):
        raise ValueError(
            "Could not determine fissile cell bounds from geometry surfaces. "
            "Specify source_box in OpenMCRunSettings."
        )

    # Contract by 1% to keep particles off surfaces
    shrink = 0.99
    r = r_max * shrink
    dz = (z_max - z_min) * 0.01  # 1% inset from each z face

    return (-r, -r, z_min + dz, r, r, z_max - dz)


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
    ) -> dict[str, str]:
        """Return all OpenMC input file contents as a filename → content dict.

        Always produces geometry.xml, materials.xml, settings.xml.
        Produces tallies.xml when results_config requests any tally group.

        Args:
            geometry:       Fully expanded CascadeGeometry.
            materials:      Materials referenced by cells in geometry.
            settings:       Run parameters. Defaults are used if None.
            results_config: Results capture config. Tallies.xml is only
                            generated when this is non-None and
                            results_config.needs_tallies_xml() is True.

        Returns:
            dict mapping filename -> XML string content.
        """
        if settings is None:
            settings = OpenMCRunSettings()

        material_id_map = {mat.id: i + 1 for i, mat in enumerate(materials)}

        files = {
            "geometry.xml": self.export_geometry(geometry, material_id_map),
            "materials.xml": self.export_materials(materials, material_id_map),
            "settings.xml": self.export_settings(
                settings, geometry, materials, results_config
            ),
        }

        if results_config is not None and results_config.needs_tallies_xml():
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

        # --- Group 2: scalar cell tallies -----------------------------------
        if config.scalars.enabled:
            self._append_scalar_tallies(
                root, config.scalars, geometry, materials, material_id_map
            )

        # --- Group 3: mesh tally --------------------------------------------
        if config.mesh.enabled:
            self._append_mesh_tally(root, config.mesh, geometry)

        # --- Group 4: energy spectra ----------------------------------------
        if config.spectra.enabled:
            self._append_spectra_tallies(
                root, config.spectra, materials, material_id_map
            )

        return _pretty_xml(root)

    def _append_scalar_tallies(
        self,
        root: ET.Element,
        cfg: ScalarTallyConfig,
        geometry: CascadeGeometry,
        materials: list[Material],
        material_id_map: dict[str, int],
    ) -> None:
        """Append one tally per cell (or fissile cell) to *root*."""
        fissile_ids: set[str] = set()
        if not cfg.all_cells:
            fissile_ids = {m.id for m in materials if _material_is_fissile(m)}

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
            tally_el.set("name", f"cell_{_int_id(cell.id)}_scalars")

            # CellFilter
            filters_el = ET.SubElement(tally_el, "filters")
            cf = ET.SubElement(filters_el, "filter")
            cf.set("type", "cell")
            cf.set("bins", _int_id(cell.id))

            # Scores
            scores_el = ET.SubElement(tally_el, "scores")
            scores_el.text = " ".join(s.value for s in cfg.scores)

            tally_id += 1

    def _append_mesh_tally(
        self,
        root: ET.Element,
        cfg: MeshTallyConfig,
        geometry: CascadeGeometry,
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

        filters_el = ET.SubElement(tally_el, "filters")
        mf = ET.SubElement(filters_el, "filter")
        mf.set("type", "mesh")
        mf.set("bins", "1")

        scores_el = ET.SubElement(tally_el, "scores")
        scores_el.text = " ".join(s.value for s in cfg.scores)

    def _append_spectra_tallies(
        self,
        root: ET.Element,
        cfg: EnergySpectraConfig,
        materials: list[Material],
        material_id_map: dict[str, int],
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

                filters_el = ET.SubElement(tally_el, "filters")

                # MaterialFilter
                mf = ET.SubElement(filters_el, "filter")
                mf.set("type", "material")
                mf.set("bins", str(int_id))

                # EnergyFilter
                ef = ET.SubElement(filters_el, "filter")
                ef.set("type", "energy")
                ef.set("bins", bounds_str)

                scores_el = ET.SubElement(tally_el, "scores")
                scores_el.text = TallyScore.FLUX.value

                tally_id += 1
        else:
            # Single global spectrum — no material filter
            tally_el = ET.SubElement(root, "tally")
            tally_el.set("id", "301")
            tally_el.set("name", "spectrum_global")

            filters_el = ET.SubElement(tally_el, "filters")
            ef = ET.SubElement(filters_el, "filter")
            ef.set("type", "energy")
            ef.set("bins", bounds_str)

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

        Returns:
            materials.xml content as a string.
        """
        if material_id_map is None:
            material_id_map = {mat.id: i + 1 for i, mat in enumerate(materials)}

        root = ET.Element("materials")

        for mat in materials:
            mat_el = ET.Element("material")
            mat_el.set("id", str(material_id_map[mat.id]))
            mat_el.set("name", mat.name)

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

        source_box = settings.source_box

        if source_box is None and geometry is not None and materials is not None:
            source_box = _compute_fissile_source_box(geometry, materials)
        if source_box is None:
            raise ValueError(
                "Cannot determine source distribution. Either provide "
                "source_box in OpenMCRunSettings, or pass geometry and "
                "materials to export_settings() for automatic detection."
            )

        root = ET.Element("settings")

        run_mode_el = ET.SubElement(root, "run_mode")
        run_mode_el.text = settings.run_mode

        particles_el = ET.SubElement(root, "particles")
        particles_el.text = str(settings.particles)

        batches_el = ET.SubElement(root, "batches")
        batches_el.text = str(settings.batches)

        inactive_el = ET.SubElement(root, "inactive")
        inactive_el.text = str(settings.inactive)

        seed_el = ET.SubElement(root, "seed")
        seed_el.text = str(settings.seed)

        # Source definition — isotropic point source at origin for eigenvalue.
        # For fixed source mode this needs to be user-configurable (future work).
        if settings.run_mode == "eigenvalue":
            source_el = ET.SubElement(root, "source")
            space_el = ET.SubElement(source_el, "space")
            space_el.set("type", "box")
            params_el = ET.SubElement(space_el, "parameters")
            params_el.text = " ".join(str(v) for v in source_box)

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
    # Result import (stub — HDF5 parsing is a separate implementation)
    # ------------------------------------------------------------------

    def export_geometry_stub(self, geometry: CascadeGeometry) -> dict[str, object]:
        """Legacy stub — returns geometry as dict. Use export() instead."""
        return {"simulator": self.name, "geometry": geometry.to_dict()}

    def import_results(self, payload: dict[str, object]) -> list[TallyResult]:
        """Import tally results from OpenMC statepoint HDF5 output.

        OpenMC writes results to statepoint.<batch>.h5 files.
        Parsing these requires the h5py library and knowledge of which
        tallies were requested in the input (tallies.xml — not yet generated).

        This is a stub — full implementation comes when tally definition
        and result analysis are added to the pipeline.

        Args:
            payload: Dict containing at minimum {"statepoint_path": "..."}.

        Returns:
            Empty list until implemented.
        """
        # TODO: implement when tallies.xml generation and HDF5 parsing are added
        # Steps:
        #   1. Open statepoint file with h5py
        #   2. Read tally IDs and scores
        #   3. Extract mean + std dev per tally bin
        #   4. Map to TallyResult domain objects
        return []

    # ------------------------------------------------------------------
    # Export tallies - exports tallies from the OpenMC output
    # ------------------------------------------------------------------

    def export_tallies(
            self,
            results_config: ResultsConfig,
            geometry: CascadeGeometry,
            materials: list[Material],
            material_id_map: dict[str, int] | None = None,
    ) -> str:
        """Generate tallies.xml for OpenMC.

        Tally ID ranges (must match results.py parser):
            101–199  Scalar cell tallies (one tally per cell)
            200      Mesh tally
            301+     Energy spectra (one tally per material)

        Args:
            results_config: Which tally groups to emit.
            geometry:       Used to enumerate cells for scalar tallies.
            materials:      Used to enumerate materials for spectra tallies.
            material_id_map: Maps material.id -> integer id (same map used
                             for materials.xml). Auto-generated if None.

        Returns:
            tallies.xml content as a UTF-8 string.
        """
        if material_id_map is None:
            material_id_map = {mat.id: i + 1 for i, mat in enumerate(materials)}

        root = ET.Element("tallies")

        # Filter ID counter — all filters are top-level; tallies reference by ID.
        # OpenMC schema: <filter id="N" type="..."><bins>...</bins></filter>
        #                <tally id="M"><filters>N ...</filters><scores>...</scores></tally>
        next_filter_id = 1

        # ── Group 2: Scalar cell tallies (IDs 101+) ─────────────────────────
        if results_config.scalars.enabled:
            fissile_mat_ids: set[str] = set()
            if not results_config.scalars.all_cells:
                for mat in materials:
                    if _material_is_fissile(mat):
                        fissile_mat_ids.add(mat.id)

            tally_id = 101
            for cell in geometry.cells:
                if cell.material_id is None:
                    continue  # void cells have nothing to tally

                if not results_config.scalars.all_cells:
                    if cell.material_id not in fissile_mat_ids:
                        continue

                # Top-level CellFilter
                cell_filter_id = next_filter_id
                next_filter_id += 1
                f_el = ET.SubElement(root, "filter")
                f_el.set("id", str(cell_filter_id))
                f_el.set("type", "cell")
                bins_el = ET.SubElement(f_el, "bins")
                bins_el.text = _int_id(cell.id)

                tally_el = ET.SubElement(root, "tally")
                tally_el.set("id", str(tally_id))
                tally_el.set("name", f"cell_{_int_id(cell.id)}_scalars")
                filters_el = ET.SubElement(tally_el, "filters")
                filters_el.text = str(cell_filter_id)
                scores_el = ET.SubElement(tally_el, "scores")
                scores_el.text = " ".join(s.value for s in results_config.scalars.scores)

                tally_id += 1

        # ── Group 3: Mesh tally (ID 200) ─────────────────────────────────────
        if results_config.mesh.enabled:
            cfg = results_config.mesh

            # Mesh definition — must appear before the filter that references it
            mesh_el = ET.SubElement(root, "mesh")
            mesh_el.set("id", "1")
            if cfg.mesh_type == MeshType.REGULAR:
                mesh_el.set("type", "regular")
                bounds = _geometry_bounds(geometry)
                lower_el = ET.SubElement(mesh_el, "lower_left")
                lower_el.text = f"{bounds[0]} {bounds[2]} {bounds[4]}"
                upper_el = ET.SubElement(mesh_el, "upper_right")
                upper_el.text = f"{bounds[1]} {bounds[3]} {bounds[5]}"
                dim_el = ET.SubElement(mesh_el, "dimension")
                dim_el.text = f"{cfg.nx} {cfg.ny} {cfg.nz}"
                width_el = ET.SubElement(mesh_el, "width")
                dx = (bounds[1] - bounds[0]) / cfg.nx
                dy = (bounds[3] - bounds[2]) / cfg.ny
                dz = (bounds[5] - bounds[4]) / cfg.nz
                width_el.text = f"{dx:.6g} {dy:.6g} {dz:.6g}"
            else:
                mesh_el.set("type", "cylindrical")
                bounds = _geometry_bounds(geometry)
                origin_el = ET.SubElement(mesh_el, "origin")
                origin_el.text = "0.0 0.0 0.0"
                r_el = ET.SubElement(mesh_el, "r_grid")
                max_r = max(abs(bounds[0]), abs(bounds[1]),
                            abs(bounds[2]), abs(bounds[3]))
                r_step = max_r / cfg.nr
                r_el.text = " ".join(f"{i * r_step:.6g}" for i in range(cfg.nr + 1))
                z_el = ET.SubElement(mesh_el, "z_grid")
                z_step = (bounds[5] - bounds[4]) / cfg.nz_cyl
                z_el.text = " ".join(
                    f"{bounds[4] + i * z_step:.6g}" for i in range(cfg.nz_cyl + 1)
                )

            # Top-level MeshFilter
            mesh_filter_id = next_filter_id
            next_filter_id += 1
            mf_el = ET.SubElement(root, "filter")
            mf_el.set("id", str(mesh_filter_id))
            mf_el.set("type", "mesh")
            mf_bins_el = ET.SubElement(mf_el, "bins")
            mf_bins_el.text = "1"

            tally_el = ET.SubElement(root, "tally")
            tally_el.set("id", "200")
            tally_el.set("name", "power_flux_mesh")
            filters_el = ET.SubElement(tally_el, "filters")
            filters_el.text = str(mesh_filter_id)
            scores_el = ET.SubElement(tally_el, "scores")
            scores_el.text = " ".join(s.value for s in cfg.scores)

        # ── Group 4: Energy spectra (IDs 301+) ───────────────────────────────
        if results_config.spectra.enabled:
            cfg = results_config.spectra
            boundaries = cfg.boundaries()

            if boundaries:
                energy_bins_text = " ".join(f"{e:.6g}" for e in boundaries)

                # One shared top-level EnergyFilter for all spectrum tallies
                energy_filter_id = next_filter_id
                next_filter_id += 1
                ef_el = ET.SubElement(root, "filter")
                ef_el.set("id", str(energy_filter_id))
                ef_el.set("type", "energy")
                ef_bins_el = ET.SubElement(ef_el, "bins")
                ef_bins_el.text = energy_bins_text

                if cfg.per_material:
                    for tally_id, mat in enumerate(materials, start=301):
                        mat_int_id = material_id_map.get(mat.id, tally_id - 300)

                        # Top-level MaterialFilter for this material
                        mat_filter_id = next_filter_id
                        next_filter_id += 1
                        mat_f_el = ET.SubElement(root, "filter")
                        mat_f_el.set("id", str(mat_filter_id))
                        mat_f_el.set("type", "material")
                        mat_bins_el = ET.SubElement(mat_f_el, "bins")
                        mat_bins_el.text = str(mat_int_id)

                        tally_el = ET.SubElement(root, "tally")
                        tally_el.set("id", str(tally_id))
                        tally_el.set("name", f"spectrum_{mat.id}")
                        filters_el = ET.SubElement(tally_el, "filters")
                        filters_el.text = f"{energy_filter_id} {mat_filter_id}"
                        scores_el = ET.SubElement(tally_el, "scores")
                        scores_el.text = "flux"
                else:
                    tally_el = ET.SubElement(root, "tally")
                    tally_el.set("id", "301")
                    tally_el.set("name", "spectrum_global")
                    filters_el = ET.SubElement(tally_el, "filters")
                    filters_el.text = str(energy_filter_id)
                    scores_el = ET.SubElement(tally_el, "scores")
                    scores_el.text = "flux"

        return _pretty_xml(root)