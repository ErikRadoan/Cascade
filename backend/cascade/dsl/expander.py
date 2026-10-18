"""Geometry expander: turns validated DSL schemas into a ``CascadeGeometry``.

A YAML document is a flat mapping of named components. They fall into four
roles, and the expander treats each one differently:

* **Primitives** (Tier 1): Sphere, PlaneX/Y/Z, CylinderX/Y/Z, ConeZ, Torus.
  Each is exactly one surface at its own authored position.
* **Cells** (Tier 2): a material plus a region expression that refers to
  primitives by name.
* **Templates**: reusable shapes that only become geometry once placed. These
  are Box, FuelPin and the Union / Subtraction / Intersection composites.
  A primitive that has a ``material`` can also be placed as a solid.
* **Placements**: SinglePlacement, SquareLattice, HexLattice and
  BooleanPlacement. They instantiate templates into the scene.

``expand()`` runs these steps in order:

1. Index the document by role.
2. Expand every primitive, then resolve every Tier-2 cell against them.
3. Place the "universe" Boxes (the outer boxes) first. The first one
   publishes its axial planes so fuel pins share a common floor.
4. Place everything else: single placements, boolean placements, lattices.
5. Add the fill (moderator) cell of each universe Box. It has to exclude every
   pin placed in step 4, so it can only be built last.

Surface, cell and universe ids come from ``Context.fresh_id`` and therefore
follow creation order. Reordering steps never changes the geometry, but it
renumbers ids, which shows up in snapshot tests and diffs.
"""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass, field

from ..domain.geometry import (
    BoundaryType,
    CascadeGeometry,
    Cell,
    Complement,
    Inside,
    Intersection,
    LatticeInstance,
    Outside,
    Region,
    Surface,
    SurfaceType,
    Union,
    region_from_yaml_dict,
)
from . import primitives, templates
from .primitives import translate_params
from .schema.base import BaseComponentSchema
from .schema.boolean import (
    BooleanPlacementSchema,
    IntersectionSchema,
    SubtractionSchema,
    UnionSchema,
)
from .schema.box import BoxSchema
from .schema.cell import CellSchema
from .schema.fuel_pin import FuelPinSchema
from .schema.lattice import HexLatticeSchema, SquareLatticeSchema
from .schema.single_placement import SinglePlacementSchema
from .schema.sphere import SphereSchema
from .schema.torus import TorusSchema

__all__ = ["Context", "expand"]

Vec3 = tuple[float, float, float]
SceneObject = Surface | Cell

_BOOLEAN_TEMPLATE_TYPES = (UnionSchema, SubtractionSchema, IntersectionSchema)
_LATTICE_TYPES = (SquareLatticeSchema, HexLatticeSchema)
_PLACEMENT_TYPES = (SinglePlacementSchema, BooleanPlacementSchema, *_LATTICE_TYPES)

_NO_OFFSET: Vec3 = (0.0, 0.0, 0.0)


# ---------------------------------------------------------------------------
# Expansion state and schema index
# ---------------------------------------------------------------------------

@dataclass
class Context:
    """Mutable state shared by every step of one expansion run."""

    param_values: dict[str, float] = field(default_factory=dict)

    # Axial planes (surface ids) of the first universe Box. Fuel pins reuse
    # the bottom plane so every pin in a box starts at the same floor.
    axial_bot_id: str | None = None
    axial_top_id: str | None = None

    # One entry per fuel pin placed so far: (outer radial surface, bottom
    # plane, top plane). The universe Box's fill cell uses these to cut out
    # exactly the volume each pin occupies.
    outermost_pin_extents: list[tuple[str, str, str]] = field(default_factory=list)
    # Outer radial surfaces on their own. Only consulted for a radial surface
    # that was registered without an extent.
    outermost_surfaces: list[str] = field(default_factory=list)

    # Tier-1 primitive name -> its surface id, so Tier-2 cells can refer to
    # primitives by name.
    primitive_ids: dict[str, str] = field(default_factory=dict)

    lattice_instances: list[LatticeInstance] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    _counter: int = field(default=0, init=False, repr=False)

    def fresh_id(self, prefix: str = "s") -> str:
        """Return a new unique id such as ``s12`` (surface) or ``c3`` (cell)."""
        self._counter += 1
        return f"{prefix}{self._counter}"

    def has_axial_bounds(self) -> bool:
        return self.axial_bot_id is not None and self.axial_top_id is not None


@dataclass
class _SchemaIndex:
    """The document's components grouped by role (see the module docstring)."""

    schemas: dict[str, BaseComponentSchema]
    primitives: dict[str, BaseComponentSchema]
    cells: dict[str, CellSchema]
    # Everything that can be named by a placement: Box, FuelPin, the boolean
    # composites, and the primitives (which are placeable when they have a
    # material).
    templates: dict[str, BaseComponentSchema]
    placements: dict[str, BaseComponentSchema]
    lattices: dict[str, SquareLatticeSchema | HexLatticeSchema]
    # Lattices that are the template of another lattice. They are expanded as
    # part of the outer lattice, never on their own.
    nested_lattice_templates: set[str]


