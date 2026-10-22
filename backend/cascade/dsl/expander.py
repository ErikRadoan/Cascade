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

    # Rotated-instance bookkeeping (see _fill_instance / _finish_rotated_instances).
    # Every fill cell stamped into the ROOT universe: (confining sphere id,
    # centre, radius, fill cell). The universe Box's moderator cell excludes
    # each sphere, and each fill cell is clipped to the Box at the end.
    root_fills: list[tuple[str, Vec3, float, Cell]] = field(default_factory=list)
    # Background cells of local universes. Their material is the universe
    # Box's material, which isn't known until every Box has been placed.
    background_cells: list[Cell] = field(default_factory=list)

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
    if op == "union":
        # Flat n-ary union rather than a left-nested chain of binary unions.
        return regions[0] if len(regions) == 1 else Union(list(regions))
    result = regions[0]
    for region in regions[1:]:
        if op == "intersection":
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
    """Names of a boolean composite's operands (all three ops may be n-ary)."""
    return schema.operand_names()


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
    if parent_universe == "0":
        ctx.root_fills.append((confine.id, center, radius, fill_cell))
    return [confine, fill_cell]


def _background(
    ctx: Context,
    universe_id: str,
    covered: list[Region],
    radius: float,
    name: str,
    material: str | None = None,
) -> list[SceneObject]:
    """A background cell for a local universe.

    A fill cell's confining sphere is larger than the object inside it, and
    OpenMC does not fall back to a neighbouring cell when a particle sits
    inside the fill cell but outside everything the local universe defines
    (it is a lost particle). The background covers the rest of the sphere in
    the local frame: ``inside sphere AND NOT (any local cell)``. Its material
    is filled in by ``_finish_rotated_instances`` (the Box's material) unless
    ``material`` is given. Returns ``[bounding sphere, background cell]``.
    """
    bound = Surface(
        id=ctx.fresh_id("s"),
        type_=SurfaceType.SPHERE,
        params={"x0": 0.0, "y0": 0.0, "z0": 0.0, "r": radius},
    )
    terms: list[Region] = [Inside(bound.id)]
    if covered:
        terms.append(Complement(covered[0] if len(covered) == 1 else Union(list(covered))))
    cell = Cell(
        id=ctx.fresh_id("c"),
        region=Intersection(terms),
        material_id=material,
        name=f"{name}_background",
        universe_id=universe_id,
    )
    if material is None:
        ctx.background_cells.append(cell)
    return [bound, cell]


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
    background = _background(
        ctx, universe_id, [c.region for c in scoped_cells], bounding_radius, cell_name,
    )
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
    return [*local_surfaces, *scoped_cells, *background, *fill]


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

    # Rotated placements are stamped in through fill cells that own their
    # whole confining sphere, so the moderator must not also claim it.
    for sphere_id in dict.fromkeys(sphere for sphere, *_rest in ctx.root_fills):
        exclusions.append(Outside(sphere_id))

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
    *,
    as_operand: bool = False,
) -> tuple[list[Surface], Region]:
    """Build a shape's surfaces and region at its local origin.

    Used for boolean operands and for placing a composite. ``visiting`` is the
    chain of composites being resolved, used to report circular references.
    ``as_operand`` is True for an operand of another composite: only then does
    a boolean's own x/y/z offset apply (a placed composite is positioned by
    its placement instead).
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
                operand, operand_name, index, ctx, next_visiting, as_operand=True,
            )
            composite_surfaces.extend(operand_surfaces)
            operand_regions.append(operand_region)

        region = _combine_operand_regions(schema, operand_regions)
        offset = _origin_offset(schema)
        if as_operand and any(offset):
            translated, id_map = _translate(composite_surfaces, *offset, ctx)
            composite_surfaces = _only_surfaces(translated)
            region = _remap_region(region, id_map)
        return composite_surfaces, region

    raise TypeError(
        f"'{name}' (type {type(schema).__name__}) cannot be used as a "
        f"boolean composite operand."
    )


def _combine_operand_regions(schema: BaseComponentSchema, regions: list[Region]) -> Region:
    """Fold operand regions flat (n-ary), not as a chain of binary nodes."""
    if isinstance(schema, UnionSchema):
        return Union(list(regions))
    if isinstance(schema, SubtractionSchema):
        kept, *cutters = regions
        return Intersection([kept, *(Complement(r) for r in cutters)])
    return Intersection(list(regions))


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
    placement's x/y/z, and then merged into ONE cell whatever the op. A union
    used to keep one cell per child, which left overlapping children as
    overlapping cells (undefined in OpenMC) and made the union not behave as
    a single object.
    """
    if name in visiting:
        chain = " -> ".join((*visiting, name))
        raise ValueError(f"Circular BooleanPlacement reference: {chain}.")

    children = _expand_boolean_children(schema, name, index, ctx, (*visiting, name))
    material_filter = set(schema.materials) if schema.materials else None

    return _merge_children(children, schema.op, name, material_filter, ctx)


