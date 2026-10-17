"""Geometry expander — n-ary Union, Box offset, boundary force, Box.role.

Full documented version: project artifacts/expander.py.
"""

from __future__ import annotations
import math
import uuid
from dataclasses import dataclass, field
from domain.geometry import BoundaryType, CascadeGeometry, Cell, Complement, Inside, Intersection, LatticeInstance, Outside, Region, Surface, SurfaceType, Union, region_from_yaml_dict
from dsl import primitives, templates
from .schema.base import BaseComponentSchema
from .schema.boolean import BooleanPlacementSchema, IntersectionSchema, SubtractionSchema, UnionSchema
from .schema.box import BoxSchema
from .schema.cell import CellSchema
from .schema.fuel_pin import FuelPinSchema
from dsl.schema.lattice import HexLatticeSchema, SquareLatticeSchema
from dsl.schema.single_placement import SinglePlacementSchema
from .schema.sphere import SphereSchema
from .schema.torus import TorusSchema

@dataclass
class Context:
    param_values: dict[str, float] = field(default_factory=dict)
    outermost_surfaces: list[str] = field(default_factory=list)
    # Bugfix: (radial_id, bot_id, top_id) for every FuelPin placed inside a
    # Box, so the Box's fill/moderator cell can exclude exactly each pin's
    # *finite* solid volume — not an infinite-height cylinder. Kept alongside
    # outermost_surfaces (still populated, in case anything else reads it)
    # rather than replacing it.
    outermost_pin_extents: list[tuple[str, str, str]] = field(default_factory=list)
    axial_bot_id: str | None = field(default=None)
    axial_top_id: str | None = field(default=None)
    lattice_instances: list[LatticeInstance] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    # Bugfix: name -> Step-0 surface id for every Tier-1 primitive, so a
    # primitive used as a boolean composite operand can be referenced
    # instead of re-expanded (see _expand_shape_at_origin).
    primitive_ids: dict[str, str] = field(default_factory=dict)
    _counter: int = field(default=0, init=False, repr=False)

    def fresh_id(self, prefix: str='s') -> str:
        self._counter += 1
        return f'{prefix}{self._counter}'

    def has_axial_bounds(self) -> bool:
        return self.axial_bot_id is not None and self.axial_top_id is not None
_TRANSLATE_PARAMS: dict[SurfaceType, dict[str, str]] = {SurfaceType.PLANE_X: {'x': 'dx', 'x0': 'dx'}, SurfaceType.PLANE_Y: {'y': 'dy', 'y0': 'dy'}, SurfaceType.PLANE_Z: {'z': 'dz', 'z0': 'dz'}, SurfaceType.CYLINDER_Z: {'x': 'dx', 'x0': 'dx', 'y': 'dy', 'y0': 'dy'}, SurfaceType.CYLINDER_X: {'y': 'dy', 'y0': 'dy', 'z': 'dz', 'z0': 'dz'}, SurfaceType.CYLINDER_Y: {'x': 'dx', 'x0': 'dx', 'z': 'dz', 'z0': 'dz'}, SurfaceType.SPHERE: {'x': 'dx', 'x0': 'dx', 'y': 'dy', 'y0': 'dy', 'z': 'dz', 'z0': 'dz'}, SurfaceType.CONE_Z: {'x': 'dx', 'x0': 'dx', 'y': 'dy', 'y0': 'dy', 'z': 'dz', 'z0': 'dz'}, SurfaceType.TORUS: {'x': 'dx', 'x0': 'dx', 'y': 'dy', 'y0': 'dy', 'z': 'dz', 'z0': 'dz'}}

def _translate_params(type_: SurfaceType, params: dict, dx: float, dy: float, dz: float) -> dict:
    offsets = {'dx': dx, 'dy': dy, 'dz': dz}
    axes = _TRANSLATE_PARAMS.get(type_, {})
    result = dict(params)
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

def _translate(objects: list[Surface | Cell], dx: float, dy: float, dz: float, ctx: Context) -> tuple[list[Surface | Cell], dict[str, str]]:
    id_map: dict[str, str] = {}
    new_surfaces: list[Surface] = []
    new_cells: list[Cell] = []
    for obj in objects:
        if isinstance(obj, Surface):
            new_id = ctx.fresh_id('s')
            id_map[obj.id] = new_id
            new_surfaces.append(Surface(id=new_id, type_=obj.type_, params=_translate_params(obj.type_, obj.params, dx, dy, dz), boundary_type=obj.boundary_type))
    for obj in objects:
        if isinstance(obj, Cell):
            new_cells.append(Cell(id=ctx.fresh_id('c'), region=_remap_region(obj.region, id_map), material_id=obj.material_id, name=obj.name))
    return (new_surfaces + new_cells, id_map)


# ---------------------------------------------------------------------------
# Rotation support — local universe + OpenMC-native fill-cell rotation.
#
# Rotating a surface's own coefficients only works for quadrics (planes,
# cylinders, cones, spheres); Torus is a quartic and CANNOT be expressed as
# a rotated general surface in OpenMC at all. So rotation is never baked
# into coefficients here. Instead, a rotated placement's local geometry is
# authored at the origin (untranslated, unrotated — exactly like the
# existing "Step-0" surfaces used for boolean-composite operands), wrapped
# in its own universe, and instanced into the parent universe via a single
# fill-cell carrying OpenMC's native `translation`/`rotation` attributes.
# OpenMC transforms the *particle* into local coordinates during tracking,
# so every surface type comes out exactly right at any angle — this is the
# same mechanism OpenMC itself recommends for rotated geometry.
#
# The fill-cell needs a region in the PARENT frame to confine where the
# rotated universe appears. A sphere centered at the placement position is
# used for this, sized to the local geometry's own circumradius from its
# local origin — a sphere is rotation-invariant about its own center, so
# this bound is exact regardless of what rotation is applied, with no
# rotation matrix math needed on our side at all.
# ---------------------------------------------------------------------------