def _index_schemas(schemas: dict[str, BaseComponentSchema]) -> _SchemaIndex:
    primitive_schemas: dict[str, BaseComponentSchema] = {}
    cell_schemas: dict[str, CellSchema] = {}
    template_schemas: dict[str, BaseComponentSchema] = {}
    placement_schemas: dict[str, BaseComponentSchema] = {}

    for name, schema in schemas.items():
        if primitives.is_primitive(schema):
            primitive_schemas[name] = schema
        elif isinstance(schema, CellSchema):
            cell_schemas[name] = schema
        elif isinstance(schema, _PLACEMENT_TYPES):
            placement_schemas[name] = schema
        else:
            template_schemas[name] = schema

    # A primitive is both a named surface for Tier-2 cells and, when it has a
    # material, a template a SinglePlacement can instantiate.
    template_schemas.update(primitive_schemas)

    lattice_schemas = {
        name: schema
        for name, schema in placement_schemas.items()
        if isinstance(schema, _LATTICE_TYPES)
    }
    nested_lattice_templates = {
        schema.template
        for schema in lattice_schemas.values()
        if schema.template in lattice_schemas
    }

    return _SchemaIndex(
        schemas=schemas,
        primitives=primitive_schemas,
        cells=cell_schemas,
        templates=template_schemas,
        placements=placement_schemas,
        lattices=lattice_schemas,
        nested_lattice_templates=nested_lattice_templates,
    )


# ---------------------------------------------------------------------------
# Low-level geometry helpers
# ---------------------------------------------------------------------------

def _only_surfaces(objects: list[SceneObject]) -> list[Surface]:
    return [obj for obj in objects if isinstance(obj, Surface)]


def _only_cells(objects: list[SceneObject]) -> list[Cell]:
    return [obj for obj in objects if isinstance(obj, Cell)]


def _without_boundary(surfaces: list[Surface]) -> list[Surface]:
    """Copy surfaces with their boundary condition cleared.

    Only the outermost surfaces of a model may be reflective or vacuum. A shape
    used as an operand or placed as an interior solid must not inherit its
    schema's boundary_type, or OpenMC would reflect particles off what is
    really a transparent interior wall.
    """
    return [
        Surface(id=s.id, type_=s.type_, params=s.params, boundary_type=BoundaryType.NONE)
        for s in surfaces
    ]


def _remap_region(region: Region, id_map: dict[str, str]) -> Region:
    """Rebuild a region tree with surface ids replaced according to ``id_map``."""
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


def _translate(
    objects: list[SceneObject], dx: float, dy: float, dz: float, ctx: Context,
) -> tuple[list[SceneObject], dict[str, str]]:
    """Copy surfaces and cells shifted by (dx, dy, dz), all under fresh ids.

    Cell regions are re-pointed at the copied surfaces. Cells are copied as
    plain material cells (universe and fill attributes are not carried over).
    Returns the copies (surfaces first, then cells) and an old-id -> new-id map
    of the surfaces.
    """
    id_map: dict[str, str] = {}
    new_surfaces: list[Surface] = []
    new_cells: list[Cell] = []

    for obj in objects:
        if isinstance(obj, Surface):
            new_id = ctx.fresh_id("s")
            id_map[obj.id] = new_id
            new_surfaces.append(Surface(
                id=new_id,
                type_=obj.type_,
                params=translate_params(obj.type_, obj.params, dx, dy, dz),
                boundary_type=obj.boundary_type,
            ))

    for obj in objects:
        if isinstance(obj, Cell):
            new_cells.append(Cell(
                id=ctx.fresh_id("c"),
                region=_remap_region(obj.region, id_map),
                material_id=obj.material_id,
                name=obj.name,
            ))

    return new_surfaces + new_cells, id_map


def _combine_regions(op: str, regions: list[Region]) -> Region:
    """Fold regions left to right with a binary boolean op."""
    if not regions:
        raise ValueError("Cannot combine empty region list")
    result = regions[0]
    for region in regions[1:]:
        if op == "union":
            result = Union([result, region])
        elif op == "intersection":
            result = Intersection([result, region])
        elif op == "subtraction":
            result = Intersection([result, Complement(region)])
        else:
            raise ValueError(f"Unknown boolean op '{op}'")
    return result


def _origin_offset(schema: BaseComponentSchema) -> Vec3:
    """A schema's own local x/y/z offset (0 for schemas that have none)."""
    return (
        float(getattr(schema, "x", 0.0) or 0.0),
        float(getattr(schema, "y", 0.0) or 0.0),
        float(getattr(schema, "z", 0.0) or 0.0),
    )


def _operand_names(schema: BaseComponentSchema) -> list[str]:
    """Names of a boolean composite's operands (Union may be n-ary)."""
    if isinstance(schema, UnionSchema):
        return schema.operand_names()
    return [schema.a, schema.b]


# ---------------------------------------------------------------------------
# Rotated placements
#
# Rotation is never baked into surface coefficients. OpenMC can rotate a cell
# fill but not a surface, and a torus (a quartic) cannot be written as a
# rotated general surface at all. Instead the object is built at the origin,
# wrapped in its own universe, and instanced into the parent universe by one
# fill cell that carries OpenMC's `translation` and `rotation`.
#
# The fill cell needs a region in the parent frame to confine where the
# rotated universe appears. A sphere centred on the placement works: it is
# invariant under rotation about its own centre, so a sphere sized to the
# object's bounding radius is exact for any angle and needs no rotation math.
# ---------------------------------------------------------------------------

_UNBOUNDED_ROTATION_MSG = (
    "'{name}' ({type_name}) has unbounded extent (an infinite plane, "
    "cylinder, or cone) and can't be rotated as a standalone placement — "
    "there's no finite bounding volume to confine the rotated local "
    "universe within. Combine it with bounding surfaces in a boolean "
    "composite (so the composite as a whole is finite), or place it "
    "without rotation."
)


def _rotation_of(schema: BaseComponentSchema) -> Vec3:
    return (
        float(getattr(schema, "rx", 0.0) or 0.0),
        float(getattr(schema, "ry", 0.0) or 0.0),
        float(getattr(schema, "rz", 0.0) or 0.0),
    )