@dataclass
class _RotatedOperand:
    """A rotated BooleanPlacement child, still in its own origin-local frame.

    Its shape lives inside a rotated universe, so it has no region in the
    parent frame and can't be folded into siblings' regions directly. See
    ``_merge_with_rotated``.
    """

    surfaces: list[Surface]
    cells: list[Cell]       # origin-local material cells
    position: Vec3          # placement position in the parent frame
    rotation: Vec3
    radius: float           # bounding-sphere radius around ``position``


_BooleanOperand = tuple[list[Surface], list[Cell]] | _RotatedOperand


def _rotated_operand(
    child: SinglePlacementSchema, child_name: str, index: _SchemaIndex, ctx: Context, offset: Vec3,
) -> _RotatedOperand:
    tpl = index.templates.get(child.template)
    if tpl is None:
        raise ValueError(f"SinglePlacement '{child_name}' references undefined template '{child.template}'.")
    x, y, z = child.position()
    position = (x + offset[0], y + offset[1], z + offset[2])

    if templates.is_composite_template(tpl):
        surfaces, cells = templates.expand_template(
            ctx, tpl, child_name, _NO_OFFSET, force_local_axial=True,
        )
    else:
        # Interior geometry: boundary conditions are cleared (inside_boolean=False).
        surfaces, region = _build_solid_at_origin(child, child_name, tpl, index, ctx, inside_boolean=False)
        cells = [Cell(id=ctx.fresh_id("c"), region=region, material_id=tpl.material, name=child_name)]

    return _RotatedOperand(
        surfaces=list(surfaces), cells=list(cells), position=position,
        rotation=_rotation_of(child),
        radius=_local_bounding_radius(tpl, child.template, index),
    )


def _expand_boolean_children(
    schema: BooleanPlacementSchema,
    name: str,
    index: _SchemaIndex,
    ctx: Context,
    visiting: tuple[str, ...],
) -> list[_BooleanOperand]:
    """Expand each child into its (surfaces, cells), offset by the parent.

    A rotated SinglePlacement child comes back as a ``_RotatedOperand``.
    """
    offset = (schema.x, schema.y, schema.z)
    results: list[_BooleanOperand] = []

    for child_name in schema.children:
        child = index.placements.get(child_name)
        if child is None:
            raise ValueError(
                f"BooleanPlacement '{name}' child '{child_name}' is not a defined placement."
            )

        if isinstance(child, BooleanPlacementSchema):
            objects = _place_boolean_placement(child, child_name, index, ctx, visiting)
            if any(isinstance(o, Cell) and o.fill is not None for o in objects):
                raise NotImplementedError(
                    f"BooleanPlacement '{name}': child '{child_name}' is a BooleanPlacement that "
                    f"contains a rotated child, and can't be nested inside another boolean yet. "
                    f"Put the rotated object directly in '{name}' instead."
                )
            if any(offset):
                objects, _ = _translate(objects, *offset, ctx)
        elif isinstance(child, SinglePlacementSchema):
            if _is_rotated(_rotation_of(child)):
                results.append(_rotated_operand(child, child_name, index, ctx, offset))
                continue
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