_UNBOUNDED_ROTATION_MSG = (
    "'{name}' ({type_name}) has unbounded extent (an infinite plane, "
    "cylinder, or cone) and can't be rotated as a standalone placement — "
    "there's no finite bounding volume to confine the rotated local "
    "universe within. Combine it with bounding surfaces in a boolean "
    "composite (so the composite as a whole is finite), or place it "
    "without rotation."
)


def _local_bounding_radius(schema: BaseComponentSchema, name: str, schemas: dict[str, BaseComponentSchema]) -> float:
    """Conservative bounding-sphere radius, measured from the schema's own
    local origin (0, 0, 0) — i.e. before any translation/rotation. Exact
    for Sphere; conservative (uses full height, not half) for Box/FuelPin,
    since their local origin sits at a face/end rather than the centroid.
    """
    if isinstance(schema, BoxSchema):
        return math.sqrt(schema.half_x() ** 2 + schema.half_y() ** 2 + schema.z_size ** 2)
    if isinstance(schema, FuelPinSchema):
        max_r = max((r for r, _ in schema.radial_layers()), default=0.0)
        return math.sqrt(max_r ** 2 + schema.pellet_height ** 2)
    if isinstance(schema, SphereSchema):
        return schema.radius
    if isinstance(schema, TorusSchema):
        tube_h = schema.tube_height if schema.tube_height is not None else schema.tube_radius
        return schema.ring_radius + max(schema.tube_radius, tube_h)
    if isinstance(schema, _BOOLEAN_COMPOSITE_TYPES):
        if isinstance(schema, UnionSchema) and hasattr(schema, 'operand_names'):
            op_names = schema.operand_names()
        else:
            op_names = [schema.a, schema.b]
        best = 0.0
        for op_name in op_names:
            op_schema = schemas.get(op_name)
            if op_schema is None:
                continue
            ox = float(getattr(op_schema, 'x', 0.0) or 0.0)
            oy = float(getattr(op_schema, 'y', 0.0) or 0.0)
            oz = float(getattr(op_schema, 'z', 0.0) or 0.0)
            offset = math.sqrt(ox ** 2 + oy ** 2 + oz ** 2)
            best = max(best, offset + _local_bounding_radius(op_schema, op_name, schemas))
        return best
    raise ValueError(_UNBOUNDED_ROTATION_MSG.format(name=name, type_name=type(schema).__name__))


def _rotation_of(schema: BaseComponentSchema) -> tuple[float, float, float]:
    return (
        float(getattr(schema, 'rx', 0.0) or 0.0),
        float(getattr(schema, 'ry', 0.0) or 0.0),
        float(getattr(schema, 'rz', 0.0) or 0.0),
    )


def _is_rotated(rotation: tuple[float, float, float]) -> bool:
    return rotation[0] != 0.0 or rotation[1] != 0.0 or rotation[2] != 0.0


def _place_rotated(local_objects: list[Surface | Cell], position: tuple[float, float, float], rotation: tuple[float, float, float], bounding_radius: float, ctx: Context, cell_name: str) -> list[Surface | Cell]:
    """Wrap already-expanded, origin-local `local_objects` (surfaces plus
    exactly one material Cell, both authored untranslated/unrotated) into
    their own universe, then instance that universe into the parent
    (root) universe via one fill-cell carrying `translation`/`rotation`.
    """
    universe_id = ctx.fresh_id('u')
    local_surfaces = [o for o in local_objects if isinstance(o, Surface)]
    local_cells = [o for o in local_objects if isinstance(o, Cell)]
    scoped_cells = [Cell(id=c.id, region=c.region, material_id=c.material_id, name=c.name, universe_id=universe_id) for c in local_cells]
    confine = Surface(id=ctx.fresh_id('s'), type_=SurfaceType.SPHERE, params={'x0': position[0], 'y0': position[1], 'z0': position[2], 'r': bounding_radius})
    fill_cell = Cell(id=ctx.fresh_id('c'), region=Inside(confine.id), material_id=None, name=cell_name, universe_id='0', fill=universe_id, translation=position, rotation=rotation)
    return list(local_surfaces) + scoped_cells + [confine, fill_cell]

def _expand_box_surfaces(schema: BoxSchema, ctx: Context) -> tuple[list[Surface], dict[str, str]]:
    hx = schema.half_x()
    hy = schema.half_y()
    bt = schema.boundary_type
    s_xlo = Surface(id=ctx.fresh_id('s'), type_=SurfaceType.PLANE_X, params={'x': -hx}, boundary_type=bt)
    s_xhi = Surface(id=ctx.fresh_id('s'), type_=SurfaceType.PLANE_X, params={'x': +hx}, boundary_type=bt)
    s_ylo = Surface(id=ctx.fresh_id('s'), type_=SurfaceType.PLANE_Y, params={'y': -hy}, boundary_type=bt)
    s_yhi = Surface(id=ctx.fresh_id('s'), type_=SurfaceType.PLANE_Y, params={'y': +hy}, boundary_type=bt)
    s_bot = Surface(id=ctx.fresh_id('s'), type_=SurfaceType.PLANE_Z, params={'z': 0.0}, boundary_type=bt)
    s_top = Surface(id=ctx.fresh_id('s'), type_=SurfaceType.PLANE_Z, params={'z': schema.z_size}, boundary_type=bt)
    label_map = {'xlo': s_xlo.id, 'xhi': s_xhi.id, 'ylo': s_ylo.id, 'yhi': s_yhi.id, 'bot': s_bot.id, 'top': s_top.id}
    return ([s_xlo, s_xhi, s_ylo, s_yhi, s_bot, s_top], label_map)

def _place_box(schema: BoxSchema, position: tuple[float, float, float], ctx: Context):
    (dx, dy, dz) = position
    (box_surfaces, label_map) = _expand_box_surfaces(schema, ctx)
    translated: list[Surface] = []
    id_map: dict[str, str] = {}
    for s in box_surfaces:
        new_id = ctx.fresh_id('s')
        id_map[s.id] = new_id
        translated.append(Surface(id=new_id, type_=s.type_, params=_translate_params(s.type_, s.params, dx, dy, dz), boundary_type=s.boundary_type))
    translated_labels = {k: id_map[v] for (k, v) in label_map.items()}
    ctx.axial_bot_id = translated_labels['bot']
    ctx.axial_top_id = translated_labels['top']
    return (translated, translated_labels)