def _is_rotated(rotation: Vec3) -> bool:
    return any(angle != 0.0 for angle in rotation)


def _local_bounding_radius(
    schema: BaseComponentSchema, name: str, index: _SchemaIndex,
) -> float:
    """Radius of a sphere around the schema's local origin that encloses it.

    Exact for a Sphere, conservative elsewhere: Box and FuelPin have their
    local origin on a face or end rather than at the centre, so the full height
    is used instead of half. A boolean composite takes its farthest operand.

    Raises ValueError for unbounded shapes (planes, cylinders, cones): a
    rotated placement needs a finite bound.
    """
    if isinstance(schema, BoxSchema):
        return math.sqrt(schema.half_x() ** 2 + schema.half_y() ** 2 + schema.z_size ** 2)

    if isinstance(schema, FuelPinSchema):
        max_radius = max((r for r, _ in schema.radial_layers()), default=0.0)
        return math.sqrt(max_radius ** 2 + schema.pellet_height ** 2)

    if isinstance(schema, SphereSchema):
        return schema.radius

    if isinstance(schema, TorusSchema):
        tube_height = schema.tube_height if schema.tube_height is not None else schema.tube_radius
        return schema.ring_radius + max(schema.tube_radius, tube_height)

    if isinstance(schema, _BOOLEAN_TEMPLATE_TYPES):
        farthest = 0.0
        for operand_name in _operand_names(schema):
            operand = index.schemas.get(operand_name)
            if operand is None:
                continue
            ox, oy, oz = _origin_offset(operand)
            offset = math.sqrt(ox ** 2 + oy ** 2 + oz ** 2)
            farthest = max(farthest, offset + _local_bounding_radius(operand, operand_name, index))
        return farthest

    raise ValueError(_UNBOUNDED_ROTATION_MSG.format(name=name, type_name=type(schema).__name__))


def _fill_instance(
    ctx: Context,
    *,
    name: str,
    parent_universe: str,
    child_universe: str,
    translation: Vec3,
    rotation: Vec3,
    center: Vec3,
    radius: float,
) -> list[SceneObject]:
    """Stamp ``child_universe`` into ``parent_universe`` through a fill cell.

    The fill cell is confined to a sphere (``center``, ``radius``) in the
    parent frame; ``translation`` and ``rotation`` are applied to the child.
    Returns ``[confining sphere, fill cell]``.
    """
    confine = Surface(
        id=ctx.fresh_id("s"),
        type_=SurfaceType.SPHERE,
        params={"x0": center[0], "y0": center[1], "z0": center[2], "r": radius},
    )
    fill_cell = Cell(
        id=ctx.fresh_id("c"),
        region=Inside(confine.id),
        material_id=None,
        name=name,
        universe_id=parent_universe,
        fill=child_universe,
        translation=translation,
        rotation=rotation,
    )
    return [confine, fill_cell]


def _place_rotated(
    local_objects: list[SceneObject],
    position: Vec3,
    rotation: Vec3,
    bounding_radius: float,
    ctx: Context,
    cell_name: str,
) -> list[SceneObject]:
    """Instance origin-local geometry into the root universe, rotated.

    ``local_objects`` are the surfaces and cell(s) of one object, built at the
    origin without any translation or rotation. Their cells are moved into a
    new universe, which a single fill cell then places at ``position`` with
    the given ``rotation``.
    """
    universe_id = ctx.fresh_id("u")
    local_surfaces = _only_surfaces(local_objects)
    scoped_cells = [
        Cell(id=c.id, region=c.region, material_id=c.material_id, name=c.name,
             universe_id=universe_id)
        for c in _only_cells(local_objects)
    ]
    fill = _fill_instance(
        ctx,
        name=cell_name,
        parent_universe="0",
        child_universe=universe_id,
        translation=position,
        rotation=rotation,
        center=position,
        radius=bounding_radius,
    )
    return [*local_surfaces, *scoped_cells, *fill]


# ---------------------------------------------------------------------------
# Boxes
# ---------------------------------------------------------------------------

def _expand_box_surfaces(
    schema: BoxSchema, ctx: Context,
) -> tuple[list[Surface], dict[str, str]]:
    """The six planes of a Box: centred on x/y at the origin, standing on z=0.

    The planes carry the schema's own boundary_type. The returned dict maps
    ``xlo, xhi, ylo, yhi, bot, top`` to surface ids.
    """
    half_x = schema.half_x()
    half_y = schema.half_y()

    def plane(plane_type: SurfaceType, axis: str, position: float) -> Surface:
        return Surface(
            id=ctx.fresh_id("s"),
            type_=plane_type,
            params={axis: position},
            boundary_type=schema.boundary_type,
        )

    x_lo = plane(SurfaceType.PLANE_X, "x", -half_x)
    x_hi = plane(SurfaceType.PLANE_X, "x", +half_x)
    y_lo = plane(SurfaceType.PLANE_Y, "y", -half_y)
    y_hi = plane(SurfaceType.PLANE_Y, "y", +half_y)
    bottom = plane(SurfaceType.PLANE_Z, "z", 0.0)
    top = plane(SurfaceType.PLANE_Z, "z", schema.z_size)

    labels = {
        "xlo": x_lo.id, "xhi": x_hi.id,
        "ylo": y_lo.id, "yhi": y_hi.id,
        "bot": bottom.id, "top": top.id,
    }
    return [x_lo, x_hi, y_lo, y_hi, bottom, top], labels


