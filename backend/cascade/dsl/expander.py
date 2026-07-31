"""Geometry expander v5 — Phase C of geometry-restructuring-plan.md: template
dispatch (FuelPin, and any future composite template) now goes through
templates.TEMPLATE_REGISTRY instead of a hardcoded isinstance chain. Box
stays a single, explicitly-documented exception to that registry — see
"The Box axial-sharing problem" below and in templates.py's module
docstring.

v4 docstring (kept — Phase A/B, unchanged in this pass):

    Tier-1 primitives (Sphere, PlaneX/Y/Z, CylinderX/Y/Z — see
    dsl/primitives.py) and Tier-2 Cells (dsl/schema/cell.py) are completely
    independent of the legacy template/placement pipeline: a primitive
    carries its own position already, and a Cell only ever references
    primitive names, never a placement. They're resolved in Step 0, before
    the template/placement pipeline below even starts.

    Ordering note (important, deliberate, not an oversight): EVERY Tier-1
    primitive in the document is expanded first, building a complete
    name -> Surface.id map, before ANY Cell's region is resolved against
    that map. This means a Cell may reference a primitive declared LATER
    in the YAML file — unlike the template/placement rule (template before
    placement), which is still enforced for that pipeline only.

v3 docstring (kept — still describes the original duplicate-z-plane fix
this pipeline relies on):

    Root cause of v2 bug:
        The fuel pin template created its own axial z-planes at z=0 and z=height.
        The box template also created z-planes at z=0 and z=height.
        After translation these were geometrically identical but separate surfaces.
        OpenMC saw regions where the fill cell and pin cells had inconsistent
        axial bounds — particles crossing into those regions got lost.

    Fix:
        The fuel pin template now produces ONLY radial surfaces (cylinders).
        Axial bounds are contributed solely by the Box placement.
        The box z-planes are stored in ctx after Step 1 so that Step 2's
        pin placements can reference them.

The Box axial-sharing problem (Phase C §6):
    Box's fill cell is `box interior AND NOT (any placed pin's outer
    surface)`. But pins are placed AFTER the box specifically so they can
    borrow the box's z-planes as their own axial bounds (see above) — so
    the set of "any placed pin's outer surface" isn't complete until every
    pin placement has run. Box's fill cell literally cannot be built in a
    single "return everything now" call the way templates.py's
    CompositeTemplateExpander protocol wants. Rather than complicate that
    protocol with a two-phase (surfaces-now, cells-later) shape for the
    sake of one template, Box stays a narrow, explicitly-named exception
    here: expanded first (Step 1, surfaces only, axial IDs stashed in
    ctx), with its fill cell built last (Step 3, once ctx.outermost_surfaces
    is complete). It is deliberately NOT registered in
    templates.TEMPLATE_REGISTRY — see that module's docstring.
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
from . import primitives, templates
from .primitives import translate_params
from .schema.base import BaseComponentSchema
from .schema.box import BoxSchema
from .schema.cell import CellSchema
from .schema.fuel_pin import FuelPinSchema
from .schema.lattice import HexLatticeSchema, SquareLatticeSchema
from .schema.plane import PlaneXSchema, PlaneYSchema, PlaneZSchema
from .schema.single_placement import SinglePlacementSchema


# ---------------------------------------------------------------------------
# Context
# ---------------------------------------------------------------------------

@dataclass
class Context:
    param_values:       dict[str, float]       = field(default_factory=dict)
    outermost_surfaces: list[str]              = field(default_factory=list)
    # Axial bounds from the placed Box — shared by all pin placements.
    # Populated ONLY by the Box exception below; read by
    # templates._FuelPinTemplateExpander (and any future template that
    # needs a shared axial extent).
    axial_bot_id:       str | None             = field(default=None)
    axial_top_id:       str | None             = field(default=None)
    _counter:           int                    = field(default=0, init=False, repr=False)

    def fresh_id(self, prefix: str = "s") -> str:
        self._counter += 1
        return f"{prefix}{self._counter}"

    def has_axial_bounds(self) -> bool:
        return self.axial_bot_id is not None and self.axial_top_id is not None


# ---------------------------------------------------------------------------
# Region remap helper — used by `_translate()` below (kept from earlier
# versions; not currently called anywhere in expand(), retained as-is —
# out of scope for this pass).
# ---------------------------------------------------------------------------

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
                params=        translate_params(obj.type_, obj.params, dx, dy, dz),
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
# Box — the documented exception (see module docstring). Its six planes are
# now built via Phase A's primitive expanders internally (rather than
# hand-rolled Surface() construction), per plan §6 — box.py's schema fields
# are unchanged, only how its surfaces get materialized.
# ---------------------------------------------------------------------------

def _expand_box_surfaces(
    schema: BoxSchema, ctx: Context,
) -> tuple[list[Surface], dict[str, str]]:
    hx = schema.half_x()
    hy = schema.half_y()
    bt = schema.boundary_type

    # Each face is built by calling Phase A's own primitive expander for
    # a PlaneX/Y/Z at the appropriate local offset — Box no longer
    # constructs Surface() objects by hand for these. Order preserved
    # exactly (xlo, xhi, ylo, yhi, bot, top) so fresh_id() minting order,
    # and therefore every downstream surface ID, is unchanged from v4.
    (s_xlo,), _ = primitives.expand_primitive(ctx, PlaneXSchema(x=-hx, boundary_type=bt), "xlo")
    (s_xhi,), _ = primitives.expand_primitive(ctx, PlaneXSchema(x=+hx, boundary_type=bt), "xhi")
    (s_ylo,), _ = primitives.expand_primitive(ctx, PlaneYSchema(y=-hy, boundary_type=bt), "ylo")
    (s_yhi,), _ = primitives.expand_primitive(ctx, PlaneYSchema(y=+hy, boundary_type=bt), "yhi")
    (s_bot,), _ = primitives.expand_primitive(ctx, PlaneZSchema(z=0.0, boundary_type=bt), "bot")
    (s_top,), _ = primitives.expand_primitive(ctx, PlaneZSchema(z=schema.z_size, boundary_type=bt), "top")

    label_map = {
        "xlo": s_xlo.id, "xhi": s_xhi.id,
        "ylo": s_ylo.id, "yhi": s_yhi.id,
        "bot": s_bot.id, "top": s_top.id,
    }
    return [s_xlo, s_xhi, s_ylo, s_yhi, s_bot, s_top], label_map


def _place_box(
    schema: BoxSchema,
    position: tuple[float, float, float],
    ctx: Context,
) -> tuple[list[Surface], dict[str, str]]:
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
            params=        translate_params(s.type_, s.params, dx, dy, dz),
            boundary_type= s.boundary_type,
        ))

    translated_labels = {k: id_map[v] for k, v in label_map.items()}

    ctx.axial_bot_id = translated_labels["bot"]
    ctx.axial_top_id = translated_labels["top"]

    return translated, translated_labels


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

    Step 0 (Phase A/B): expand every Tier-1 primitive, then resolve every
        Tier-2 Cell's region against the complete primitive
        name -> Surface.id map.
    Step 1: Find the Box SinglePlacement (the one documented exception —
        see module docstring), translate its surfaces, register axial
        bounds in ctx.
    Step 2: Dispatch every other template placement (SinglePlacement +
        lattices) through templates.TEMPLATE_REGISTRY.
    Step 3: Build the Box's fill cell using ctx.outermost_surfaces, now
        that every pin has been placed.
    """
    ctx = Context(param_values=param_values or {})
    all_objects: list[Surface | Cell] = []

    # ------------------------------------------------------------------
    # Step 0 — Tier-1 primitives and Tier-2 Cells
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
    # Template/placement pipeline — operating only on whatever wasn't a
    # Tier-1 primitive or Tier-2 Cell above.
    # ------------------------------------------------------------------
    templates_by_name:  dict[str, BaseComponentSchema] = {}
    placements: dict[str, BaseComponentSchema] = {}

    _PLACEMENT_TYPES = (SinglePlacementSchema, SquareLatticeSchema, HexLatticeSchema)
    for name, schema in legacy_schemas.items():
        if isinstance(schema, _PLACEMENT_TYPES):
            placements[name] = schema
        else:
            templates_by_name[name] = schema

    box_schema:            BoxSchema | None        = None
    box_translated_labels: dict[str, str] | None  = None
    box_placement_name:    str | None              = None

    # ------------------------------------------------------------------
    # Step 1 — Box placement first (the documented exception)
    # ------------------------------------------------------------------
    for name, schema in placements.items():
        if not isinstance(schema, SinglePlacementSchema):
            continue
        tpl = templates_by_name.get(schema.template)
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
    # Step 2 — Every other template placement, generic dispatch via
    # templates.TEMPLATE_REGISTRY (Phase C). Adding a new composite
    # template only requires registering it there — nothing here needs
    # to change.
    # ------------------------------------------------------------------
    for name, schema in placements.items():
        if isinstance(schema, SinglePlacementSchema):
            tpl = templates_by_name.get(schema.template)
            if tpl is None:
                raise ValueError(
                    f"SinglePlacement '{name}' references undefined template "
                    f"'{schema.template}'."
                )
            if isinstance(tpl, BoxSchema):
                continue  # already handled in Step 1

            if not templates.is_composite_template(tpl):
                raise TypeError(
                    f"SinglePlacement '{name}' references template of type "
                    f"'{type(tpl).__name__}' which has no placement handler. "
                    f"Register it in templates.TEMPLATE_REGISTRY."
                )
            surfaces, cells = templates.expand_template(ctx, tpl, name, schema.position())
            all_objects.extend(surfaces)
            all_objects.extend(cells)

        elif isinstance(schema, (SquareLatticeSchema, HexLatticeSchema)):
            tpl = templates_by_name.get(schema.template)
            if tpl is None:
                lattice_kind = type(schema).__name__
                raise ValueError(
                    f"{lattice_kind} '{name}' references undefined template "
                    f"'{schema.template}'."
                )
            if not templates.is_composite_template(tpl):
                lattice_kind = type(schema).__name__
                raise TypeError(
                    f"{lattice_kind} '{name}': template type "
                    f"'{type(tpl).__name__}' not supported in lattices. "
                    f"Register it in templates.TEMPLATE_REGISTRY."
                )
            for i, pos in enumerate(schema.pin_positions()):
                surfaces, cells = templates.expand_template(ctx, tpl, f"{name}_{i}", pos)
                all_objects.extend(surfaces)
                all_objects.extend(cells)

    # ------------------------------------------------------------------
    # Step 3 — Box's fill cell, now that every pin has been placed and
    # ctx.outermost_surfaces is complete.
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