def _build_fill_cell(schema: BoxSchema, translated_labels: dict[str, str], ctx: Context, cell_name: str) -> Cell:
    box_interior = [Outside(translated_labels['xlo']), Inside(translated_labels['xhi']), Outside(translated_labels['ylo']), Inside(translated_labels['yhi']), Outside(translated_labels['bot']), Inside(translated_labels['top'])]
    # Bugfix: this used to exclude Outside(radial_cylinder) alone, which is
    # an infinite-height cylindrical cutout — it has no idea where the pin's
    # own top/bottom are. As long as every pin spanned the box's *full*
    # height (the old pellet_height bug), that was invisible: the pin's own
    # material always filled the excluded column anyway. Now that a pin can
    # be shorter than its Box, the space above a short pin was neither
    # claimed by the pin (too short) nor refilled by the moderator (still
    # excluded there, since the cylinder has no cap) — a void the exact
    # shape of the pin, open straight through the Box's top face.
    # Fix: exclude each pin's *finite* solid volume via De Morgan's law.
    # NOT(Inside(cyl) AND Outside(bot) AND Inside(top))
    #   = Outside(cyl) OR Inside(bot) OR Outside(top)
    # i.e. "not radially inside the pin, OR below the pin's floor, OR above
    # the pin's own ceiling" — so the moderator correctly fills back in
    # above (and below) a pin shorter than its Box.
    outer_exclusions: list[Region] = []
    extents = getattr(ctx, 'outermost_pin_extents', None) or []
    covered_radial_ids = {radial_id for (radial_id, _bot, _top) in extents}
    for radial_id, bot_id, top_id in extents:
        outer_exclusions.append(Union([Outside(radial_id), Inside(bot_id), Outside(top_id)]))
    # Fallback for any radial id that (for whatever reason) never got a
    # tracked extent — keeps prior infinite-cylinder behavior rather than
    # silently dropping the exclusion.
    for sid in ctx.outermost_surfaces:
        if sid not in covered_radial_ids:
            outer_exclusions.append(Outside(sid))
    return Cell(id=ctx.fresh_id('c'), region=Intersection(box_interior + outer_exclusions), material_id=schema.material, name=cell_name)
_BOOLEAN_COMPOSITE_TYPES = (UnionSchema, SubtractionSchema, IntersectionSchema)

def _expand_shape_at_origin(schema: BaseComponentSchema, name: str, schemas: dict[str, BaseComponentSchema], ctx: Context, _visiting: frozenset[str]=frozenset()) -> tuple[list[Surface], Region]:
    if isinstance(schema, FuelPinSchema):
        raise TypeError(f"'{name}' is a FuelPin, which cannot be used as a boolean composite operand.")
    if isinstance(schema, BoxSchema):
        (surfaces, label_map) = _expand_box_surfaces(schema, ctx)
        # Bugfix: a Box used as a Subtraction/Intersection/Union operand is an
        # interior/composite surface, never the geometry's outer boundary.
        # _expand_box_surfaces() stamps schema.boundary_type (default
        # REFLECTIVE) onto all six planes; left unforced, an operand Box's
        # boundary condition leaks into OpenMC's surface model and reflects
        # particles off what should be a transparent cavity wall.
        surfaces = [Surface(id=s.id, type_=s.type_, params=s.params, boundary_type=BoundaryType.NONE) for s in surfaces]
        ox = float(getattr(schema, 'x', 0.0) or 0.0)
        oy = float(getattr(schema, 'y', 0.0) or 0.0)
        oz = float(getattr(schema, 'z', 0.0) or 0.0)
        if ox != 0.0 or oy != 0.0 or oz != 0.0:
            (translated, id_map) = _translate(surfaces, ox, oy, oz, ctx)
            surfaces = [o for o in translated if isinstance(o, Surface)]
            label_map = {k: id_map[v] for (k, v) in label_map.items()}
        region: Region = Intersection([Outside(label_map['xlo']), Inside(label_map['xhi']), Outside(label_map['ylo']), Inside(label_map['yhi']), Outside(label_map['bot']), Inside(label_map['top'])])
        return (surfaces, region)
    if primitives.is_primitive(schema):
        # Bugfix (was): this schema was already expanded once in expand()'s
        # Step 0 (every Tier-1 primitive is expanded unconditionally,
        # regardless of whether it's placed standalone or only used as an
        # operand here). To avoid a second, orphaned surface with no Cell
        # referencing it, this used to reuse the Step-0 surface id via
        # ctx.primitive_ids and return NO new surfaces at all.
        #
        # That "fix" broke composite placement instead: _place_boolean_composite
        # translates whatever `local_surfaces` this function returns by the
        # composite's placement offset — an empty list has nothing to
        # translate, so the composite's region silently kept pointing at the
        # primitive's original, untranslated Step-0 surface. A Subtraction of
        # two Spheres placed via SinglePlacement at (10, 20, 30) stayed put
        # at (0, 0, 0) with zero error or warning.
        #
        # Correct fix: always mint a fresh, independent copy here (matching
        # exactly how the BoxSchema branch above always re-expands fresh
        # surfaces for its own operand use) so the composite's own
        # placement translate has something real to shift. The Step-0
        # surface remains in geometry.surfaces at its own authored position
        # for any Tier-2 Cell that references this primitive by name
        # directly — a primitive can serve both roles at once, just as a
        # Box template can be both a standalone placement and a boolean
        # operand elsewhere, each with its own independent surfaces.
        (surfaces, region) = primitives.expand_primitive(ctx, schema, name)
        # Same interior-vs-boundary rule as Box just above: an operand's
        # boundary condition must never leak onto OpenMC's outer boundary.
        bt = getattr(schema, 'boundary_type', None)
        if bt is not None and bt != BoundaryType.NONE:
            surfaces = [Surface(id=s.id, type_=s.type_, params=s.params, boundary_type=BoundaryType.NONE) for s in surfaces]
        return (surfaces, region)
    if isinstance(schema, _BOOLEAN_COMPOSITE_TYPES):
        if name in _visiting:
            chain = ' -> '.join((*_visiting, name))
            raise ValueError(f'Circular reference detected while resolving boolean composite operands: {chain}.')
        next_visiting = _visiting | {name}
        if isinstance(schema, UnionSchema) and hasattr(schema, 'operand_names'):
            op_names = schema.operand_names()
        else:
            op_names = [schema.a, schema.b]
        surfaces = []
        regions = []
        for op_name in op_names:
            op_schema = schemas.get(op_name)
            if op_schema is None:
                raise ValueError(f"'{name}' operand references undefined template/primitive '{op_name}'.")
            (s_surfs, s_region) = _expand_shape_at_origin(op_schema, op_name, schemas, ctx, next_visiting)
            surfaces.extend(s_surfs)
            regions.append(s_region)
        if isinstance(schema, UnionSchema):
            region = regions[0] if len(regions) == 1 else Union(regions)
        elif isinstance(schema, SubtractionSchema):
            region = regions[0]
            for r in regions[1:]:
                region = Intersection([region, Complement(r)])
        else:
            region = regions[0]
            for r in regions[1:]:
                region = Intersection([region, r])
        return (surfaces, region)
    raise TypeError(f"'{name}' (type {type(schema).__name__}) cannot be used as a boolean composite operand.")