def _box_interior_terms(labels: dict[str, str]) -> list[Region]:
    """Half-space conditions whose intersection is the box interior."""
    return [
        Outside(labels["xlo"]), Inside(labels["xhi"]),
        Outside(labels["ylo"]), Inside(labels["yhi"]),
        Outside(labels["bot"]), Inside(labels["top"]),
    ]


def _box_solid_at_origin(
    schema: BoxSchema, ctx: Context, *, clear_boundary: bool,
) -> tuple[list[Surface], Region]:
    """A Box as a solid region at the origin: its surfaces and interior region."""
    surfaces, labels = _expand_box_surfaces(schema, ctx)
    if clear_boundary:
        surfaces = _without_boundary(surfaces)
    return surfaces, Intersection(_box_interior_terms(labels))


def _place_universe_box(
    schema: BoxSchema, position: Vec3, ctx: Context,
) -> tuple[list[Surface], dict[str, str]]:
    """Place an outer Box. Keeps its boundary_type: these are the model's outer faces.

    Returns the translated surfaces and the label -> surface id map.
    """
    surfaces, labels = _expand_box_surfaces(schema, ctx)
    translated, id_map = _translate(surfaces, *position, ctx)
    return _only_surfaces(translated), {key: id_map[sid] for key, sid in labels.items()}


def _build_fill_cell(
    schema: BoxSchema, labels: dict[str, str], ctx: Context, cell_name: str,
) -> Cell:
    """The material that fills a universe Box around everything placed in it.

    The cell is the box interior minus each fuel pin. Excluding only a pin's
    infinite radial cylinder would leave a pin-shaped hole through the box
    ceiling above any pin that is shorter than the box. So the exclusion uses
    the pin's finite volume instead, by De Morgan's law:

        NOT(inside radial AND above floor AND below ceiling)
          = outside radial OR below floor OR above ceiling

    which lets the fill material close in above and below a short pin.
    """
    exclusions: list[Region] = []
    covered_radial_ids: set[str] = set()

    for radial_id, bottom_id, top_id in ctx.outermost_pin_extents:
        covered_radial_ids.add(radial_id)
        exclusions.append(Union([Outside(radial_id), Inside(bottom_id), Outside(top_id)]))

    # Safety net for a radial surface registered without axial extents:
    # exclude it as an infinite cylinder rather than dropping it.
    for radial_id in ctx.outermost_surfaces:
        if radial_id not in covered_radial_ids:
            exclusions.append(Outside(radial_id))

    return Cell(
        id=ctx.fresh_id("c"),
        region=Intersection(_box_interior_terms(labels) + exclusions),
        material_id=schema.material,
        name=cell_name,
    )


# ---------------------------------------------------------------------------
# Boolean composite templates (Union / Subtraction / Intersection)
# ---------------------------------------------------------------------------

def _expand_shape_at_origin(
    schema: BaseComponentSchema,
    name: str,
    index: _SchemaIndex,
    ctx: Context,
    visiting: tuple[str, ...] = (),
) -> tuple[list[Surface], Region]:
    """Build a shape's surfaces and region at its local origin.

    Used for boolean operands and for placing a composite. ``visiting`` is the
    chain of composites being resolved, used to report circular references.
    """
    if isinstance(schema, FuelPinSchema):
        raise TypeError(f"'{name}' is a FuelPin, which cannot be used as a boolean composite operand.")

    if isinstance(schema, BoxSchema):
        surfaces, region = _box_solid_at_origin(schema, ctx, clear_boundary=True)
        offset = _origin_offset(schema)
        if any(offset):
            translated, id_map = _translate(surfaces, *offset, ctx)
            surfaces = _only_surfaces(translated)
            region = _remap_region(region, id_map)
        return surfaces, region

    if primitives.is_primitive(schema):
        # Always make a fresh copy of the primitive's surface rather than
        # reusing the one created in step 1. A composite is translated as a
        # unit by its placement offset; a reused surface would stay at the
        # primitive's authored position and ignore that offset. The step-1
        # surface still exists for Tier-2 cells that name the primitive.
        surfaces, region = primitives.expand_primitive(ctx, schema, name)
        return _without_boundary(surfaces), region

    if isinstance(schema, _BOOLEAN_TEMPLATE_TYPES):
        if name in visiting:
            chain = " -> ".join((*visiting, name))
            raise ValueError(
                "Circular reference detected while resolving boolean composite "
                f"operands: {chain}."
            )
        next_visiting = (*visiting, name)

        composite_surfaces: list[Surface] = []
        operand_regions: list[Region] = []
        for operand_name in _operand_names(schema):
            operand = index.schemas.get(operand_name)
            if operand is None:
                raise ValueError(
                    f"'{name}' operand references undefined template/primitive "
                    f"'{operand_name}'."
                )
            operand_surfaces, operand_region = _expand_shape_at_origin(
                operand, operand_name, index, ctx, next_visiting,
            )
            composite_surfaces.extend(operand_surfaces)
            operand_regions.append(operand_region)

        return composite_surfaces, _combine_operand_regions(schema, operand_regions)

    raise TypeError(
        f"'{name}' (type {type(schema).__name__}) cannot be used as a "
        f"boolean composite operand."
    )


def _combine_operand_regions(schema: BaseComponentSchema, regions: list[Region]) -> Region:
    if isinstance(schema, UnionSchema):
        # Union is n-ary, so it stays flat instead of nesting binary unions.
        return regions[0] if len(regions) == 1 else Union(regions)
    op = "subtraction" if isinstance(schema, SubtractionSchema) else "intersection"
    return _combine_regions(op, regions)


