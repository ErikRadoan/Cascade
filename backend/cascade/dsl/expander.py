"""Geometry expander v3 — fixes the duplicate z-plane / lost particle bug.

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

    This requires a two-pass approach:
        Pass 1:  Expand box template, translate, store z-planes in ctx.
        Pass 2:  Expand and translate fuel pin placements using ctx z-planes.

    Declaration order in YAML still doesn't matter — we always process
    Box SinglePlacements before other placements.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from ..domain.geometry import (
    BoundaryType, CascadeGeometry, Cell, Complement,
    Inside, Intersection, Outside, Region, Surface, SurfaceType, Union,
)
from .schema.base import BaseComponentSchema
from .schema.box import BoxSchema
from .schema.fuel_pin import FuelPinSchema
from .schema.lattice import HexLatticeSchema, SquareLatticeSchema
from .schema.single_placement import SinglePlacementSchema
from .schema.sphere import SphereSchema
from .schema.boolean import UnionSchema, SubtractionSchema, IntersectionSchema

# ---------------------------------------------------------------------------
# Context
# ---------------------------------------------------------------------------

@dataclass
class Context:
    param_values:       dict[str, float]       = field(default_factory=dict)
    # Regions already claimed by placed inner objects — the Box's fill
    # cell excludes Complement() of each of these, so any object placed
    # inside the boundary correctly carves a hole out of the fill
    # material regardless of the object's shape (a single convex radius,
    # as with FuelPin, or an arbitrary CSG union/subtraction/intersection
    # tree). Replaces the old `outermost_surfaces: list[str]` field, which
    # only worked because every placeable shape used to be a single
    # cylinder's Outside(id) — that assumption breaks for a non-convex
    # boolean shape, where "outside the shape" isn't expressible as one
    # surface's positive halfspace.
    excluded_regions:   list[Region]            = field(default_factory=list)
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
    """Expand fuel pin radial surfaces ONLY — no z-planes.

    Z-planes are shared from the Box placement via ctx.axial_bot_id / axial_top_id.
    This prevents duplicate coincident surfaces that cause lost particles.

    Returns (radial_surfaces, outermost_cylinder_id).
    """
    surfaces: list[Surface] = []
    layer_surfaces: list[Surface] = []

    for outer_r, _ in schema.radial_layers():
        # x0/y0 must be present (even at the origin-default 0.0) so that
        # _translate_params has keys to shift below — it only translates
        # params that already exist on the dict (`if param_key in result`).
        # Without them, every pin placement silently no-ops the x/y shift
        # and every instance in a lattice ends up centered on the origin,
        # fully overlapping in the actual OpenMC model even though each
        # gets a distinct cell id/name and a correct `position` in the
        # scene (which is why this bug is invisible in the 3D editor and
        # only shows up as identical tally results per lattice instance).
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
    """Build fuel pin cells using provided surface IDs.

    Uses the axial bound IDs from context (shared with the box).

    `cell_name_prefix` is the SAME string SceneBuilder uses to name this
    pin's SceneComponent (the placement name, or f"{name}_{i}" for a
    lattice instance — see scene_builder_service.py's build()/_build_placed).
    Cell.name is set to f"{cell_name_prefix}_layer{i}" for the i-th radial
    layer, which is exactly how scene_builder_service.py's _build_fuel_pin
    labels each CylinderLayer's cell_name. This is what lets a tally result
    (openmc_adapter.py embeds cell.name in the tally's `name` attribute) be
    matched back to a specific pin+layer in the 3D viewer — previously
    Cell.name was f"{mat_id}_layer_{i}", which collided across every pin
    of the same fuel type and carried no placement information at all.
    """
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
    """Expand box boundary surfaces at origin.

    Returns (surfaces, label_map) where label_map maps
    'bot'/'top'/'xlo'/'xhi'/'ylo'/'yhi' to surface IDs.
    """
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
# Self-contained shape expansion — Sphere, Box, and boolean composites
#
# Unlike FuelPin (radial surfaces only, borrows axial bounds from a placed
# Box — see _place_fuel_pin), every shape reachable from here produces its
# own complete surface set at the origin, so it can be placed standalone
# via SinglePlacement OR nested as an operand inside a Union/Subtraction/
# Intersection with no special-casing at the placement site.
# ---------------------------------------------------------------------------

_BOOLEAN_SCHEMAS = (UnionSchema, SubtractionSchema, IntersectionSchema)


def _expand_shape_at_origin(
    template_name: str,
    templates: dict[str, BaseComponentSchema],
    ctx: Context,
) -> tuple[list[Surface], Region]:
    """Return (surfaces, region) for `template_name` at the origin.

    Recurses into Union/Subtraction/Intersection operands. Raises
    TypeError for FuelPin (see boolean.py's module docstring) or any
    other template type with no self-contained shape.
    """
    schema = templates.get(template_name)
    if schema is None:
        raise ValueError(f"Undefined template '{template_name}'.")

    if isinstance(schema, SphereSchema):
        s = Surface(
            id=ctx.fresh_id("s"), type_=SurfaceType.SPHERE,
            params={"r": schema.radius, "x0": 0.0, "y0": 0.0, "z0": 0.0},
            boundary_type=schema.boundary_type,
        )
        return [s], Inside(s.id)

    if isinstance(schema, BoxSchema):
        surfaces, label_map = _expand_box_surfaces(schema, ctx)
        region = Intersection([
            Outside(label_map["xlo"]), Inside(label_map["xhi"]),
            Outside(label_map["ylo"]), Inside(label_map["yhi"]),
            Outside(label_map["bot"]), Inside(label_map["top"]),
        ])
        return surfaces, region

    if isinstance(schema, _BOOLEAN_SCHEMAS):
        a_surfaces, a_region = _expand_shape_at_origin(schema.a, templates, ctx)
        b_surfaces, b_region = _expand_shape_at_origin(schema.b, templates, ctx)
        combined_surfaces = a_surfaces + b_surfaces

        if isinstance(schema, UnionSchema):
            region = Union([a_region, b_region])
        elif isinstance(schema, SubtractionSchema):
            region = Intersection([a_region, Complement(b_region)])
        else:  # IntersectionSchema
            region = Intersection([a_region, b_region])

        return combined_surfaces, region

    raise TypeError(
        f"Template '{template_name}' of type {type(schema).__name__} has no "
        f"self-contained shape and cannot be placed standalone or used as a "
        f"Union/Subtraction/Intersection operand. FuelPin in particular "
        f"depends on shared axial bounds from a placed Box — place it via "
        f"SinglePlacement/lattice against a Box boundary instead."
    )


def _place_boolean_shape(
    template_name: str,
    templates: dict[str, BaseComponentSchema],
    position: tuple[float, float, float],
    ctx: Context,
    cell_name: str,
    material: str,
) -> list[Surface | Cell]:
    """Place a self-contained shape (Sphere/Box/boolean composite) as one
    filled cell at `position`. No axial-bounds dependency, unlike
    _place_fuel_pin — every surface this needs comes from
    _expand_shape_at_origin().
    """
    origin_surfaces, origin_region = _expand_shape_at_origin(template_name, templates, ctx)
    dx, dy, dz = position

    translated: list[Surface] = []
    id_map: dict[str, str] = {}
    for s in origin_surfaces:
        new_id = ctx.fresh_id("s")
        id_map[s.id] = new_id
        translated.append(Surface(
            id=            new_id,
            type_=         s.type_,
            params=        _translate_params(s.type_, s.params, dx, dy, dz),
            boundary_type= s.boundary_type,
        ))

    region = _remap_region(origin_region, id_map)
    cell = Cell(id=ctx.fresh_id("c"), region=region, material_id=material, name=cell_name)

    # Register this shape's occupied region so a surrounding Box's fill
    # cell excludes exactly the volume it occupies — correct even for a
    # non-convex union/subtraction, unlike the single-cylinder
    # approximation _place_fuel_pin uses (see Context.excluded_regions).
    ctx.excluded_regions.append(region)

    return [*translated, cell]

# ---------------------------------------------------------------------------
# Placement functions
# ---------------------------------------------------------------------------

def _place_box(
    schema: BoxSchema,
    position: tuple[float, float, float],
    ctx: Context,
) -> list[Surface | Cell]:
    """Place a Box — translate its surfaces and register axial bounds in ctx.

    The fill cell is built after all inner placements so outermost_surfaces
    is complete. We store the translated z-plane IDs in ctx here so pin
    placements that follow can reuse them.
    """
    dx, dy, dz = position
    box_surfaces, label_map = _expand_box_surfaces(schema, ctx)

    # Translate all box surfaces
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

    # Remap label_map to translated IDs
    translated_labels = {k: id_map[v] for k, v in label_map.items()}

    # Store axial bounds in context — pins placed after this share these surfaces
    ctx.axial_bot_id = translated_labels["bot"]
    ctx.axial_top_id = translated_labels["top"]

    return translated, translated_labels


def _place_fuel_pin(
    schema: FuelPinSchema,
    position: tuple[float, float, float],
    ctx: Context,
    cell_name_prefix: str,
) -> list[Surface | Cell]:
    """Place one fuel pin instance.

    Requires ctx.axial_bot_id and ctx.axial_top_id to be set
    (i.e. a Box must have been placed first).
    """
    if not ctx.has_axial_bounds():
        raise RuntimeError(
            "Cannot place a FuelPin without axial bounds. "
            "Place a Box (via SinglePlacement) before placing fuel pins. "
            "The Box provides the z-plane surfaces shared by all pins."
        )

    dx, dy, dz = position

    # Expand radial surfaces at origin, then translate in X/Y only
    # (z stays 0 — axial bounds come from the box's z-planes)
    radial_surfaces, outermost_id = _expand_fuel_pin_radial(schema, ctx)

    # Translate only X and Y (cylinders have no z-position param, but
    # their x0/y0 centre coordinates shift with the placement)
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

    # Register this pin's occupied region so the box's fill cell excludes
    # it (see Context.excluded_regions). Radial-only, same approximation
    # as before this generalization — the pin's axial extent is assumed
    # to already match the box's own axial bounds, so excluding just the
    # "inside this radius" cross-section is sufficient.
    if outermost_id:
        new_outermost = id_map.get(outermost_id)
        if new_outermost:
            ctx.excluded_regions.append(Inside(new_outermost))

    # Build cells using translated surface IDs and shared axial bounds
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
    """Build the water fill cell once all pins are placed.

    `cell_name` is the box placement's name — the same string SceneBuilder
    uses for this Box's SceneComponent (see scene_builder_service.py's
    _build_box), so a tally on this cell can be matched back to the box
    in the 3D viewer the same way fuel pin layers are (see
    _build_fuel_pin_cells). Previously this was f"fill_{schema.material}",
    which carried no placement identity — fine when there's only one Box
    per geometry (the only case supported today), but worth naming
    consistently now rather than adding another special case later.
    """
    box_interior = [
        Outside(translated_labels["xlo"]),
        Inside(translated_labels["xhi"]),
        Outside(translated_labels["ylo"]),
        Inside(translated_labels["yhi"]),
        Outside(translated_labels["bot"]),
        Inside(translated_labels["top"]),
    ]
    # Complement() of every placed inner object's own region — see
    # Context.excluded_regions docstring for why this replaced the old
    # per-surface Outside(sid) list.
    outer_exclusions = [Complement(r) for r in ctx.excluded_regions]

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

    Ordering (enforced regardless of YAML declaration order):
        Step 1: Find the Box SinglePlacement, translate its surfaces,
                register axial bounds in ctx.
        Step 2: Place all fuel pin placements (SinglePlacement + lattices),
                sharing the box's z-planes as axial bounds.
        Step 3: Build the fill cell using ctx.outermost_surfaces.
    """
    ctx = Context(param_values=param_values or {})

    # Separate templates from placements
    templates:  dict[str, BaseComponentSchema] = {}
    placements: dict[str, BaseComponentSchema] = {}

    _PLACEMENT_TYPES = (SinglePlacementSchema, SquareLatticeSchema, HexLatticeSchema)
    for name, schema in schemas.items():
        if isinstance(schema, _PLACEMENT_TYPES):
            placements[name] = schema
        else:
            templates[name] = schema

    all_objects:           list[Surface | Cell]   = []
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
    _FUEL_TYPES = (FuelPinSchema,)

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
            elif isinstance(tpl, (SphereSchema, UnionSchema, SubtractionSchema, IntersectionSchema)):
                placed = _place_boolean_shape(
                    schema.template, templates, schema.position(), ctx,
                    cell_name=name, material=tpl.material,
                )
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