def _place_boolean_composite(schema, template_name: str, position: tuple[float, float, float], ctx: Context, schemas: dict[str, BaseComponentSchema], cell_name: str) -> list:
    (dx, dy, dz) = position
    (local_surfaces, local_region) = _expand_shape_at_origin(schema, template_name, schemas, ctx)
    (translated_objects, id_map) = _translate(local_surfaces, dx, dy, dz, ctx)
    translated_surfaces = [obj for obj in translated_objects if isinstance(obj, Surface)]
    translated_region = _remap_region(local_region, id_map)
    cell = Cell(id=ctx.fresh_id('c'), region=translated_region, material_id=schema.material, name=cell_name)
    return translated_surfaces + [cell]

def _combine_regions(op: str, regions: list[Region]) -> Region:
    if not regions:
        raise ValueError('Cannot combine empty region list')
    result = regions[0]
    for r in regions[1:]:
        if op == 'union':
            result = Union([result, r])
        elif op == 'intersection':
            result = Intersection([result, r])
        elif op == 'subtraction':
            result = Intersection([result, Complement(r)])
        else:
            raise ValueError(f"Unknown boolean op '{op}'")
    return result

def _cells_to_combined_region(cells: list[Cell]) -> Region | None:
    regions = [c.region for c in cells if c.region is not None]
    if not regions:
        return None
    if len(regions) == 1:
        return regions[0]
    return Union(regions)

def _expand_single_placement_objects(schema: SinglePlacementSchema, name: str, templates_by_name: dict, schemas: dict, ctx: Context, extra_offset: tuple[float, float, float]=(0.0, 0.0, 0.0), _inside_boolean: bool=False) -> list:
    tpl = templates_by_name.get(schema.template)
    if tpl is None:
        raise ValueError(f"SinglePlacement '{name}' references undefined template '{schema.template}'.")
    (px, py, pz) = schema.position()
    pos = (px + extra_offset[0], py + extra_offset[1], pz + extra_offset[2])
    rotation = _rotation_of(schema)
    if _is_rotated(rotation) and _inside_boolean:
        raise NotImplementedError(f"SinglePlacement '{name}' is a BooleanPlacement child and can't be rotated yet — combining a rotated (fill-cell) child's region with its siblings' Union/Subtraction/Intersection isn't supported in this pass. Rotate '{name}' as a standalone SinglePlacement instead, or remove its rotation.")
    if isinstance(tpl, BoxSchema):
        if _is_rotated(rotation):
            (local_surfaces, labels) = _expand_box_surfaces(tpl, ctx)
            local_surfaces = [Surface(id=s.id, type_=s.type_, params=s.params, boundary_type=BoundaryType.NONE) for s in local_surfaces]
            local_region = Intersection([Outside(labels['xlo']), Inside(labels['xhi']), Outside(labels['ylo']), Inside(labels['yhi']), Outside(labels['bot']), Inside(labels['top'])])
            cell = Cell(id=ctx.fresh_id('c'), region=local_region, material_id=tpl.material, name=name)
            radius = _local_bounding_radius(tpl, schema.template, schemas)
            return _place_rotated(list(local_surfaces) + [cell], pos, rotation, radius, ctx, name)
        (surfaces, labels) = _expand_box_surfaces(tpl, ctx)
        (translated, id_map) = _translate(surfaces, pos[0], pos[1], pos[2], ctx)
        translated_surfaces = [o for o in translated if isinstance(o, Surface)]
        tlabels = {k: id_map[v] for (k, v) in labels.items()}
        region = Intersection([Outside(tlabels['xlo']), Inside(tlabels['xhi']), Outside(tlabels['ylo']), Inside(tlabels['yhi']), Outside(tlabels['bot']), Inside(tlabels['top'])])
        cell = Cell(id=ctx.fresh_id('c'), region=region, material_id=tpl.material, name=name)
        return translated_surfaces + [cell]
    if primitives.is_primitive(tpl):
        # A Tier-1 primitive (Sphere, CylinderX/Y/Z, ConeZ, Torus — Plane
        # isn't included, see plane.py) can be placed directly as a solid
        # object exactly like Box's role="solid", provided it has a
        # `material` set. Mirrors Box just above: mint a fresh surface at
        # this SinglePlacement's own position (schema.x/y/z is what the
        # primitive would use standalone, but here the whole point is
        # SinglePlacement chooses the position — reusing the Step-0
        # standalone surface would hit the same "reuse breaks translation"
        # bug fixed in _expand_shape_at_origin above), then wrap it in a
        # Cell.
        if getattr(tpl, 'material', None) is None:
            raise ValueError(
                f"SinglePlacement '{name}' places '{schema.template}' (a "
                f"{type(tpl).__name__}), which has no `material` set. Add "
                f"material: <id> to '{schema.template}' to place it as a "
                f"solid object, or remove this SinglePlacement and "
                f"reference '{schema.template}' from a Tier-2 Cell region "
                f"expression instead."
            )
        if _is_rotated(rotation):
            (local_surfaces, local_region) = primitives.expand_primitive(ctx, tpl, schema.template)
            cell = Cell(id=ctx.fresh_id('c'), region=local_region, material_id=tpl.material, name=name)
            radius = _local_bounding_radius(tpl, schema.template, schemas)
            return _place_rotated(list(local_surfaces) + [cell], pos, rotation, radius, ctx, name)
        (surfaces, region) = primitives.expand_primitive(ctx, tpl, schema.template)
        (translated, id_map) = _translate(surfaces, pos[0], pos[1], pos[2], ctx)
        translated_surfaces = [o for o in translated if isinstance(o, Surface)]
        translated_region = _remap_region(region, id_map)
        cell = Cell(id=ctx.fresh_id('c'), region=translated_region, material_id=tpl.material, name=name)
        return translated_surfaces + [cell]
    if templates.is_composite_template(tpl):
        if _is_rotated(rotation):
            (local_surfaces, local_cells) = templates.expand_template(ctx, tpl, name, (0.0, 0.0, 0.0), force_local_axial=True)
            radius = _local_bounding_radius(tpl, schema.template, schemas)
            return _place_rotated(list(local_surfaces) + list(local_cells), pos, rotation, radius, ctx, name)
        (surfaces, cells) = templates.expand_template(ctx, tpl, name, pos)
        return list(surfaces) + list(cells)
    if isinstance(tpl, _BOOLEAN_COMPOSITE_TYPES):
        if _is_rotated(rotation):
            (local_surfaces, local_region) = _expand_shape_at_origin(tpl, schema.template, schemas, ctx)
            cell = Cell(id=ctx.fresh_id('c'), region=local_region, material_id=tpl.material, name=name)
            radius = _local_bounding_radius(tpl, schema.template, schemas)
            return _place_rotated(list(local_surfaces) + [cell], pos, rotation, radius, ctx, name)
        return _place_boolean_composite(tpl, schema.template, pos, ctx, schemas, cell_name=name)
    raise TypeError(f"SinglePlacement '{name}' (template type {type(tpl).__name__}) cannot be a BooleanPlacement child.")