# ---------------------------------------------------------------------------
# SinglePlacement
# ---------------------------------------------------------------------------

def _expand_single_placement_objects(
    schema: SinglePlacementSchema,
    name: str,
    index: _SchemaIndex,
    ctx: Context,
    extra_offset: Vec3 = _NO_OFFSET,
    *,
    inside_boolean: bool = False,
) -> list[SceneObject]:
    """Instantiate one SinglePlacement: its surfaces and cell(s).

    ``extra_offset`` is added to the placement position (a BooleanPlacement
    uses it to shift its children). ``inside_boolean`` marks a placement that
    is a child of a BooleanPlacement.
    """
    tpl = index.templates.get(schema.template)
    if tpl is None:
        raise ValueError(f"SinglePlacement '{name}' references undefined template '{schema.template}'.")

    x, y, z = schema.position()
    position = (x + extra_offset[0], y + extra_offset[1], z + extra_offset[2])
    rotation = _rotation_of(schema)
    rotated = _is_rotated(rotation)

    if rotated and inside_boolean:
        raise NotImplementedError(
            f"SinglePlacement '{name}' is a BooleanPlacement child and can't be rotated yet — "
            f"combining a rotated (fill-cell) child's region with its siblings' "
            f"Union/Subtraction/Intersection isn't supported in this pass. Rotate '{name}' "
            f"as a standalone SinglePlacement instead, or remove its rotation."
        )

    # Multi-material templates (FuelPin) produce their own cells.
    if templates.is_composite_template(tpl):
        if rotated:
            local_surfaces, local_cells = templates.expand_template(
                ctx, tpl, name, _NO_OFFSET, force_local_axial=True,
            )
            radius = _local_bounding_radius(tpl, schema.template, index)
            return _place_rotated([*local_surfaces, *local_cells], position, rotation, radius, ctx, name)
        surfaces, cells = templates.expand_template(ctx, tpl, name, position)
        return [*surfaces, *cells]

    # Everything else is a single solid: build it at the origin, then either
    # translate it into place or, if rotated, wrap it in a local universe.
    local_surfaces, local_region = _build_solid_at_origin(
        schema, name, tpl, index, ctx, inside_boolean=inside_boolean,
    )

    if rotated:
        cell = Cell(id=ctx.fresh_id("c"), region=local_region, material_id=tpl.material, name=name)
        radius = _local_bounding_radius(tpl, schema.template, index)
        return _place_rotated([*local_surfaces, cell], position, rotation, radius, ctx, name)

    translated, id_map = _translate(local_surfaces, *position, ctx)
    cell = Cell(
        id=ctx.fresh_id("c"),
        region=_remap_region(local_region, id_map),
        material_id=tpl.material,
        name=name,
    )
    return [*_only_surfaces(translated), cell]


def _build_solid_at_origin(
    schema: SinglePlacementSchema,
    name: str,
    tpl: BaseComponentSchema,
    index: _SchemaIndex,
    ctx: Context,
    *,
    inside_boolean: bool,
) -> tuple[list[Surface], Region]:
    """Surfaces and region of the solid a SinglePlacement refers to, at the origin."""
    if isinstance(tpl, BoxSchema):
        # A solid Box is interior geometry, so its boundary condition is
        # cleared. NOTE: a Box that is a BooleanPlacement child currently
        # keeps its own boundary_type instead. That looks unintentional (a
        # reflective wall inside the model) but is kept as-is so this
        # refactor changes no output.
        return _box_solid_at_origin(tpl, ctx, clear_boundary=not inside_boolean)

    if primitives.is_primitive(tpl):
        if getattr(tpl, "material", None) is None:
            raise ValueError(
                f"SinglePlacement '{name}' places '{schema.template}' (a "
                f"{type(tpl).__name__}), which has no `material` set. Add "
                f"material: <id> to '{schema.template}' to place it as a "
                f"solid object, or remove this SinglePlacement and "
                f"reference '{schema.template}' from a Tier-2 Cell region "
                f"expression instead."
            )
        return primitives.expand_primitive(ctx, tpl, schema.template)

    if isinstance(tpl, _BOOLEAN_TEMPLATE_TYPES):
        return _expand_shape_at_origin(tpl, schema.template, index, ctx)

    raise TypeError(
        f"SinglePlacement '{name}' references template of type "
        f"'{type(tpl).__name__}' which has no placement handler."
    )


# ---------------------------------------------------------------------------
# BooleanPlacement
# ---------------------------------------------------------------------------

def _place_boolean_placement(
    schema: BooleanPlacementSchema,
    name: str,
    index: _SchemaIndex,
    ctx: Context,
    visiting: tuple[str, ...] = (),
) -> list[SceneObject]:
    """Combine already-placed children (SinglePlacements or nested
    BooleanPlacements) with a boolean op.

    Children are expanded like standalone placements, shifted by this
    placement's x/y/z, and then either kept as separate cells (union) or
    merged into one cell (subtraction, intersection).
    """
    if name in visiting:
        chain = " -> ".join((*visiting, name))
        raise ValueError(f"Circular BooleanPlacement reference: {chain}.")

    children = _expand_boolean_children(schema, name, index, ctx, (*visiting, name))
    material_filter = set(schema.materials) if schema.materials else None

    if schema.op == "union":
        return _union_children(children, name, material_filter)
    return _merge_children(children, schema.op, name, material_filter, ctx)


