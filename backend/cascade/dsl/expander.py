"""Geometry expander v4 — adds Tier-1 primitive / Tier-2 Cell resolution
(geometry-restructuring-plan.md Phases A/B) ahead of the existing v3
Box/FuelPin/lattice template pipeline. See the v3 docstring below for the
duplicate-z-plane fix that pipeline still relies on — nothing about it
changed in this pass.

v3 docstring (kept — still describes the legacy template pipeline exactly):

    Root cause of v2 bug:
        The fuel pin template created its own axial z-planes at z=0 and z=height.
        The box template also created z-planes at z=0 and z=height.
        After translation these were geometrically identical but separate surfaces.
        OpenMC saw regions where the fill cell and pin cells had inconsistent
        axial bounds — particles crossing into those regions got lost.

    Fix:
        The fuel pin template now produces ONLY radial surfaces (cylinders).
        Axial bounds are contributed solely by the Box placement.
        The box z-planes are stored in ctx after Phase 2B so that Phase 2A
        pin placements can reference them.

Phase A/B addition (new in v4):
    Tier-1 primitives (Sphere, PlaneX/Y/Z, CylinderX/Y/Z — see
    dsl/primitives.py) and Tier-2 Cells (dsl/schema/cell.py) are completely
    independent of the legacy template/placement pipeline: a primitive
    carries its own position already, and a Cell only ever references
    primitive names, never a placement. They're resolved in a new Step 0,
    before the legacy pipeline's Box-then-pins two-pass logic even starts.

    Ordering note (important, deliberate, not an oversight): EVERY Tier-1
    primitive in the document is expanded first, building a complete
    name -> Surface.id map, before ANY Cell's region is resolved against
    that map. This means a Cell may reference a primitive declared LATER
    in the YAML file — unlike the legacy template/placement rule (template
    before placement), which is still enforced for that pipeline only.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from ..domain.geometry import (
    BoundaryType, CascadeGeometry, Cell, Complement,
    Inside, Intersection, Outside, Region, Surface, SurfaceType, Union,
    region_from_yaml_dict,
)
from . import primitives
from .schema.base import BaseComponentSchema
from .schema.box import BoxSchema
from .schema.cell import CellSchema
from .schema.fuel_pin import FuelPinSchema
from .schema.lattice import HexLatticeSchema, SquareLatticeSchema
from .schema.single_placement import SinglePlacementSchema


# ---------------------------------------------------------------------------
# Context
# ---------------------------------------------------------------------------

@dataclass
class Context:
    param_values:       dict[str, float]       = field(default_factory=dict)
    outermost_surfaces: list[str]              = field(default_factory=list)
    # Axial bounds from the placed Box — shared by all pin placements
    axial_bot_id:       str | None             = field(default=None)
    axial_top_id:       str | None             = field(default=None)
    _counter:           int                    = field(default=0, init=False, repr=False)

    def fresh_id(self, prefix: str = "s") -> str:
        self._counter += 1
        return f"{prefix}{self._counter}"

    def has_axial_bounds(self) -> bool:
        return self.axial_bot_id is not None and self.axial_top_id is not None


# ---------------------------------------------------------------------------
# Translation helpers
# ---------------------------------------------------------------------------

_TRANSLATE_PARAMS: dict[SurfaceType, dict[str, str]] = {
    SurfaceType.PLANE_X:    {"x": "dx", "x0": "dx"},
    SurfaceType.PLANE_Y:    {"y": "dy", "y0": "dy"},
    SurfaceType.PLANE_Z:    {"z": "dz", "z0": "dz"},
    SurfaceType.CYLINDER_Z: {"x": "dx", "x0": "dx", "y": "dy", "y0": "dy"},
    SurfaceType.CYLINDER_X: {"y": "dy", "y0": "dy", "z": "dz", "z0": "dz"},
    SurfaceType.CYLINDER_Y: {"x": "dx", "x0": "dx", "z": "dz", "z0": "dz"},
    SurfaceType.SPHERE:     {"x": "dx", "x0": "dx", "y": "dy", "y0": "dy",
                             "z": "dz", "z0": "dz"},
}


def _translate_params(type_: SurfaceType, params: dict,
                      dx: float, dy: float, dz: float) -> dict:
    offsets = {"dx": dx, "dy": dy, "dz": dz}
    axes    = _TRANSLATE_PARAMS.get(type_, {})
    result  = dict(params)
    for param_key, offset_key in axes.items():
        if param_key in result:
            result[param_key] = float(result[param_key]) + offsets[offset_key]
    return result


def _remap_region(region: Region, id_map: dict[str, str]) -> Region:
    if isinstance(region, Inside):
        return Inside(id_map.get(region.surface_id, region.surface_id))
    if isinstance(region, Outside):
        return Outside(id_map.get(region.surface_id, region.surface_id))
    if isinstance(region, Intersection):
        return Intersection([_remap_region(r, id_map) for r in region.regions])
    if isinstance(region, Union):
        return Union([_remap_region(r, id_map) for r in region.regions])
    if isinstance(region, Complement):
        return Complement(_remap_region(region.region, id_map))
    return region


def _translate(objects: list[Surface | Cell], dx: float, dy: float, dz: float,
               ctx: Context) -> tuple[list[Surface | Cell], dict[str, str]]:
    """Deep-copy objects with fresh IDs and translated coordinates."""
    id_map:       dict[str, str] = {}
    new_surfaces: list[Surface]  = []
    new_cells:    list[Cell]     = []

    for obj in objects:
        if isinstance(obj, Surface):
            new_id = ctx.fresh_id("s")
            id_map[obj.id] = new_id
            new_surfaces.append(Surface(
                id=            new_id,
                type_=         obj.type_,
                params=        _translate_params(obj.type_, obj.params, dx, dy, dz),
                boundary_type= obj.boundary_type,
            ))

    for obj in objects:
        if isinstance(obj, Cell):
            new_cells.append(Cell(
                id=          ctx.fresh_id("c"),
                region=      _remap_region(obj.region, id_map),
                material_id= obj.material_id,
                name=        obj.name,
            ))

    return new_surfaces + new_cells, id_map


# ---------------------------------------------------------------------------
# Template expanders — produce geometry at the origin, NO axial planes
# for FuelPin (axial bounds come from the Box)
# ---------------------------------------------------------------------------

def _expand_fuel_pin_radial(
    schema: FuelPinSchema,
    ctx: Context,
) -> tuple[list[Surface], str | None]:
    surfaces: list[Surface] = []
    layer_surfaces: list[Surface] = []

    for outer_r, _ in schema.radial_layers():
        s = Surface(id=ctx.fresh_id("s"), type_=SurfaceType.CYLINDER_Z,
                    params={"r": outer_r, "x0": 0.0, "y0": 0.0})
        surfaces.append(s)
        layer_surfaces.append(s)

    outermost_id = layer_surfaces[-1].id if layer_surfaces else None
    return surfaces, outermost_id


def _build_fuel_pin_cells(
    schema: FuelPinSchema,
    layer_surface_ids: list[str],
    bot_id: str,
    top_id: str,
    ctx: Context,
    cell_name_prefix: str,
) -> list[Cell]:
    cells: list[Cell] = []
    axial = [Outside(bot_id), Inside(top_id)]

    for i, (_, mat_id) in enumerate(schema.radial_layers()):
        outer_id = layer_surface_ids[i]
        radial = (
            [Inside(outer_id)]
            if i == 0
            else [Outside(layer_surface_ids[i-1]), Inside(outer_id)]
        )
        cells.append(Cell(
            id=          ctx.fresh_id("c"),
            region=      Intersection(radial + axial),
            material_id= mat_id,
            name=        f"{cell_name_prefix}_layer{i}",
        ))
    return cells


def _expand_box_surfaces(
    schema: BoxSchema, ctx: Context,
) -> tuple[list[Surface], dict[str, str]]:
    hx = schema.half_x()
    hy = schema.half_y()
    bt = schema.boundary_type

    s_xlo = Surface(id=ctx.fresh_id("s"), type_=SurfaceType.PLANE_X,
                    params={"x": -hx}, boundary_type=bt)
    s_xhi = Surface(id=ctx.fresh_id("s"), type_=SurfaceType.PLANE_X,
                    params={"x": +hx}, boundary_type=bt)
    s_ylo = Surface(id=ctx.fresh_id("s"), type_=SurfaceType.PLANE_Y,
                    params={"y": -hy}, boundary_type=bt)
    s_yhi = Surface(id=ctx.fresh_id("s"), type_=SurfaceType.PLANE_Y,
                    params={"y": +hy}, boundary_type=bt)
    s_bot = Surface(id=ctx.fresh_id("s"), type_=SurfaceType.PLANE_Z,
                    params={"z": 0.0}, boundary_type=bt)
    s_top = Surface(id=ctx.fresh_id("s"), type_=SurfaceType.PLANE_Z,
                    params={"z": schema.z_size}, boundary_type=bt)

    label_map = {
        "xlo": s_xlo.id, "xhi": s_xhi.id,
        "ylo": s_ylo.id, "yhi": s_yhi.id,
        "bot": s_bot.id, "top": s_top.id,
    }
    return [s_xlo, s_xhi, s_ylo, s_yhi, s_bot, s_top], label_map


# ---------------------------------------------------------------------------
# Placement functions
# ---------------------------------------------------------------------------

def _place_box(
    schema: BoxSchema,
    position: tuple[float, float, float],
    ctx: Context,
) -> list[Surface | Cell]:
    dx, dy, dz = position
    box_surfaces, label_map = _expand_box_surfaces(schema, ctx)

    translated: list[Surface] = []
    id_map: dict[str, str] = {}
    for s in box_surfaces:
        new_id = ctx.fresh_id("s")
        id_map[s.id] = new_id
        translated.append(Surface(
            id=            new_id,
            type_=         s.type_,
            params=        _translate_params(s.type_, s.params, dx, dy, dz),
            boundary_type= s.boundary_type,
        ))

    translated_labels = {k: id_map[v] for k, v in label_map.items()}

    ctx.axial_bot_id = translated_labels["bot"]
    ctx.axial_top_id = translated_labels["top"]

    return translated, translated_labels


def _place_fuel_pin(
    schema: FuelPinSchema,
    position: tuple[float, float, float],
    ctx: Context,
    cell_name_prefix: str,
) -> list[Surface | Cell]:
    if not ctx.has_axial_bounds():
        raise RuntimeError(
            "Cannot place a FuelPin without axial bounds. "
            "Place a Box (via SinglePlacement) before placing fuel pins. "
            "The Box provides the z-plane surfaces shared by all pins."
        )

    dx, dy, dz = position

    radial_surfaces, outermost_id = _expand_fuel_pin_radial(schema, ctx)

    translated_surfaces: list[Surface] = []
    id_map: dict[str, str] = {}
    for s in radial_surfaces:
        new_id = ctx.fresh_id("s")
        id_map[s.id] = new_id
        translated_surfaces.append(Surface(
            id=            new_id,
            type_=         s.type_,
            params=        _translate_params(s.type_, s.params, dx, dy, 0.0),
            boundary_type= s.boundary_type,
        ))

    if outermost_id:
        new_outermost = id_map.get(outermost_id)
        if new_outermost:
            ctx.outermost_surfaces.append(new_outermost)

    translated_layer_ids = [id_map[s.id] for s in radial_surfaces]
    cells = _build_fuel_pin_cells(
        schema=            schema,
        layer_surface_ids= translated_layer_ids,
        bot_id=            ctx.axial_bot_id,
        top_id=            ctx.axial_top_id,
        ctx=               ctx,
        cell_name_prefix=  cell_name_prefix,
    )

    return translated_surfaces + cells


def _build_fill_cell(
    schema: BoxSchema,
    translated_labels: dict[str, str],
    ctx: Context,
    cell_name: str,
) -> Cell:
    box_interior = [
        Outside(translated_labels["xlo"]),
        Inside(translated_labels["xhi"]),
        Outside(translated_labels["ylo"]),
        Inside(translated_labels["yhi"]),
        Outside(translated_labels["bot"]),
        Inside(translated_labels["top"]),
    ]
    outer_exclusions = [Outside(sid) for sid in ctx.outermost_surfaces]

    return Cell(
        id=          ctx.fresh_id("c"),
        region=      Intersection(box_interior + outer_exclusions),
        material_id= schema.material,
        name=        cell_name,
    )


# ---------------------------------------------------------------------------
# Main expand()
# ---------------------------------------------------------------------------

def expand(
    schemas:      dict[str, BaseComponentSchema],
    param_values: dict[str, float] | None = None,
    geom_name:    str = "cascade_geometry",
) -> CascadeGeometry:
    """Expand validated schemas into a CascadeGeometry.

    Step 0 (new — Phase A/B): expand every Tier-1 primitive, then resolve
        every Tier-2 Cell's region against the complete primitive
        name -> Surface.id map. See module docstring's ordering note.
    Step 1: Find the Box SinglePlacement, translate its surfaces,
        register axial bounds in ctx. (legacy template pipeline)
    Step 2: Place all fuel pin placements (SinglePlacement + lattices),
        sharing the box's z-planes as axial bounds. (legacy)
    Step 3: Build the fill cell using ctx.outermost_surfaces. (legacy)
    """
    ctx = Context(param_values=param_values or {})
    all_objects: list[Surface | Cell] = []

    # ------------------------------------------------------------------
    # Step 0 — Tier-1 primitives and Tier-2 Cells (new in v4)
    # ------------------------------------------------------------------
    primitive_name_to_id: dict[str, str] = {}
    cell_schemas: dict[str, CellSchema] = {}
    legacy_schemas: dict[str, BaseComponentSchema] = {}

    for name, schema in schemas.items():
        if primitives.is_primitive(schema):
            surfaces, _region = primitives.expand_primitive(ctx, schema, name)
            all_objects.extend(surfaces)
            # Every registered primitive expander returns exactly one
            # surface today (see dsl/primitives.py) — the last surface is
            # always the one a Cell's Inside/Outside(name) resolves to.
            primitive_name_to_id[name] = surfaces[-1].id
        elif isinstance(schema, CellSchema):
            cell_schemas[name] = schema
        else:
            legacy_schemas[name] = schema

    for name, cell_schema in cell_schemas.items():
        region = region_from_yaml_dict(cell_schema.region, primitive_name_to_id)
        all_objects.append(Cell(
            id=          ctx.fresh_id("c"),
            region=      region,
            material_id= cell_schema.material,
            name=        name,
        ))

    # ------------------------------------------------------------------
    # Legacy pipeline (Box/FuelPin/lattice templates + placements) —
    # unchanged from v3, operating only on whatever wasn't a Tier-1
    # primitive or Tier-2 Cell above.
    # ------------------------------------------------------------------
    templates:  dict[str, BaseComponentSchema] = {}
    placements: dict[str, BaseComponentSchema] = {}

    _PLACEMENT_TYPES = (SinglePlacementSchema, SquareLatticeSchema, HexLatticeSchema)
    for name, schema in legacy_schemas.items():
        if isinstance(schema, _PLACEMENT_TYPES):
            placements[name] = schema
        else:
            templates[name] = schema

    box_schema:            BoxSchema | None        = None
    box_translated_labels: dict[str, str] | None  = None
    box_placement_name:    str | None              = None

    # ------------------------------------------------------------------
    # Step 1 — Box placements first
    # ------------------------------------------------------------------
    for name, schema in placements.items():
        if not isinstance(schema, SinglePlacementSchema):
            continue
        tpl = templates.get(schema.template)
        if not isinstance(tpl, BoxSchema):
            continue

        if box_schema is not None:
            raise ValueError(
                "Only one Box placement is supported per geometry. "
                "Use a single Box that encompasses all inner geometry."
            )

        placed_surfaces, translated_labels = _place_box(tpl, schema.position(), ctx)
        all_objects.extend(placed_surfaces)
        box_schema            = tpl
        box_translated_labels = translated_labels
        box_placement_name    = name

    # ------------------------------------------------------------------
    # Step 2 — Fuel pin placements (SinglePlacement + lattices)
    # ------------------------------------------------------------------
    for name, schema in placements.items():
        if isinstance(schema, SinglePlacementSchema):
            tpl = templates.get(schema.template)
            if tpl is None:
                raise ValueError(
                    f"SinglePlacement '{name}' references undefined template "
                    f"'{schema.template}'."
                )
            if isinstance(tpl, BoxSchema):
                continue  # already handled in Step 1

            if isinstance(tpl, FuelPinSchema):
                placed = _place_fuel_pin(tpl, schema.position(), ctx, cell_name_prefix=name)
                all_objects.extend(placed)
            else:
                raise TypeError(
                    f"SinglePlacement '{name}' references template of type "
                    f"'{type(tpl).__name__}' which has no placement handler. "
                    f"Add it to the expander."
                )

        elif isinstance(schema, SquareLatticeSchema):
            tpl = templates.get(schema.template)
            if tpl is None:
                raise ValueError(
                    f"SquareLattice '{name}' references undefined template "
                    f"'{schema.template}'."
                )
            if isinstance(tpl, FuelPinSchema):
                for i, pos in enumerate(schema.pin_positions()):
                    placed = _place_fuel_pin(tpl, pos, ctx, cell_name_prefix=f"{name}_{i}")
                    all_objects.extend(placed)
            else:
                raise TypeError(
                    f"SquareLattice '{name}': template type "
                    f"'{type(tpl).__name__}' not supported in lattices yet."
                )

        elif isinstance(schema, HexLatticeSchema):
            tpl = templates.get(schema.template)
            if tpl is None:
                raise ValueError(
                    f"HexLattice '{name}' references undefined template "
                    f"'{schema.template}'."
                )
            if isinstance(tpl, FuelPinSchema):
                for i, pos in enumerate(schema.pin_positions()):
                    placed = _place_fuel_pin(tpl, pos, ctx, cell_name_prefix=f"{name}_{i}")
                    all_objects.extend(placed)
            else:
                raise TypeError(
                    f"HexLattice '{name}': template type "
                    f"'{type(tpl).__name__}' not supported yet."
                )

    # ------------------------------------------------------------------
    # Step 3 — Fill cell
    # ------------------------------------------------------------------
    if box_schema is not None and box_translated_labels is not None:
        fill_cell = _build_fill_cell(
            box_schema, box_translated_labels, ctx,
            cell_name=box_placement_name or f"fill_{box_schema.material}",
        )
        all_objects.append(fill_cell)

    surfaces = [obj for obj in all_objects if isinstance(obj, Surface)]
    cells    = [obj for obj in all_objects if isinstance(obj, Cell)]

    return CascadeGeometry(
        id=           str(uuid.uuid4()),
        name=         geom_name,
        surfaces=     surfaces,
        cells=        cells,
        param_values= ctx.param_values,
    )
