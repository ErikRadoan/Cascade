"""Geometry expander: converts validated schema objects into CascadeGeometry."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from ..domain.geometry import (
    BoundaryType,
    CascadeGeometry,
    Cell,
    Inside,
    Intersection,
    Outside,
    Surface,
    SurfaceType,
)
from .schema.bounding_box import BoundingBoxSchema
from .schema.fuel_pin import FuelPinSchema


# ---------------------------------------------------------------------------
# Resolution context
# ---------------------------------------------------------------------------

@dataclass
class Context:
    """State shared across all expanders within one geometry expansion.

    Attributes:
        param_values:
            Sweep parameter substitutions — stored on the final
            CascadeGeometry for result tracking.

        outermost_surfaces:
            IDs of each inner component's outermost radial surface.
            Fuel pin expanders append the cladding cylinder ID here.
            BoundingBox reads this to build the fill-material cell region
            ("inside box AND outside all inner geometry").

        axial_bounds:
            The bottom and top bounding surfaces for the problem, if already
            created by a BoundingBox expander. Structure:
                {"bot": Surface, "top": Surface}
            When set, inner components (fuel pins) reuse these surfaces
            as their axial caps instead of creating new duplicate planes.
            This avoids two separate surfaces at the same Z coordinate,
            one with boundary_type and one without — which makes OpenMC
            complain that some boundary surfaces have no condition applied.

        _counter:
            Monotonically increasing integer for ID generation.
    """
    param_values:       dict[str, float]           = field(default_factory=dict)
    outermost_surfaces: list[str]                  = field(default_factory=list)
    axial_bounds:       dict[str, Surface] | None  = field(default=None)
    _counter:           int                        = field(default=0, init=False, repr=False)

    def fresh_id(self, prefix: str = "s") -> str:
        self._counter += 1
        return f"{prefix}{self._counter}"


# ---------------------------------------------------------------------------
# BoundingBox expander  (must run FIRST — sets ctx.axial_bounds)
# ---------------------------------------------------------------------------

def _expand_bounding_box(
    schema: BoundingBoxSchema,
    ctx: Context,
) -> list[Surface | Cell]:
    """Expand BoundingBoxSchema into six boundary surfaces and a fill cell.

    Must be called BEFORE fuel pin expanders so that ctx.axial_bounds is
    set and inner components can reuse the z-planes rather than creating
    duplicates without boundary conditions.

    The fill cell is added AFTER all inner geometry is known, so this
    function splits its work: surfaces are returned immediately, but the
    fill cell is deferred and appended at the end of expand().
    To keep the API simple we return all six surfaces here and store a
    reference so expand() can build the fill cell after all components run.
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
    s_zlo = Surface(id=ctx.fresh_id("s"), type_=SurfaceType.PLANE_Z,
                    params={"z": schema.z_min}, boundary_type=bt)
    s_zhi = Surface(id=ctx.fresh_id("s"), type_=SurfaceType.PLANE_Z,
                    params={"z": schema.z_max}, boundary_type=bt)

    # Store axial planes in context so fuel pin expanders reuse them
    ctx.axial_bounds = {"bot": s_zlo, "top": s_zhi}

    # Lateral planes stored for fill-cell construction in expand()
    ctx._lateral_surfaces = {          # type: ignore[attr-defined]
        "xlo": s_xlo, "xhi": s_xhi,
        "ylo": s_ylo, "yhi": s_yhi,
        "zlo": s_zlo, "zhi": s_zhi,
    }
    ctx._fill_material = schema.material   # type: ignore[attr-defined]

    return [s_xlo, s_xhi, s_ylo, s_yhi, s_zlo, s_zhi]


# ---------------------------------------------------------------------------
# FuelPin expander  (must run AFTER BoundingBox sets ctx.axial_bounds)
# ---------------------------------------------------------------------------