def _expand_boolean_children(
    schema: BooleanPlacementSchema,
    name: str,
    index: _SchemaIndex,
    ctx: Context,
    visiting: tuple[str, ...],
) -> list[tuple[list[Surface], list[Cell]]]:
    """Expand each child into its (surfaces, cells), offset by the parent."""
    offset = (schema.x, schema.y, schema.z)
    results: list[tuple[list[Surface], list[Cell]]] = []

    for child_name in schema.children:
        child = index.placements.get(child_name)
        if child is None:
            raise ValueError(
                f"BooleanPlacement '{name}' child '{child_name}' is not a defined placement."
            )

        if isinstance(child, BooleanPlacementSchema):
            objects = _place_boolean_placement(child, child_name, index, ctx, visiting)
            if any(offset):
                objects, _ = _translate(objects, *offset, ctx)
        elif isinstance(child, SinglePlacementSchema):
            objects = _expand_single_placement_objects(
                child, child_name, index, ctx, offset, inside_boolean=True,
            )
        elif isinstance(child, _LATTICE_TYPES):
            raise TypeError(
                f"BooleanPlacement '{name}': lattice child '{child_name}' is not supported in v1."
            )
        else:
            raise TypeError(
                f"BooleanPlacement '{name}': child '{child_name}' has unsupported type "
                f"{type(child).__name__}."
            )

        results.append((_only_surfaces(objects), _only_cells(objects)))

    return results


def _keeps_material(cell: Cell, material_filter: set[str] | None) -> bool:
    return material_filter is None or cell.material_id in material_filter


def _union_children(
    children: list[tuple[list[Surface], list[Cell]]],
    name: str,
    material_filter: set[str] | None,
) -> list[SceneObject]:
    """Union keeps every child cell, renamed ``<parent>_<child cell>``."""
    objects: list[SceneObject] = []
    for surfaces, cells in children:
        objects.extend(surfaces)
        for cell in cells:
            if _keeps_material(cell, material_filter):
                objects.append(Cell(
                    id=cell.id,
                    region=cell.region,
                    material_id=cell.material_id,
                    name=name if cell.name is None else f"{name}_{cell.name}",
                ))
    return objects


def _merge_children(
    children: list[tuple[list[Surface], list[Cell]]],
    op: str,
    name: str,
    material_filter: set[str] | None,
    ctx: Context,
) -> list[SceneObject]:
    """Merge the children into one cell using the first kept child's material.

    Each child's kept cells are first joined by union into a single region,
    and those per-child regions are then combined with ``op``.
    """
    surfaces: list[Surface] = []
    child_regions: list[Region] = []
    material: str | None = None

    for child_surfaces, child_cells in children:
        surfaces.extend(child_surfaces)
        kept = [c for c in child_cells if _keeps_material(c, material_filter)]
        if not kept:
            continue
        if material is None:
            material = kept[0].material_id
        region = _union_of_cell_regions(kept)
        if region is not None:
            child_regions.append(region)

    if not child_regions:
        return list(surfaces)

    cell = Cell(
        id=ctx.fresh_id("c"),
        region=_combine_regions(op, child_regions),
        material_id=material,
        name=name,
    )
    return [*surfaces, cell]


def _union_of_cell_regions(cells: list[Cell]) -> Region | None:
    regions = [c.region for c in cells if c.region is not None]
    if not regions:
        return None
    if len(regions) == 1:
        return regions[0]
    return Union(regions)


# ---------------------------------------------------------------------------
# Lattices
# ---------------------------------------------------------------------------

def _expand_lattice(
    schema: SquareLatticeSchema | HexLatticeSchema,
    name: str,
    index: _SchemaIndex,
    ctx: Context,
) -> list[SceneObject]:
    """Expand a lattice. Picks the strategy from the template and rotation."""
    template_name = schema.template

    inner_lattice = index.lattices.get(template_name)
    if inner_lattice is not None:
        return _expand_nested_lattice(schema, inner_lattice, name, index, ctx)

    tpl = index.templates.get(template_name)
    if tpl is None:
        raise ValueError(f"Lattice '{name}' references undefined template '{template_name}'.")
    if not templates.is_composite_template(tpl):
        raise TypeError(f"Lattice '{name}': template type not supported in lattices yet.")

    if schema.is_rotated():
        return _expand_rotated_lattice(schema, tpl, template_name, name, index, ctx)
    return _expand_flat_lattice(tpl, template_name, name, schema.pin_positions(), ctx)


def _relative_positions(positions: list[Vec3]) -> list[Vec3]:
    """Positions relative to the first one."""
    ox, oy, oz = positions[0]
    return [(x - ox, y - oy, z - oz) for x, y, z in positions]


def _expand_flat_lattice(
    tpl: BaseComponentSchema,
    prototype_key: str,
    lattice_name: str,
    positions: list[Vec3],
    ctx: Context,
) -> list[SceneObject]:
    """Expand an unrotated lattice: every pin is a plain translated copy.

    The first pin is recorded as a shared prototype in ``ctx.lattice_instances``
    so the viewport can draw the whole lattice with GPU instancing.
    """
    if not positions:
        return []

    objects: list[SceneObject] = []
    prototype_surfaces: list[Surface] = []
    prototype_cells: list[Cell] = []

    for i, position in enumerate(positions):
        surfaces, cells = templates.expand_template(ctx, tpl, f"{lattice_name}_{i}", position)
        objects.extend(surfaces)
        objects.extend(cells)
        if i == 0:
            prototype_surfaces, prototype_cells = surfaces, cells

    ctx.lattice_instances.append(LatticeInstance(
        lattice_name=lattice_name,
        prototype_key=prototype_key,
        prototype_surfaces=prototype_surfaces,
        prototype_cells=prototype_cells,
        instances=_relative_positions(positions),
        inner_offsets=[],
    ))
    return objects