def _place_boolean_placement(schema: BooleanPlacementSchema, name: str, placements: dict, templates_by_name: dict, schemas: dict, ctx: Context, _visiting: frozenset[str]=frozenset()) -> list:
    if name in _visiting:
        chain = ' -> '.join((*_visiting, name))
        raise ValueError(f'Circular BooleanPlacement reference: {chain}.')
    next_visiting = _visiting | {name}
    residual = (schema.x, schema.y, schema.z)
    child_results: list = []
    for child_name in schema.children:
        child = placements.get(child_name)
        if child is None:
            raise ValueError(f"BooleanPlacement '{name}' child '{child_name}' is not a defined placement.")
        if isinstance(child, BooleanPlacementSchema):
            objs = _place_boolean_placement(child, child_name, placements, templates_by_name, schemas, ctx, next_visiting)
            if residual != (0.0, 0.0, 0.0):
                (objs, _) = _translate(objs, residual[0], residual[1], residual[2], ctx)
            surfaces = [o for o in objs if isinstance(o, Surface)]
            cells = [o for o in objs if isinstance(o, Cell)]
            child_results.append((surfaces, cells))
        elif isinstance(child, SinglePlacementSchema):
            objs = _expand_single_placement_objects(child, child_name, templates_by_name, schemas, ctx, residual, _inside_boolean=True)
            surfaces = [o for o in objs if isinstance(o, Surface)]
            cells = [o for o in objs if isinstance(o, Cell)]
            child_results.append((surfaces, cells))
        elif isinstance(child, (SquareLatticeSchema, HexLatticeSchema)):
            raise TypeError(f"BooleanPlacement '{name}': lattice child '{child_name}' is not supported in v1.")
        else:
            raise TypeError(f"BooleanPlacement '{name}': child '{child_name}' has unsupported type {type(child).__name__}.")
    if not child_results:
        return []
    mat_filter = set(schema.materials) if schema.materials else None
    if schema.op == 'union':
        out: list = []
        for (surfaces, cells) in child_results:
            out.extend(surfaces)
            for c in cells:
                if mat_filter is None or c.material_id in mat_filter:
                    out.append(Cell(id=c.id, region=c.region, material_id=c.material_id, name=name if c.name is None else f'{name}_{c.name}'))
        return out
    all_surfaces: list = []
    child_regions: list = []
    pick_material: str | None = None
    for (surfaces, cells) in child_results:
        all_surfaces.extend(surfaces)
        kept = [c for c in cells if mat_filter is None or c.material_id in mat_filter]
        if not kept:
            continue
        if pick_material is None:
            pick_material = kept[0].material_id
        region = _cells_to_combined_region(kept)
        if region is not None:
            child_regions.append(region)
    if not child_regions:
        return all_surfaces
    final_region = _combine_regions(schema.op, child_regions)
    if pick_material is None:
        pick_material = 'H2O'
    cell = Cell(id=ctx.fresh_id('c'), region=final_region, material_id=pick_material, name=name)
    return all_surfaces + [cell]

