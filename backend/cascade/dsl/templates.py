"""Composite template expansion — Phase C of geometry-restructuring-plan.md.

A composite ("macro") template describes a multi-material object: one
placement of it expands into MULTIPLE Tier-1-shaped surfaces AND MULTIPLE
Tier-2 cells (one cell per material layer) — e.g. FuelPin's pellet/gap/
clad layers. This is different from a Tier-1 primitive (dsl/primitives.py),
which is always exactly one surface and never carries a material itself.
It's also different from a Tier-2 Cell (dsl/schema/cell.py), which is a
single region/material pair authored directly by the user — a composite
template is a *generator* of several such cells from a small set of
convenience parameters (pellet_radius, gap_thickness, ...).

This module owns:
    - the CompositeTemplateExpander protocol (plan §3.3)
    - TEMPLATE_REGISTRY, mapping schema class -> CompositeTemplateExpander
      instance (plan §3.4)
    - the FuelPin implementation, moved here near-verbatim from the
      pre-Phase-C expander.py (_expand_fuel_pin_radial/_build_fuel_pin_cells/
      _place_fuel_pin) — geometry output is byte-for-byte unchanged; only
      the code's address changed.

Deviation from the plan's literal protocol signature (documented, not an
oversight): §3.3's sketch omits a `schema` parameter — the same gap Phase A's
PrimitiveExpander sketch had (see primitives.py's docstring), and for the
same reason: without the specific schema instance, an expander has no way
to read pellet_radius/gap_thickness/etc. `schema` is added here exactly as
it was added to PrimitiveExpander in Phase A.

Box is deliberately NOT registered here — see "The Box axial-sharing
problem" in geometry-restructuring-plan.md §6. Box's fill cell must
exclude every fuel pin's outermost surface, but pins are placed AFTER the
Box (so pins can borrow the Box's z-planes as their own axial bounds) —
the fill cell literally cannot be computed until every pin placement has
run, which is incompatible with this protocol's single
"return everything for this instance, right now" call shape. Per the
plan's own recommendation (option a), this stays one narrow,
explicitly-named exception in expander.py's dispatch loop (Box is expanded
first via `_place_box`, and its fill cell is built last via
`_build_fill_cell`, once `ctx.outermost_surfaces` is complete) rather than
forcing every future template through a more complex two-phase protocol
for the sake of accommodating one user of it.
"""

from __future__ import annotations

from typing import Protocol

from ..domain.geometry import Cell, Inside, Intersection, Outside, Surface, SurfaceType
from .primitives import ExpansionContext, translate_params
from .schema.base import BaseComponentSchema
from .schema.fuel_pin import FuelPinSchema


class CompositeTemplateExpander(Protocol):
    """Implemented once per composite ("macro") template schema type
    (geometry-restructuring-plan.md §3.3)."""

    def expand_template(
        self,
        ctx: ExpansionContext,
        schema: BaseComponentSchema,
        name: str,
        position: tuple[float, float, float],
    ) -> tuple[list[Surface], list[Cell]]:
        """Return ALL surfaces and ALL cells (one per material layer) for
        ONE instance of this template at `position`.

        Used for both SinglePlacement (called once) and lattice instancing
        (called once per pin position) — the caller is responsible for
        computing `name` appropriately for each case (a bare placement
        name for SinglePlacement, `f"{name}_{i}"` per pin for a lattice),
        exactly as before this refactor.

        A composite template is never used as a Tier-2 region operand —
        unlike a Tier-1 primitive, it has no single Surface.id a Cell could
        reference; that's why FuelPin cannot appear as a Union/Subtraction/
        Intersection operand (dsl/schema/boolean.py) and expander.py raises
        a clear error if it's referenced that way.
        """
        ...


# ---------------------------------------------------------------------------
# FuelPin
# ---------------------------------------------------------------------------

def _expand_fuel_pin_radial(
    schema: FuelPinSchema,
    ctx: ExpansionContext,
) -> tuple[list[Surface], str | None]:
    """Radial (cylinder) surfaces only, at the origin — NO axial planes;
    axial bounds are contributed solely by the placed Box (see module
    docstring and the v3 note this preserves from pre-Phase-C expander.py)."""
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
    ctx: ExpansionContext,
    cell_name_prefix: str,
) -> list[Cell]:
    cells: list[Cell] = []
    axial = [Outside(bot_id), Inside(top_id)]

    for i, (_, mat_id) in enumerate(schema.radial_layers()):
        outer_id = layer_surface_ids[i]
        radial = (
            [Inside(outer_id)]
            if i == 0
            else [Outside(layer_surface_ids[i - 1]), Inside(outer_id)]
        )
        cells.append(Cell(
            id=          ctx.fresh_id("c"),
            region=      Intersection(radial + axial),
            material_id= mat_id,
            name=        f"{cell_name_prefix}_layer{i}",
        ))
    return cells


class _FuelPinTemplateExpander:
    """FuelPin needs the placement Box's axial (z) planes, shared via
    `ctx.axial_bot_id`/`ctx.axial_top_id` on the expander's Context. This
    is the one piece of cross-template state any CompositeTemplateExpander
    is allowed to *read*; only the Box exception (expander.py) is allowed
    to *populate* it — see module docstring."""

    def expand_template(
        self,
        ctx: ExpansionContext,
        schema: FuelPinSchema,
        name: str,
        position: tuple[float, float, float],
    ) -> tuple[list[Surface], list[Cell]]:
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
                params=        translate_params(s.type_, s.params, dx, dy, 0.0),
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
            cell_name_prefix=  name,
        )

        return translated_surfaces, cells


# ---------------------------------------------------------------------------
# Registry — schema class -> expander instance (plan §3.4). Box is
# deliberately absent — see module docstring.
# ---------------------------------------------------------------------------

TEMPLATE_REGISTRY: dict[type[BaseComponentSchema], CompositeTemplateExpander] = {
    FuelPinSchema: _FuelPinTemplateExpander(),
}


def is_composite_template(schema: BaseComponentSchema) -> bool:
    """True if `schema`'s exact type is a registered composite template."""
    return type(schema) in TEMPLATE_REGISTRY


def expand_template(
    ctx: ExpansionContext,
    schema: BaseComponentSchema,
    name: str,
    position: tuple[float, float, float],
) -> tuple[list[Surface], list[Cell]]:
    """Dispatch to the registered expander for `schema`'s type.

    Raises:
        TypeError: if schema's type isn't registered (including Box,
        which is intentionally never registered here — see module
        docstring; callers must special-case Box before reaching this).
    """
    expander = TEMPLATE_REGISTRY.get(type(schema))
    if expander is None:
        raise TypeError(
            f"'{name}' (type {type(schema).__name__}) is not a registered "
            f"composite template. Registered types: "
            f"{[t.__name__ for t in TEMPLATE_REGISTRY]}."
        )
    return expander.expand_template(ctx, schema, name, position)