def _expand_nested_lattice(
    outer: SquareLatticeSchema | HexLatticeSchema,
    inner: SquareLatticeSchema | HexLatticeSchema,
    outer_name: str,
    index: _SchemaIndex,
    ctx: Context,
) -> list[SceneObject]:
    """Expand a lattice whose template is another lattice (an assembly of pins).

    Every pin ends up as a translated copy at ``assembly position + pin
    offset``. The first pin is the shared prototype, with the assembly
    positions as ``instances`` and the pin offsets as ``inner_offsets``.
    """
    inner_name = outer.template
    if outer.is_rotated() or inner.is_rotated():
        raise NotImplementedError(
            f"Nested lattice '{outer_name}' -> '{inner_name}': rotation on nested "
            f"(lattice-of-lattice) assemblies isn't supported yet. Rotate a "
            f"single-level lattice, or remove rotation from one of these two levels."
        )

    pin_tpl = index.templates.get(inner.template)
    if pin_tpl is None:
        raise ValueError(
            f"Nested lattice '{outer_name}' -> '{inner_name}' references undefined "
            f"pin template '{inner.template}'."
        )
    if not templates.is_composite_template(pin_tpl):
        raise TypeError(f"Nested lattice '{outer_name}': inner template not supported.")

    outer_positions = outer.pin_positions()
    inner_positions = inner.pin_positions()
    if not outer_positions or not inner_positions:
        return []

    inner_offsets = _relative_positions(inner_positions)
    objects: list[SceneObject] = []
    prototype_surfaces: list[Surface] = []
    prototype_cells: list[Cell] = []

    for assembly_idx, (ax, ay, az) in enumerate(outer_positions):
        for pin_idx, (px, py, pz) in enumerate(inner_offsets):
            world_position = (ax + px, ay + py, az + pz)
            surfaces, cells = templates.expand_template(
                ctx, pin_tpl, f"{outer_name}_a{assembly_idx}_p{pin_idx}", world_position,
            )
            objects.extend(surfaces)
            objects.extend(cells)
            if assembly_idx == 0 and pin_idx == 0:
                prototype_surfaces, prototype_cells = surfaces, cells

    ctx.lattice_instances.append(LatticeInstance(
        lattice_name=outer_name,
        prototype_key=inner.template,
        prototype_surfaces=prototype_surfaces,
        prototype_cells=prototype_cells,
        instances=_relative_positions(outer_positions),
        inner_offsets=inner_offsets,
    ))
    return objects


def _expand_rotated_lattice(
    schema: SquareLatticeSchema | HexLatticeSchema,
    tpl: BaseComponentSchema,
    prototype_key: str,
    lattice_name: str,
    index: _SchemaIndex,
    ctx: Context,
) -> list[SceneObject]:
    """Expand a rotated lattice as an "assembly" universe of pin universes.

    Every pin becomes a fill cell inside one shared assembly universe. Pins
    with the same own-rotation share a single pin universe, so a lattice with
    a few overridden pins does not create hundreds of duplicate universes. One
    outer fill cell then instances the assembly into the root universe with the
    lattice-level rotation, pivoting about the lattice origin.

    These lattices skip ``ctx.lattice_instances``, so the viewport falls back
    to the flat surface/cell list for them. The OpenMC geometry is exact either
    way; only the GPU-instancing fast path is lost.
    """
    positions = schema.pin_positions()
    if not positions:
        return []

    pin_rotations = schema.pin_rotations()
    origin = schema.origin_point()
    pin_radius = _local_bounding_radius(tpl, prototype_key, index)
    assembly_universe = ctx.fresh_id("u")

    objects: list[SceneObject] = []
    pin_universe_by_rotation: dict[Vec3, str] = {}
    assembly_radius = 0.0

    for i, position in enumerate(positions):
        local_position = (position[0] - origin[0], position[1] - origin[1], position[2] - origin[2])
        own_rotation = pin_rotations[i]

        pin_universe = pin_universe_by_rotation.get(own_rotation)
        if pin_universe is None:
            prototype_number = len(pin_universe_by_rotation)
            local_surfaces, local_cells = templates.expand_template(
                ctx, tpl, f"{lattice_name}_proto{prototype_number}", _NO_OFFSET, force_local_axial=True,
            )
            pin_universe = ctx.fresh_id("u")
            objects.extend(local_surfaces)
            objects.extend(
                Cell(id=c.id, region=c.region, material_id=c.material_id, name=c.name,
                     universe_id=pin_universe)
                for c in local_cells
            )
            pin_universe_by_rotation[own_rotation] = pin_universe

        objects.extend(_fill_instance(
            ctx,
            name=f"{lattice_name}_{i}",
            parent_universe=assembly_universe,
            child_universe=pin_universe,
            translation=local_position,
            rotation=own_rotation,
            center=local_position,
            radius=pin_radius,
        ))
        distance = math.sqrt(local_position[0] ** 2 + local_position[1] ** 2 + local_position[2] ** 2)
        assembly_radius = max(assembly_radius, distance + pin_radius)

    objects.extend(_fill_instance(
        ctx,
        name=lattice_name,
        parent_universe="0",
        child_universe=assembly_universe,
        translation=origin,
        rotation=schema.assembly_rotation(),
        center=_NO_OFFSET,
        radius=assembly_radius,
    ))
    return objects


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def expand(
    schemas: dict[str, BaseComponentSchema],
    param_values: dict[str, float] | None = None,
    geom_name: str = "cascade_geometry",
) -> CascadeGeometry:
    """Expand validated DSL schemas into a flat ``CascadeGeometry``.

    ``param_values`` is carried through to the result unchanged (it records
    which sweep values produced this geometry). See the module docstring for
    the order in which components are expanded.
    """
    ctx = Context(param_values=param_values or {})
    index = _index_schemas(schemas)
    objects: list[SceneObject] = []

    objects.extend(_expand_primitives(index, ctx))
    objects.extend(_expand_cells(index, ctx))

    boolean_children = _find_boolean_children(index.placements)

    universe_surfaces, universe_boxes = _place_universe_boxes(index, boolean_children, ctx)
    objects.extend(universe_surfaces)
    objects.extend(_place_remaining(index, boolean_children, ctx))

    for name, box_schema, labels in universe_boxes:
        fill_name = name or f"fill_{box_schema.material}"
        objects.append(_build_fill_cell(box_schema, labels, ctx, cell_name=fill_name))

    return CascadeGeometry(
        id=str(uuid.uuid4()),
        name=geom_name,
        surfaces=_only_surfaces(objects),
        cells=_only_cells(objects),
        param_values=ctx.param_values,
        lattice_instances=ctx.lattice_instances,
        warnings=ctx.warnings,
    )