def expand(schemas: dict[str, BaseComponentSchema], param_values: dict[str, float] | None=None, geom_name: str='cascade_geometry') -> CascadeGeometry:
    ctx = Context(param_values=param_values or {})
    all_objects: list = []
    # Populates ctx.primitive_ids so Tier-2 CellSchema region expressions
    # (region_from_yaml_dict, just below) can resolve a primitive's YAML
    # name to its Step-0 surface id. NOT used by boolean-composite operand
    # resolution any more (see _expand_shape_at_origin's primitive branch) —
    # that path always mints its own fresh, independently-translatable
    # copy instead of reusing this one, so a primitive can be placed via a
    # Tier-2 Cell at its authored position AND used as a boolean operand
    # elsewhere without the two uses fighting over one shared surface.
    primitive_name_to_id: dict[str, str] = ctx.primitive_ids
    cell_schemas: dict[str, CellSchema] = {}
    legacy_schemas: dict[str, BaseComponentSchema] = {}
    for (name, schema) in schemas.items():
        if primitives.is_primitive(schema):
            (surfaces, _region) = primitives.expand_primitive(ctx, schema, name)
            all_objects.extend(surfaces)
            primitive_name_to_id[name] = surfaces[-1].id
        elif isinstance(schema, CellSchema):
            cell_schemas[name] = schema
        else:
            legacy_schemas[name] = schema
    for (name, cell_schema) in cell_schemas.items():
        region = region_from_yaml_dict(cell_schema.region, primitive_name_to_id)
        all_objects.append(Cell(id=ctx.fresh_id('c'), region=region, material_id=cell_schema.material, name=name))
    templates_by_name: dict[str, BaseComponentSchema] = {}
    placements: dict[str, BaseComponentSchema] = {}
    _PLACEMENT_TYPES = (SinglePlacementSchema, SquareLatticeSchema, HexLatticeSchema, BooleanPlacementSchema)
    for (name, schema) in legacy_schemas.items():
        if isinstance(schema, _PLACEMENT_TYPES):
            placements[name] = schema
        else:
            templates_by_name[name] = schema
    # Tier-1 primitives are ALSO valid SinglePlacement templates (see the
    # primitive branches in this loop and in _expand_single_placement_objects)
    # provided they have `material` set — register them here so `schema.template`
    # lookups find them. They were deliberately excluded from `legacy_schemas`
    # above (Step 0 already gave every primitive its own Tier-2-facing surface),
    # but that's an orthogonal, independent use — see the comment on
    # primitive_name_to_id above for why the two don't conflict.
    for (name, schema) in schemas.items():
        if primitives.is_primitive(schema):
            templates_by_name[name] = schema
    owned_by_boolean: set[str] = set()
    parent_of: dict[str, str] = {}
    for (bname, bschema) in placements.items():
        if not isinstance(bschema, BooleanPlacementSchema):
            continue
        for child in bschema.children:
            if child in parent_of:
                raise ValueError(f"Placement '{child}' is listed as a child of both '{parent_of[child]}' and '{bname}'.")
            parent_of[child] = bname
            owned_by_boolean.add(child)
    box_placements: list[tuple[str, BoxSchema, dict[str, str]]] = []
    lattice_schemas = {n: s for (n, s) in placements.items() if isinstance(s, (SquareLatticeSchema, HexLatticeSchema))}
    nested_template_lattices: set[str] = set()
    for (_n, _s) in lattice_schemas.items():
        if _s.template in lattice_schemas:
            nested_template_lattices.add(_s.template)
    for (name, schema) in placements.items():
        if name in owned_by_boolean:
            continue
        if not isinstance(schema, SinglePlacementSchema):
            continue
        tpl = templates_by_name.get(schema.template)
        if not isinstance(tpl, BoxSchema):
            continue
        if getattr(tpl, 'role', 'universe') != 'universe':
            continue
        if _is_rotated(_rotation_of(schema)):
            raise NotImplementedError(f"SinglePlacement '{name}' rotates the outer/universe Box '{schema.template}' — this drives the whole model's coordinate frame and FuelPin axial-bounds sharing, so rotating it isn't 'rotate one object', it's 'rotate the whole model', which isn't supported. Set role: solid on '{schema.template}' if you meant to rotate an ordinary interior box, or remove the rotation here.")
        prev_bot, prev_top = ctx.axial_bot_id, ctx.axial_top_id
        (placed_surfaces, translated_labels) = _place_box(tpl, schema.position(), ctx)
        all_objects.extend(placed_surfaces)
        if box_placements:
            ctx.axial_bot_id = prev_bot
            ctx.axial_top_id = prev_top
        box_placements.append((name, tpl, translated_labels))
    for (name, schema) in placements.items():
        if name in owned_by_boolean:
            continue
        if isinstance(schema, SinglePlacementSchema):
            tpl = templates_by_name.get(schema.template)
            if tpl is None:
                raise ValueError(f"SinglePlacement '{name}' references undefined template '{schema.template}'.")
            rotation = _rotation_of(schema)
            if isinstance(tpl, BoxSchema):
                if getattr(tpl, 'role', 'universe') == 'universe':
                    continue
                pos = schema.position()
                if _is_rotated(rotation):
                    # The outer/universe Box (role="universe", handled by
                    # the box_placements pass above) is excluded from
                    # rotation entirely — it drives the model's whole
                    # coordinate frame and axial-bounds sharing, so tilting
                    # it isn't "rotate one placed object", it's "rotate the
                    # whole model", which is out of scope here. Only
                    # role="solid" Boxes (ordinary interior objects) reach
                    # this branch.
                    (local_surfaces, labels) = _expand_box_surfaces(tpl, ctx)
                    local_surfaces = [Surface(id=s.id, type_=s.type_, params=s.params, boundary_type=BoundaryType.NONE) for s in local_surfaces]
                    local_region = Intersection([Outside(labels['xlo']), Inside(labels['xhi']), Outside(labels['ylo']), Inside(labels['yhi']), Outside(labels['bot']), Inside(labels['top'])])
                    cell = Cell(id=ctx.fresh_id('c'), region=local_region, material_id=tpl.material, name=name)
                    radius = _local_bounding_radius(tpl, schema.template, schemas)
                    all_objects.extend(_place_rotated(list(local_surfaces) + [cell], pos, rotation, radius, ctx, name))
                    continue
                (surfaces, labels) = _expand_box_surfaces(tpl, ctx)
                # Bugfix: a role="solid" Box placed standalone (not a
                # boolean-composite operand, not the universe box) is still
                # interior geometry, never the geometry's outer boundary.
                # Same leak already fixed in _expand_shape_at_origin() for
                # boolean-composite operands — force NONE here too so this
                # schema's boundary_type (default REFLECTIVE) doesn't
                # reflect particles off an interior wall in the OpenMC export.
                surfaces = [Surface(id=s.id, type_=s.type_, params=s.params, boundary_type=BoundaryType.NONE) for s in surfaces]
                (translated, id_map) = _translate(surfaces, pos[0], pos[1], pos[2], ctx)
                tlabels = {k: id_map[v] for (k, v) in labels.items()}
                region = Intersection([Outside(tlabels['xlo']), Inside(tlabels['xhi']), Outside(tlabels['ylo']), Inside(tlabels['yhi']), Outside(tlabels['bot']), Inside(tlabels['top'])])
                cell = Cell(id=ctx.fresh_id('c'), region=region, material_id=tpl.material, name=name)
                all_objects.extend([o for o in translated if isinstance(o, Surface)])
                all_objects.append(cell)
                continue
            if primitives.is_primitive(tpl):
                # Mirrors the role="solid" Box branch just above (and
                # _expand_single_placement_objects' identical branch, used
                # for BooleanPlacement children) — same duplication
                # precedent already established in this function for Box.
                if getattr(tpl, 'material', None) is None:
                    raise ValueError(
                        f"SinglePlacement '{name}' places '{schema.template}' "
                        f"(a {type(tpl).__name__}), which has no `material` "
                        f"set. Add material: <id> to '{schema.template}' to "
                        f"place it as a solid object, or remove this "
                        f"SinglePlacement and reference '{schema.template}' "
                        f"from a Tier-2 Cell region expression instead."
                    )
                pos = schema.position()
                if _is_rotated(rotation):
                    (local_surfaces, local_region) = primitives.expand_primitive(ctx, tpl, schema.template)
                    cell = Cell(id=ctx.fresh_id('c'), region=local_region, material_id=tpl.material, name=name)
                    radius = _local_bounding_radius(tpl, schema.template, schemas)
                    all_objects.extend(_place_rotated(list(local_surfaces) + [cell], pos, rotation, radius, ctx, name))
                    continue
                (surfaces, region) = primitives.expand_primitive(ctx, tpl, schema.template)
                (translated, id_map) = _translate(surfaces, pos[0], pos[1], pos[2], ctx)
                translated_region = _remap_region(region, id_map)
                cell = Cell(id=ctx.fresh_id('c'), region=translated_region, material_id=tpl.material, name=name)
                all_objects.extend([o for o in translated if isinstance(o, Surface)])
                all_objects.append(cell)
                continue
            if templates.is_composite_template(tpl):
                if _is_rotated(rotation):
                    (local_surfaces, local_cells) = templates.expand_template(ctx, tpl, name, (0.0, 0.0, 0.0), force_local_axial=True)
                    radius = _local_bounding_radius(tpl, schema.template, schemas)
                    all_objects.extend(_place_rotated(list(local_surfaces) + list(local_cells), schema.position(), rotation, radius, ctx, name))
                else:
                    (surfaces, cells) = templates.expand_template(ctx, tpl, name, schema.position())
                    all_objects.extend(surfaces)
                    all_objects.extend(cells)
            elif isinstance(tpl, _BOOLEAN_COMPOSITE_TYPES):
                if _is_rotated(rotation):
                    (local_surfaces, local_region) = _expand_shape_at_origin(tpl, schema.template, schemas, ctx)
                    cell = Cell(id=ctx.fresh_id('c'), region=local_region, material_id=tpl.material, name=name)
                    radius = _local_bounding_radius(tpl, schema.template, schemas)
                    all_objects.extend(_place_rotated(list(local_surfaces) + [cell], schema.position(), rotation, radius, ctx, name))
                else:
                    placed = _place_boolean_composite(tpl, schema.template, schema.position(), ctx, schemas, cell_name=name)
                    all_objects.extend(placed)
            else:
                raise TypeError(f"SinglePlacement '{name}' references template of type '{type(tpl).__name__}' which has no placement handler.")
        elif isinstance(schema, BooleanPlacementSchema):
            placed = _place_boolean_placement(schema, name, placements, templates_by_name, schemas, ctx)
            all_objects.extend(placed)
        elif isinstance(schema, (SquareLatticeSchema, HexLatticeSchema)):
            if name in nested_template_lattices:
                continue
            _dispatch_lattice(ctx, schema, name, templates_by_name, lattice_schemas, all_objects, schemas)
    for bname, bschema, blabels in box_placements:
        fill_cell = _build_fill_cell(bschema, blabels, ctx, cell_name=bname or f'fill_{bschema.material}')
        all_objects.append(fill_cell)
    surfaces = [obj for obj in all_objects if isinstance(obj, Surface)]
    cells = [obj for obj in all_objects if isinstance(obj, Cell)]
    return CascadeGeometry(id=str(uuid.uuid4()), name=geom_name, surfaces=surfaces, cells=cells, param_values=ctx.param_values, lattice_instances=ctx.lattice_instances, warnings=ctx.warnings)