def _expand_fuel_pin(
    schema: FuelPinSchema,
    ctx: Context,
    z_offset: float = 0.0,
) -> list[Surface | Cell]:
    """Expand a FuelPinSchema into surfaces and cells.

    If ctx.axial_bounds is set (BoundingBox was expanded first), the fuel
    pin reuses those z-planes as its axial caps. This ensures there is only
    ONE z-plane surface at each axial boundary — the one that already carries
    the correct boundary_type — rather than a duplicate without it.

    If no bounding box has been defined yet, the fuel pin creates its own
    z-planes (useful for standalone testing without a bounding box).

    Registers the outermost cylinder ID in ctx.outermost_surfaces for the
    fill cell construction.
    """
    surfaces: list[Surface] = []
    cells:    list[Cell]    = []

    # --- Axial planes: reuse box planes if available, else create new ones ---
    if ctx.axial_bounds is not None:
        s_bot = ctx.axial_bounds["bot"]
        s_top = ctx.axial_bounds["top"]
        # Don't add them to surfaces — they're already in the geometry
        # from the BoundingBox expansion.
    else:
        s_bot = Surface(
            id=ctx.fresh_id("s"),
            type_=SurfaceType.PLANE_Z,
            params={"z": z_offset},
        )
        s_top = Surface(
            id=ctx.fresh_id("s"),
            type_=SurfaceType.PLANE_Z,
            params={"z": z_offset + schema.pellet_height},
        )
        surfaces.extend([s_bot, s_top])

    # --- Radial cylinder surfaces ---
    layer_surfaces: list[Surface] = []
    for outer_r, _ in schema.radial_layers():
        s = Surface(
            id=ctx.fresh_id("s"),
            type_=SurfaceType.CYLINDER_Z,
            params={"r": outer_r},
        )
        surfaces.append(s)
        layer_surfaces.append(s)

    # Register outermost surface for fill cell
    if layer_surfaces:
        ctx.outermost_surfaces.append(layer_surfaces[-1].id)

    # --- One cell per radial layer ---
    axial_region = [Outside(s_bot.id), Inside(s_top.id)]

    for i, (_, material_id) in enumerate(schema.radial_layers()):
        outer_surf = layer_surfaces[i]

        if i == 0:
            radial_region = [Inside(outer_surf.id)]
        else:
            inner_surf = layer_surfaces[i - 1]
            radial_region = [Outside(inner_surf.id), Inside(outer_surf.id)]

        cell = Cell(
            id=ctx.fresh_id("c"),
            region=Intersection(radial_region + axial_region),
            material_id=material_id,
            name=f"{material_id}_layer_{i}",
        )
        cells.append(cell)

    return surfaces + cells


# ---------------------------------------------------------------------------
# Dispatch table
# ---------------------------------------------------------------------------

_EXPANDERS = {
    BoundingBoxSchema: _expand_bounding_box,   # NOTE: box first in this dict
    FuelPinSchema:     _expand_fuel_pin,
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def expand(
    schemas: dict[str, object],
    param_values: dict[str, float] | None = None,
    geom_name: str = "cascade_geometry",
) -> CascadeGeometry:
    """Expand validated schema objects into a CascadeGeometry.

    Expansion order:
        BoundingBox components are always expanded first, regardless of their
        position in the YAML, so that ctx.axial_bounds is available when
        fuel pins and other inner geometry are processed.

        All other components are expanded in YAML declaration order.

    After all components are expanded, the fill cell for the BoundingBox
    is constructed (it needs ctx.outermost_surfaces to be complete first).
    """
    ctx = Context(param_values=param_values or {})
    all_objects: list[Surface | Cell] = []

    # --- Pass 1: BoundingBox first (sets ctx.axial_bounds) ---
    for component_name, schema in schemas.items():
        if isinstance(schema, BoundingBoxSchema):
            expanded = _expand_bounding_box(schema, ctx)
            all_objects.extend(expanded)

    # --- Pass 2: everything else ---
    for component_name, schema in schemas.items():
        if isinstance(schema, BoundingBoxSchema):
            continue   # already handled

        expander_fn = _EXPANDERS.get(type(schema))
        if expander_fn is None:
            raise TypeError(
                f"No expander registered for schema type '{type(schema).__name__}' "
                f"(component '{component_name}'). "
                f"Add it to _EXPANDERS in expander.py."
            )

        expanded = expander_fn(schema, ctx)
        all_objects.extend(expanded)

    # --- Pass 3: build fill cell now that outermost_surfaces is complete ---
    if hasattr(ctx, "_lateral_surfaces"):
        lat = ctx._lateral_surfaces        # type: ignore[attr-defined]
        fill_mat = ctx._fill_material      # type: ignore[attr-defined]

        box_interior = [
            Outside(lat["xlo"].id),
            Inside(lat["xhi"].id),
            Outside(lat["ylo"].id),
            Inside(lat["yhi"].id),
            Outside(lat["zlo"].id),
            Inside(lat["zhi"].id),
        ]
        outer_exclusions = [Outside(sid) for sid in ctx.outermost_surfaces]

        fill_cell = Cell(
            id=ctx.fresh_id("c"),
            region=Intersection(box_interior + outer_exclusions),
            material_id=fill_mat,
            name=f"fill_{fill_mat}",
        )
        all_objects.append(fill_cell)

    surfaces = [obj for obj in all_objects if isinstance(obj, Surface)]
    cells    = [obj for obj in all_objects if isinstance(obj, Cell)]

    return CascadeGeometry(
        id=str(uuid.uuid4()),
        name=geom_name,
        surfaces=surfaces,
        cells=cells,
        param_values=ctx.param_values,
    )