def _expand_primitives(index: _SchemaIndex, ctx: Context) -> list[Surface]:
    """Create every primitive's surface and remember it by name.

    Every primitive is expanded, whether or not anything places it, because a
    Tier-2 cell may refer to it by name from anywhere in the document.
    """
    surfaces: list[Surface] = []
    for name, schema in index.primitives.items():
        primitive_surfaces, _region = primitives.expand_primitive(ctx, schema, name)
        surfaces.extend(primitive_surfaces)
        ctx.primitive_ids[name] = primitive_surfaces[-1].id
    return surfaces


def _expand_cells(index: _SchemaIndex, ctx: Context) -> list[Cell]:
    """Resolve Tier-2 cells. Runs after all primitives exist, so a cell may
    refer to a primitive declared later in the document."""
    cells: list[Cell] = []
    for name, cell_schema in index.cells.items():
        region = region_from_yaml_dict(cell_schema.region, ctx.primitive_ids)
        cells.append(Cell(
            id=ctx.fresh_id("c"),
            region=region,
            material_id=cell_schema.material,
            name=name,
        ))
    return cells


def _find_boolean_children(placements: dict[str, BaseComponentSchema]) -> set[str]:
    """Names of placements owned by a BooleanPlacement.

    Those are expanded by their parent, never on their own. A placement can
    belong to only one parent.
    """
    parent_of: dict[str, str] = {}
    for name, schema in placements.items():
        if not isinstance(schema, BooleanPlacementSchema):
            continue
        for child in schema.children:
            if child in parent_of:
                raise ValueError(
                    f"Placement '{child}' is listed as a child of both "
                    f"'{parent_of[child]}' and '{name}'."
                )
            parent_of[child] = name
    return set(parent_of)


def _is_universe_box(tpl: BaseComponentSchema | None) -> bool:
    return isinstance(tpl, BoxSchema) and tpl.is_universe()


def _place_universe_boxes(
    index: _SchemaIndex, boolean_children: set[str], ctx: Context,
) -> tuple[list[Surface], list[tuple[str, BoxSchema, dict[str, str]]]]:
    """Place the outer Boxes before anything else.

    The first universe Box publishes its bottom/top planes on the context so
    fuel pins placed later can share them. Returns the box surfaces and, for
    each box, ``(placement name, schema, labels)`` for its fill cell.
    """
    surfaces: list[Surface] = []
    boxes: list[tuple[str, BoxSchema, dict[str, str]]] = []

    for name, schema in index.placements.items():
        if name in boolean_children or not isinstance(schema, SinglePlacementSchema):
            continue
        tpl = index.templates.get(schema.template)
        if not _is_universe_box(tpl):
            continue
        if _is_rotated(_rotation_of(schema)):
            raise NotImplementedError(
                f"SinglePlacement '{name}' rotates the outer/universe Box '{schema.template}' — "
                f"this drives the whole model's coordinate frame and FuelPin axial-bounds sharing, "
                f"so rotating it isn't 'rotate one object', it's 'rotate the whole model', which "
                f"isn't supported. Set role: solid on '{schema.template}' if you meant to rotate "
                f"an ordinary interior box, or remove the rotation here."
            )

        box_surfaces, labels = _place_universe_box(tpl, schema.position(), ctx)
        surfaces.extend(box_surfaces)
        if not boxes:
            ctx.axial_bot_id = labels["bot"]
            ctx.axial_top_id = labels["top"]
        boxes.append((name, tpl, labels))

    return surfaces, boxes


def _place_remaining(
    index: _SchemaIndex, boolean_children: set[str], ctx: Context,
) -> list[SceneObject]:
    """Place every placement except boolean children and universe Boxes."""
    objects: list[SceneObject] = []

    for name, schema in index.placements.items():
        if name in boolean_children:
            continue

        if isinstance(schema, SinglePlacementSchema):
            if _is_universe_box(index.templates.get(schema.template)):
                continue  # already placed by _place_universe_boxes
            objects.extend(_expand_single_placement_objects(schema, name, index, ctx))
        elif isinstance(schema, BooleanPlacementSchema):
            objects.extend(_place_boolean_placement(schema, name, index, ctx))
        elif isinstance(schema, _LATTICE_TYPES):
            if name in index.nested_lattice_templates:
                continue  # expanded as part of the outer lattice
            objects.extend(_expand_lattice(schema, name, index, ctx))

    return objects