def _dispatch_lattice(ctx, schema, name, templates_by_name, lattice_schemas, all_objects, schemas):
    tpl_name = schema.template
    if tpl_name in lattice_schemas:
        inner = lattice_schemas[tpl_name]
        if schema.is_rotated() or inner.is_rotated():
            raise NotImplementedError(f"Nested lattice '{name}' -> '{tpl_name}': rotation on nested (lattice-of-lattice) assemblies isn't supported yet. Rotate a single-level lattice, or remove rotation from one of these two levels.")
        pin_tpl = templates_by_name.get(inner.template)
        if pin_tpl is None:
            raise ValueError(f"Nested lattice '{name}' -> '{tpl_name}' references undefined pin template '{inner.template}'.")
        if not templates.is_composite_template(pin_tpl):
            raise TypeError(f"Nested lattice '{name}': inner template not supported.")
        _expand_nested_lattice(ctx, pin_tpl, inner.template, name, schema.pin_positions(), inner.pin_positions(), all_objects)
        return
    tpl = templates_by_name.get(tpl_name)
    if tpl is None:
        raise ValueError(f"Lattice '{name}' references undefined template '{tpl_name}'.")
    if templates.is_composite_template(tpl):
        if schema.is_rotated():
            _expand_lattice_rotated(ctx, tpl, tpl_name, name, schema, schemas, all_objects)
        else:
            _expand_lattice_with_instancing(ctx, tpl, tpl_name, name, schema.pin_positions(), all_objects)
    else:
        raise TypeError(f"Lattice '{name}': template type not supported in lattices yet.")


