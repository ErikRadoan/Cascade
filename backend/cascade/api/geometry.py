"""Geometry domain models."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, StrEnum
from abc import ABC


class SurfaceType(Enum):
    """Surface geometry type."""
    PLANE_X = "plane_x"
    PLANE_Y = "plane_y"
    PLANE_Z = "plane_z"
    CYLINDER_X = "cylinder_x"
    CYLINDER_Y = "cylinder_y"
    CYLINDER_Z = "cylinder_z"
    SPHERE = "sphere"
    CONE_Z = "cone_z"
    TORUS = "torus"

class BoundaryType(StrEnum):
    NONE       = "none"        # interior surface, no special treatment
    VACUUM     = "vacuum"      # particles crossing are killed
    REFLECTIVE = "reflective"  # particles bounce back
    PERIODIC   = "periodic"    # used for lattice symmetry

def region_to_json(region: Region) -> dict:
    """Serialize a Region tree to nested JSON, for client-side point
    classification (geometry-plot rasterization) — the structured
    counterpart to Region.__repr__()'s flattened string, which Cell.to_dict()
    already uses for DB persistence. Kept separate from to_dict() so nothing
    about existing storage/parsing (job_repository.py's _region_from_str)
    has to change.
    """
    if isinstance(region, Inside):
        return {"op": "inside", "surface": region.surface_id}
    if isinstance(region, Outside):
        return {"op": "outside", "surface": region.surface_id}
    if isinstance(region, Intersection):
        return {"op": "and", "items": [region_to_json(r) for r in region.regions]}
    if isinstance(region, Union):
        return {"op": "or", "items": [region_to_json(r) for r in region.regions]}
    if isinstance(region, Complement):
        return {"op": "not", "item": region_to_json(region.region)}
    raise TypeError(f"Unknown Region type: {type(region).__name__}")


def region_from_yaml_dict(d: dict, name_to_id: dict[str, str]) -> Region:
    """Inverse of region_to_json() — the DSL-facing counterpart used by
    schema/cell.py's CellSchema.region.

    Reconstructs a Region tree from the nested-dict shape CellSchema.region
    validates for shape (see that module), resolving each `surface` NAME
    reference to its Surface.id via `name_to_id`. `name_to_id` is built by
    the expander from every Tier-1 primitive's authored YAML key once all
    primitives in the document have been expanded — see expander.py's
    ordering note (a Cell may reference a primitive declared later in the
    file; this function itself doesn't care about declaration order at
    all, it just looks names up in a dict that's already complete by the
    time it's called).

    Kept in this module, next to region_to_json(), so the two stay in sync
    in one place (geometry-restructuring-plan.md §3.2) rather than one
    living here and its inverse living in the DSL layer.

    Raises:
        ValueError: for an unknown `op`, or a `surface` name not present in
        `name_to_id` — never a bare KeyError, so callers (e.g.
        POST /geometry/validate) can surface a clean validation error
        instead of a 500.
    """
    if not isinstance(d, dict):
        raise ValueError(f"Region node must be a mapping, got {type(d).__name__}.")

    op = d.get("op")

    if op in ("inside", "outside"):
        surface_name = d.get("surface")
        surface_id = name_to_id.get(surface_name)
        if surface_id is None:
            raise ValueError(
                f"Region references unknown surface '{surface_name}'. "
                f"Known surfaces: {sorted(name_to_id)}."
            )
        return Inside(surface_id) if op == "inside" else Outside(surface_id)

    if op == "and":
        items = d.get("items") or []
        return Intersection([region_from_yaml_dict(i, name_to_id) for i in items])

    if op == "or":
        items = d.get("items") or []
        return Union([region_from_yaml_dict(i, name_to_id) for i in items])

    if op == "not":
        item = d.get("item")
        if item is None:
            raise ValueError("A 'not' region node requires an 'item'.")
        return Complement(region_from_yaml_dict(item, name_to_id))

    raise ValueError(
        f"Unknown region op '{op}'. Must be one of: inside, outside, and, or, not."
    )


class Region(ABC):
    """Base class for CSG region expressions."""

    def __repr__(self) -> str:
        """String representation for adapters."""
        raise NotImplementedError


@dataclass(slots=True)
class Inside(Region):
    """Inside a surface (negative side)."""
    surface_id: str

    def __repr__(self) -> str:
        return f"-{self.surface_id}"


@dataclass(slots=True)
class Outside(Region):
    """Outside a surface (positive side)."""
    surface_id: str

    def __repr__(self) -> str:
        return f"+{self.surface_id}"


@dataclass(slots=True)
class Union(Region):
    """Union of regions (OR)."""
    regions: list[Region] = field(default_factory=list)

    def __repr__(self) -> str:
        if not self.regions:
            return ""
        inner = " : ".join(str(r) for r in self.regions)
        return f"({inner})"


@dataclass(slots=True)
class Intersection(Region):
    """Intersection of regions (AND)."""
    regions: list[Region] = field(default_factory=list)

    def __repr__(self) -> str:
        if not self.regions:
            return ""
        inner = " ".join(str(r) for r in self.regions)
        return f"({inner})"


@dataclass(slots=True)
class Complement(Region):
    """Complement of a region (NOT)."""
    region: Region

    def __repr__(self) -> str:
        return f"~({self.region})"


@dataclass(slots=True)
class Surface:
    """Pure geometric surface with no material."""
    id: str
    type_: SurfaceType
    params: dict[str, float | int | str | bool] = field(default_factory=dict)
    boundary_type: BoundaryType     = BoundaryType.NONE

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "type": self.type_.value,
            "params": dict(self.params),
            "boundary_type": self.boundary_type.value,
        }


@dataclass(slots=True)
class Cell:
    """Region of space with a material."""
    id: str
    region: Region
    material_id: str | None = None
    name: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "region": str(self.region),
            "material_id": self.material_id,
            "name": self.name,
        }


# ---------------------------------------------------------------------------
# Lattice instancing (CSG_VIEWER_SCALING_PLAN.md Phase C)
#
# Additive, side-channel data captured by dsl/expander.py alongside its
# existing flat surfaces/cells expansion — NOT a replacement for the flat
# form. The flat `CascadeGeometry.surfaces`/`.cells` lists still contain
# every pin, fully translated, exactly as before (OpenMC/Serpent2 XML
# export depends on that and is out of scope for this change — see the
# plan doc's §4.2). This is purely extra information for consumers (today:
# the CSG viewer) that want to know "these N cells are actually one
# prototype repeated N times" without re-deriving it via duplicate
# detection.
#
# `prototype_surfaces`/`prototype_cells` are pin/instance index 0's own
# translated surfaces and cells (i.e. the flat form already contains
# them — this does not duplicate geometry, it's pointer-equivalent data
# in a different shape, though as dataclasses here rather than a shared
# reference since CascadeGeometry.to_dict()/from_dict() round-trips
# through plain dicts anyway).
#
# `instances` are (x, y, z) offsets *relative to instance 0's own
# position* — i.e. instances[0] is always (0.0, 0.0, 0.0). Plain
# translation only: neither SquareLatticeSchema nor HexLatticeSchema
# varies orientation per pin today (see dsl/schema/lattice.py's
# pin_positions() — both return bare (x, y, z) tuples), so there is no
# rotation component. If a future lattice schema introduces per-pin
# rotation, add it here as a fourth element rather than a new field, to
# keep one lattice = one instances list.
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class LatticeInstance:
    """One lattice's prototype + every instance placement of it.

    "Prototype" = the single template every position in this lattice was
    expanded from (SquareLatticeSchema/HexLatticeSchema currently support
    exactly one `template` per lattice — no heterogeneous fill/guide-tube
    support in the schema today, so this is always ONE prototype per
    lattice, never a dict keyed by position; revisit this shape if that
    changes).
    """
    lattice_name:        str
    prototype_key:        str                              # = schema.template
    prototype_surfaces:   list[Surface] = field(default_factory=list)
    prototype_cells:       list[Cell]    = field(default_factory=list)
    # (x, y, z) offsets relative to instance 0's own position.
    # instances[0] == (0.0, 0.0, 0.0) always.
    instances:             list[tuple[float, float, float]] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "lattice_name":       self.lattice_name,
            "prototype_key":      self.prototype_key,
            "prototype_surfaces": [s.to_dict() for s in self.prototype_surfaces],
            "prototype_cells":    [c.to_dict() for c in self.prototype_cells],
            "instances":          [list(p) for p in self.instances],
        }


@dataclass(slots=True)
class CascadeGeometry:
    """Complete geometry: surfaces + cells."""
    id: str
    name: str
    surfaces: list[Surface] = field(default_factory=list)
    cells: list[Cell] = field(default_factory=list)
    param_values: dict[str, float] = field(default_factory=dict)
    # Phase C — see LatticeInstance docstring above. Empty for geometry
    # with no lattice placements (single-shape edits, non-lattice
    # components) — consumers must treat this as an optional accelerator,
    # not assume it's always populated, and fall back to the flat
    # surfaces/cells form when it's empty.
    lattice_instances: list[LatticeInstance] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "name": self.name,
            "surfaces": [surface.to_dict() for surface in self.surfaces],
            "cells": [cell.to_dict() for cell in self.cells],
            "param_values": self.param_values,
            "lattice_instances": [li.to_dict() for li in self.lattice_instances],
        }

