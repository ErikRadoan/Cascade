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


@dataclass(slots=True)
class CascadeGeometry:
    """Complete geometry: surfaces + cells."""
    id: str
    name: str
    surfaces: list[Surface] = field(default_factory=list)
    cells: list[Cell] = field(default_factory=list)
    param_values: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "name": self.name,
            "surfaces": [surface.to_dict() for surface in self.surfaces],
            "cells": [cell.to_dict() for cell in self.cells],
            "param_values": self.param_values,
        }
