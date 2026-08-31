"""Geometry expander — n-ary Union, Box offset, boundary force, Box.role.

Full documented version: project artifacts/expander.py.
"""

from __future__ import annotations
import uuid
from dataclasses import dataclass, field
from typing import Any
from ..domain.geometry import BoundaryType, CascadeGeometry, Cell, Complement, Inside, Intersection, LatticeInstance, Outside, Region, Surface, SurfaceType, Union, region_from_yaml_dict
from . import primitives
from . import templates
from .schema.base import BaseComponentSchema
from .schema.boolean import BooleanPlacementSchema, IntersectionSchema, SubtractionSchema, UnionSchema
from .schema.box import BoxSchema
from .schema.cell import CellSchema
from .schema.fuel_pin import FuelPinSchema
from .schema.lattice import HexLatticeSchema, SquareLatticeSchema
from .schema.single_placement import SinglePlacementSchema

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

def _expand_single_placement_objects(schema: SinglePlacementSchema, name: str, templates_by_name: dict, schemas: dict, ctx: Context, extra_offset: tuple[float, float, float]=(0.0, 0.0, 0.0)) -> list:
    tpl = templates_by_name.get(schema.template)
    if tpl is None:
        raise ValueError(f"SinglePlacement '{name}' references undefined template '{schema.template}'.")
    (px, py, pz) = schema.position()
    pos = (px + extra_offset[0], py + extra_offset[1], pz + extra_offset[2])
    if isinstance(tpl, BoxSchema):
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
        (surfaces, region) = primitives.expand_primitive(ctx, tpl, schema.template)
        (translated, id_map) = _translate(surfaces, pos[0], pos[1], pos[2], ctx)
        translated_surfaces = [o for o in translated if isinstance(o, Surface)]
        translated_region = _remap_region(region, id_map)
        cell = Cell(id=ctx.fresh_id('c'), region=translated_region, material_id=tpl.material, name=name)
        return translated_surfaces + [cell]
    if templates.is_composite_template(tpl):
        (surfaces, cells) = templates.expand_template(ctx, tpl, name, pos)
        return list(surfaces) + list(cells)
    if isinstance(tpl, _BOOLEAN_COMPOSITE_TYPES):
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
            objs = _expand_single_placement_objects(child, child_name, templates_by_name, schemas, ctx, residual)
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
            if isinstance(tpl, BoxSchema):
                if getattr(tpl, 'role', 'universe') == 'universe':
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
                pos = schema.position()
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
                (surfaces, region) = primitives.expand_primitive(ctx, tpl, schema.template)
                pos = schema.position()
                (translated, id_map) = _translate(surfaces, pos[0], pos[1], pos[2], ctx)
                translated_region = _remap_region(region, id_map)
                cell = Cell(id=ctx.fresh_id('c'), region=translated_region, material_id=tpl.material, name=name)
                all_objects.extend([o for o in translated if isinstance(o, Surface)])
                all_objects.append(cell)
                continue
            if templates.is_composite_template(tpl):
                (surfaces, cells) = templates.expand_template(ctx, tpl, name, schema.position())
                all_objects.extend(surfaces)
                all_objects.extend(cells)
            elif isinstance(tpl, _BOOLEAN_COMPOSITE_TYPES):
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
            _dispatch_lattice(ctx, schema, name, templates_by_name, lattice_schemas, all_objects)
    for bname, bschema, blabels in box_placements:
        fill_cell = _build_fill_cell(bschema, blabels, ctx, cell_name=bname or f'fill_{bschema.material}')
        all_objects.append(fill_cell)
    surfaces = [obj for obj in all_objects if isinstance(obj, Surface)]
    cells = [obj for obj in all_objects if isinstance(obj, Cell)]
    return CascadeGeometry(id=str(uuid.uuid4()), name=geom_name, surfaces=surfaces, cells=cells, param_values=ctx.param_values, lattice_instances=ctx.lattice_instances, warnings=ctx.warnings)

def _dispatch_lattice(ctx, schema, name, templates_by_name, lattice_schemas, all_objects):
    tpl_name = schema.template
    if tpl_name in lattice_schemas:
        inner = lattice_schemas[tpl_name]
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
        _expand_lattice_with_instancing(ctx, tpl, tpl_name, name, schema.pin_positions(), all_objects)
    else:
        raise TypeError(f"Lattice '{name}': template type not supported in lattices yet.")

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