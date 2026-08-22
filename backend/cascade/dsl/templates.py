"""Composite template expansion — Phase C of geometry-restructuring-plan.md.

A composite ("macro") template describes a multi-material object: one
placement of it expands into MULTIPLE Tier-1-shaped surfaces AND MULTIPLE
Tier-2 cells (one cell per material layer) — e.g. FuelPin's pellet/gap/
clad layers.

FuelPin axial bounds: ideally shared from a placed Box via
ctx.axial_bot_id / ctx.axial_top_id. When no Box is present yet (common
while editing), we synthesize local z-planes from pellet_height so the
editor CSG path keeps working, and record a warning on ctx.warnings.
"""

from __future__ import annotations

from typing import Protocol

from ..domain.geometry import (
    BoundaryType, Cell, Inside, Intersection, Outside, Surface, SurfaceType,
)
from .primitives import ExpansionContext, translate_params
from .schema.base import BaseComponentSchema
from .schema.fuel_pin import FuelPinSchema


class CompositeTemplateExpander(Protocol):
    def expand_template(
        self,
        ctx: ExpansionContext,
        schema: BaseComponentSchema,
        name: str,
        position: tuple[float, float, float],
    ) -> tuple[list[Surface], list[Cell]]:
        ...


def _expand_fuel_pin_radial(
    schema: FuelPinSchema,
    ctx: ExpansionContext,
) -> tuple[list[Surface], str | None]:
    """Radial (cylinder) surfaces only, at the origin — NO axial planes."""
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


def _warn(ctx: ExpansionContext, message: str) -> None:
    """Append a soft warning if the expansion context supports it."""
    warnings = getattr(ctx, "warnings", None)
    if isinstance(warnings, list) and message not in warnings:
        warnings.append(message)


class _FuelPinTemplateExpander:
    """FuelPin needs axial (z) planes. Prefer Box-shared bounds from ctx;
    otherwise synthesize local planes from pellet_height so the editor
    does not hard-fail (warning only)."""

    def expand_template(
        self,
        ctx: ExpansionContext,
        schema: FuelPinSchema,
        name: str,
        position: tuple[float, float, float],
    ) -> tuple[list[Surface], list[Cell]]:
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
                outermost_list = getattr(ctx, "outermost_surfaces", None)
                if isinstance(outermost_list, list):
                    outermost_list.append(new_outermost)

        # Axial bounds: Box-shared preferred; local fallback for editor UX
        has_axial = getattr(ctx, "has_axial_bounds", None)
        use_shared = callable(has_axial) and has_axial()
        extra_surfaces: list[Surface] = []

        if use_shared:
            bot_id = ctx.axial_bot_id  # type: ignore[attr-defined]
            top_id = ctx.axial_top_id  # type: ignore[attr-defined]
        else:
            # Local z-planes from pellet_height, shifted by placement z
            bot = Surface(
                id=ctx.fresh_id("s"),
                type_=SurfaceType.PLANE_Z,
                params={"z": float(dz)},
                boundary_type=BoundaryType.NONE,
            )
            top = Surface(
                id=ctx.fresh_id("s"),
                type_=SurfaceType.PLANE_Z,
                params={"z": float(dz) + float(schema.pellet_height)},
                boundary_type=BoundaryType.NONE,
            )
            extra_surfaces.extend([bot, top])
            bot_id, top_id = bot.id, top.id
            _warn(
                ctx,
                "FuelPin placed without a Box for shared axial bounds; "
                "using each pin's pellet_height for local z-planes (preview only). "
                "Add a Box via SinglePlacement for production geometry.",
            )

        cells = _build_fuel_pin_cells(
            schema=            schema,
            layer_surface_ids= [id_map[s.id] for s in radial_surfaces],
            bot_id=            bot_id,
            top_id=            top_id,
            ctx=               ctx,
            cell_name_prefix=  name,
        )

        return translated_surfaces + extra_surfaces, cells


TEMPLATE_REGISTRY: dict[type[BaseComponentSchema], CompositeTemplateExpander] = {
    FuelPinSchema: _FuelPinTemplateExpander(),
}


def is_composite_template(schema: BaseComponentSchema) -> bool:
    return type(schema) in TEMPLATE_REGISTRY


def expand_template(
    ctx: ExpansionContext,
    schema: BaseComponentSchema,
    name: str,
    position: tuple[float, float, float],
) -> tuple[list[Surface], list[Cell]]:
    expander = TEMPLATE_REGISTRY.get(type(schema))
    if expander is None:
        raise TypeError(
            f"'{name}' (type {type(schema).__name__}) is not a registered "
            f"composite template. Registered types: "
            f"{[t.__name__ for t in TEMPLATE_REGISTRY]}."
        )
    return expander.expand_template(ctx, schema, name, position)