def _merge_children(
    children: list[_BooleanOperand],
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

    rotated = [c for c in children if isinstance(c, _RotatedOperand)]
    if len(rotated) > 1:
        raise NotImplementedError(
            f"BooleanPlacement '{name}' has {len(rotated)} rotated children. Only one rotated "
            f"child per BooleanPlacement is supported: each one is confined by its own sphere "
            f"and the spheres would overlap. Group the others in a nested BooleanPlacement."
        )
    if rotated:
        return _merge_with_rotated(children, op, name, material_filter, ctx)

    kept_materials: list[str | None] = []

    for child_surfaces, child_cells in children:
        surfaces.extend(child_surfaces)
        kept = [c for c in child_cells if _keeps_material(c, material_filter)]
        if not kept:
            continue
        if material is None:
            material = kept[0].material_id
        for cell in kept:
            if cell.material_id not in kept_materials:
                kept_materials.append(cell.material_id)
        region = _union_of_cell_regions(kept)
        if region is not None:
            child_regions.append(region)

    if not child_regions:
        return list(surfaces)

    if len(kept_materials) > 1:
        ctx.warnings.append(
            f"BooleanPlacement '{name}' ({op}) merges cells of several materials "
            f"({', '.join(str(m) for m in kept_materials)}) into one cell of material "
            f"'{material}'. Set `materials` on '{name}' to choose which materials it keeps."
        )

    cell = Cell(
        id=ctx.fresh_id("c"),
        region=_combine_regions(op, child_regions),
        material_id=material,
        name=name,
    )
    return [*surfaces, cell]


def _piece_universe(
    ctx: Context,
    x_cells: list[Cell],
    x_material: str | None,
    bg_material: str | None,
    radius: float,
    name: str,
) -> tuple[str, list[SceneObject]]:
    """A local universe for one part of a rotated composite operand.

    ``x_cells`` are the rotated operand's cells, re-created with a single
    material. A material of None means "the Box's material", resolved in
    ``_finish_rotated_instances``. No ``x_cells`` gives an empty universe
    holding only a background.
    """
    universe_id = ctx.fresh_id("u")
    scoped: list[Cell] = []
    for source in x_cells:
        cell = Cell(id=ctx.fresh_id("c"), region=source.region, material_id=x_material,
                    name=name, universe_id=universe_id)
        if x_material is None:
            ctx.background_cells.append(cell)
        scoped.append(cell)
    background = _background(
        ctx, universe_id, [c.region for c in scoped], radius, name, material=bg_material,
    )
    return universe_id, [*scoped, *background]


def _merge_with_rotated(
    children: list[_BooleanOperand],
    op: str,
    name: str,
    material_filter: set[str] | None,
    ctx: Context,
) -> list[SceneObject]:
    """Merge children when exactly one of them is rotated.

    The rotated operand X lives in its own universe, so its region doesn't
    exist in the parent frame. The ordinary siblings, though, fold into one
    region R there. X's confining sphere S is then partitioned by R: each
    part is a fill cell that instances a small universe holding X's cells
    (with the composite's material, or the Box's for a cutter) over a
    background. Nothing overlaps, and the result is the same single-material
    object the unrotated merge produces. With m the first kept child's material:

      union          R-S: m            S&R: X=m, bg=m      S&~R: X=m, bg=Box
      intersection                     S&R: X=m, bg=Box    S&~R: empty (Box)
      subtraction    X kept: R = cutters   S&~R: X=m, bg=Box    S&R: empty
      subtraction    X cutter: R = kept - other cutters
                     R-S: m            S&R: X=Box, bg=m    S&~R: empty

    "Box" is the universe Box's material (the moderator that fills the
    space the object doesn't); "empty" is a universe of just that.
    """
    surfaces: list[Surface] = []
    # (is_rotated, region, material) for every operand that kept a cell.
    contributors: list[tuple[bool, Region | None, str | None]] = []
    x_cells: list[Cell] = []
    rotated = next(c for c in children if isinstance(c, _RotatedOperand))

    for child in children:
        if isinstance(child, _RotatedOperand):
            surfaces.extend(child.surfaces)
            x_cells = [c for c in child.cells if _keeps_material(c, material_filter)]
            if x_cells:
                contributors.append((True, None, x_cells[0].material_id))
            continue
        child_surfaces, child_cells = child
        surfaces.extend(child_surfaces)
        kept = [c for c in child_cells if _keeps_material(c, material_filter)]
        if kept:
            contributors.append((False, _union_of_cell_regions(kept), kept[0].material_id))

    plain_only = [c for c in children if not isinstance(c, _RotatedOperand)]
    if not x_cells:
        return _merge_children(plain_only, op, name, material_filter, ctx)
    if all(is_rot for is_rot, _r, _m in contributors):
        # Nothing to combine with: it is just a rotated object.
        cells = [Cell(id=c.id, region=c.region, material_id=contributors[0][2], name=name)
                 for c in x_cells]
        return [*surfaces, *_place_rotated(
            [*rotated.surfaces, *cells], rotated.position, rotated.rotation, rotated.radius, ctx, name,
        )]

    material = contributors[0][2]
    plain = [r for is_rot, r, _m in contributors if not is_rot and r is not None]

    def any_of(regions: list[Region]) -> Region:
        return regions[0] if len(regions) == 1 else Union(list(regions))

    x_is_first = contributors[0][0]
    if op == "union":
        region = any_of(plain)
    elif op == "intersection":
        region = plain[0] if len(plain) == 1 else Intersection(list(plain))
    elif op == "subtraction":
        if x_is_first:
            region = any_of(plain)                      # the cutters
        else:
            kept_body, cutters = plain[0], plain[1:]
            region = kept_body if not cutters else Intersection([kept_body, Complement(any_of(cutters))])
    else:
        raise ValueError(f"Unknown boolean op '{op}'")

    confine = Surface(
        id=ctx.fresh_id("s"), type_=SurfaceType.SPHERE,
        params={"x0": rotated.position[0], "y0": rotated.position[1],
                "z0": rotated.position[2], "r": rotated.radius},
    )
    inside_s, outside_s = Inside(confine.id), Outside(confine.id)
    objects: list[SceneObject] = [*surfaces, confine]

    # (part region, x material, background material); None = the Box's material.
    parts: list[tuple[Region, str | None, str | None]]
    keeps_plain_outside = False
    if op == "union":
        keeps_plain_outside = True
        parts = [(region, material, material), (Complement(region), material, None)]
    elif op == "intersection":
        parts = [(region, material, None), (Complement(region), None, None)]
    elif x_is_first:
        parts = [(Complement(region), material, None), (region, None, None)]
    else:
        keeps_plain_outside = True
        parts = [(region, None, material), (Complement(region), None, None)]

    if keeps_plain_outside:
        objects.append(Cell(
            id=ctx.fresh_id("c"), region=Intersection([region, outside_s]),
            material_id=material, name=name,
        ))

    for part_region, x_material, bg_material in parts:
        is_empty = x_material is None and bg_material is None
        use_x = [] if is_empty else x_cells
        universe_id, local = _piece_universe(ctx, use_x, x_material, bg_material, rotated.radius, name)
        fill_cell = Cell(
            id=ctx.fresh_id("c"),
            region=Intersection([inside_s, part_region]),
            material_id=None, name=name, universe_id="0",
            fill=universe_id, translation=rotated.position, rotation=rotated.rotation,
        )
        ctx.root_fills.append((confine.id, rotated.position, rotated.radius, fill_cell))
        objects.extend([*local, fill_cell])

    return objects


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
    """Expand a rotated lattice as an "assembly" universe.

    Pins that carry no rotation of their own are plain translated copies
    placed directly in the assembly universe, so they need no fill cell and no
    confining sphere. Only pins with a ``pin_rotation_overrides`` entry become
    fill cells, and pins with the same own-rotation share one pin universe.
    The assembly universe gets a background cell, and one outer fill cell
    instances it into the root universe with the lattice-level rotation,
    pivoting about the lattice origin.

    Cell order inside the assembly universe matters: OpenMC takes the first
    cell that contains a point, so the direct (unrotated) pins come first and
    the overridden pins' confining spheres can't shadow them.

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

    direct: list[SceneObject] = []        # unrotated pins, in the assembly universe
    prototypes: list[SceneObject] = []    # pin universes of overridden pins
    fills: list[SceneObject] = []         # fill cells of overridden pins
    covered: list[Region] = []            # regions the assembly background must avoid
    pin_universe_by_rotation: dict[Vec3, str] = {}
    override_centres: list[Vec3] = []
    assembly_radius = 0.0

    for i, position in enumerate(positions):
        local_position = (position[0] - origin[0], position[1] - origin[1], position[2] - origin[2])
        own_rotation = pin_rotations[i]
        distance = math.sqrt(sum(v ** 2 for v in local_position))
        assembly_radius = max(assembly_radius, distance + pin_radius)

        if not _is_rotated(own_rotation):
            surfaces, cells = templates.expand_template(
                ctx, tpl, f"{lattice_name}_{i}", local_position, force_local_axial=True,
            )
            direct.extend(surfaces)
            for cell in cells:
                scoped = Cell(id=cell.id, region=cell.region, material_id=cell.material_id,
                              name=cell.name, universe_id=assembly_universe)
                direct.append(scoped)
                covered.append(scoped.region)
            continue

        pin_universe = pin_universe_by_rotation.get(own_rotation)
        if pin_universe is None:
            prototype_number = len(pin_universe_by_rotation)
            local_surfaces, local_cells = templates.expand_template(
                ctx, tpl, f"{lattice_name}_proto{prototype_number}", _NO_OFFSET, force_local_axial=True,
            )
            pin_universe = ctx.fresh_id("u")
            scoped_cells = [
                Cell(id=c.id, region=c.region, material_id=c.material_id, name=c.name,
                     universe_id=pin_universe)
                for c in local_cells
            ]
            prototypes.extend(local_surfaces)
            prototypes.extend(scoped_cells)
            prototypes.extend(_background(
                ctx, pin_universe, [c.region for c in scoped_cells], pin_radius,
                f"{lattice_name}_proto{prototype_number}",
            ))
            pin_universe_by_rotation[own_rotation] = pin_universe

        fill = _fill_instance(
            ctx,
            name=f"{lattice_name}_{i}",
            parent_universe=assembly_universe,
            child_universe=pin_universe,
            translation=local_position,
            rotation=own_rotation,
            center=local_position,
            radius=pin_radius,
        )
        fills.extend(fill)
        covered.append(fill[1].region)
        override_centres.append(local_position)

    if len(override_centres) > 1:
        ctx.warnings.append(
            f"Lattice '{lattice_name}' has {len(override_centres)} pins with their own rotation. "
            f"Each rotated pin is confined by a sphere of radius {pin_radius:.1f} cm, which is "
            f"much larger than the pin pitch, so these spheres overlap and the earlier pin's "
            f"universe wins wherever they do. Prefer one rotated pin per lattice."
        )

    assembly_background = _background(ctx, assembly_universe, covered, assembly_radius, lattice_name)
    root_fill = _fill_instance(
        ctx,
        name=lattice_name,
        parent_universe="0",
        child_universe=assembly_universe,
        translation=origin,
        rotation=schema.assembly_rotation(),
        center=origin,
        radius=assembly_radius,
    )
    return [*direct, *prototypes, *fills, *assembly_background, *root_fill]


# ---------------------------------------------------------------------------
# Rotated-instance finishing (runs once every Box has been placed)
# ---------------------------------------------------------------------------

def _finish_rotated_instances(
    universe_boxes: list[tuple[str, BoxSchema, dict[str, str]]], ctx: Context,
) -> None:
    """Resolve what a rotated instance can only know after the Boxes exist.

    - Background cells take the universe Box's material (void when the model
      has no Box, since there is no moderator to match).
    - Root fill cells are clipped to the Box interior. A confining sphere can
      reach past the Box, and without clipping the rotated universe would
      leak through the Box's boundary planes (vacuum/reflective faces).
    """
    material = universe_boxes[0][1].material if universe_boxes else None
    for cell in ctx.background_cells:
        cell.material_id = material

    if not ctx.root_fills:
        return

    if len(universe_boxes) == 1:
        _name, _schema, labels = universe_boxes[0]
        for _sphere, _centre, _radius, fill_cell in ctx.root_fills:
            fill_cell.region = Intersection([fill_cell.region, *_box_interior_terms(labels)])
    elif len(universe_boxes) > 1:
        ctx.warnings.append(
            "Rotated placements are not clipped to a Box when the model has several "
            "universe Boxes; check that no rotated object reaches past a Box boundary."
        )

    # One entry per confining sphere (a composite can share one between parts).
    spheres = list({sid: (c, r, cell) for sid, c, r, cell in reversed(ctx.root_fills)}.values())[::-1]
    for i, (c1, r1, cell1) in enumerate(spheres):
        for c2, r2, cell2 in spheres[i + 1:]:
            if math.dist(c1, c2) < r1 + r2:
                ctx.warnings.append(
                    f"The confining spheres of rotated '{cell1.name}' and '{cell2.name}' overlap; "
                    f"inside the overlap the earlier one wins. Move them apart or reduce their size."
                )


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

    _finish_rotated_instances(universe_boxes, ctx)

    # OpenMC takes the first cell in a universe that contains a point. Rotated
    # fill cells own a whole confining sphere, which can overlap ordinary
    # neighbours, so list them last and let the ordinary cells win.
    root_fill_ids = {id(cell) for *_rest, cell in ctx.root_fills}
    cells = _only_cells(objects)
    cells = [c for c in cells if id(c) not in root_fill_ids] + [c for c in cells if id(c) in root_fill_ids]

    return CascadeGeometry(
        id=str(uuid.uuid4()),
        name=geom_name,
        surfaces=_only_surfaces(objects),
        cells=cells,
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