def _expand_lattice_rotated(ctx, tpl, prototype_key, lattice_name, schema, schemas, all_objects):
    """Rotated-lattice path: every pin becomes its own fill-cell inside a
    shared 'assembly' universe (pin universes are reused across every
    position that shares the same own-rotation, so a lattice with a
    handful of overridden pins doesn't mint hundreds of duplicate
    universes). The whole assembly universe is then instanced into the
    parent frame by ONE outer fill-cell carrying the lattice-level
    rotation, pivoting about the lattice's own origin/center point.

    This bypasses the LatticeInstance GPU-instancing side-channel (Phase C)
    entirely — rotated lattices fall back to the ordinary flat surfaces/
    cells list for rendering until the viewport's BVH gains per-instance
    rotation support (Phase 3 of the rotation plan). OpenMC's own geometry
    is exact either way; only the viewport's instancing fast path is
    affected.
    """
    positions = schema.pin_positions()
    if not positions:
        return
    pin_rotations = schema.pin_rotations()
    assembly_rotation = schema.assembly_rotation()
    origin = schema.origin_point()
    pin_radius = _local_bounding_radius(tpl, prototype_key, schemas)
    assembly_universe = ctx.fresh_id('u')

    pin_universe_by_rotation: dict[tuple[float, float, float], str] = {}
    assembly_bound = 0.0
    proto_idx = 0
    for i, pos in enumerate(positions):
        local_pos = (pos[0] - origin[0], pos[1] - origin[1], pos[2] - origin[2])
        own_rotation = pin_rotations[i]
        u_id = pin_universe_by_rotation.get(own_rotation)
        if u_id is None:
            (local_surfaces, local_cells) = templates.expand_template(ctx, tpl, f'{lattice_name}_proto{proto_idx}', (0.0, 0.0, 0.0), force_local_axial=True)
            proto_idx += 1
            u_id = ctx.fresh_id('u')
            all_objects.extend(local_surfaces)
            all_objects.extend((Cell(id=c.id, region=c.region, material_id=c.material_id, name=c.name, universe_id=u_id) for c in local_cells))
            pin_universe_by_rotation[own_rotation] = u_id
        confine = Surface(id=ctx.fresh_id('s'), type_=SurfaceType.SPHERE, params={'x0': local_pos[0], 'y0': local_pos[1], 'z0': local_pos[2], 'r': pin_radius})
        all_objects.append(confine)
        all_objects.append(Cell(id=ctx.fresh_id('c'), region=Inside(confine.id), material_id=None, name=f'{lattice_name}_{i}', universe_id=assembly_universe, fill=u_id, translation=local_pos, rotation=own_rotation))
        assembly_bound = max(assembly_bound, math.sqrt(local_pos[0] ** 2 + local_pos[1] ** 2 + local_pos[2] ** 2) + pin_radius)

    assembly_confine = Surface(id=ctx.fresh_id('s'), type_=SurfaceType.SPHERE, params={'x0': 0.0, 'y0': 0.0, 'z0': 0.0, 'r': assembly_bound})
    all_objects.append(assembly_confine)
    all_objects.append(Cell(id=ctx.fresh_id('c'), region=Inside(assembly_confine.id), material_id=None, name=lattice_name, universe_id='0', fill=assembly_universe, translation=origin, rotation=assembly_rotation))

def _expand_lattice_with_instancing(ctx, tpl, prototype_key, lattice_name, positions, all_objects):
    if not positions:
        return
    origin = positions[0]
    prototype_surfaces = None
    prototype_cells = None
    instances = []
    for (i, pos) in enumerate(positions):
        (surfaces, cells) = templates.expand_template(ctx, tpl, f'{lattice_name}_{i}', pos)
        all_objects.extend(surfaces)
        all_objects.extend(cells)
        if i == 0:
            prototype_surfaces = surfaces
            prototype_cells = cells
        instances.append((pos[0] - origin[0], pos[1] - origin[1], pos[2] - origin[2]))
    ctx.lattice_instances.append(LatticeInstance(lattice_name=lattice_name, prototype_key=prototype_key, prototype_surfaces=prototype_surfaces or [], prototype_cells=prototype_cells or [], instances=instances, inner_offsets=[]))

def _expand_nested_lattice(ctx, pin_tpl, pin_template_key, outer_name, outer_positions, inner_positions, all_objects):
    if not outer_positions or not inner_positions:
        return
    inner_origin = inner_positions[0]
    inner_rel = [(p[0] - inner_origin[0], p[1] - inner_origin[1], p[2] - inner_origin[2]) for p in inner_positions]
    outer_origin = outer_positions[0]
    outer_rel = [(p[0] - outer_origin[0], p[1] - outer_origin[1], p[2] - outer_origin[2]) for p in outer_positions]
    prototype_surfaces = None
    prototype_cells = None
    for (ai, apos) in enumerate(outer_positions):
        for (pi, prel) in enumerate(inner_rel):
            world = (apos[0] + prel[0], apos[1] + prel[1], apos[2] + prel[2])
            (surfaces, cells) = templates.expand_template(ctx, pin_tpl, f'{outer_name}_a{ai}_p{pi}', world)
            all_objects.extend(surfaces)
            all_objects.extend(cells)
            if ai == 0 and pi == 0:
                prototype_surfaces = surfaces
                prototype_cells = cells
    ctx.lattice_instances.append(LatticeInstance(lattice_name=outer_name, prototype_key=pin_template_key, prototype_surfaces=prototype_surfaces or [], prototype_cells=prototype_cells or [], instances=outer_rel, inner_offsets=inner_rel))