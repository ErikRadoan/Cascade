"""Sphere primitive schema — Tier-1 primitive (geometry-restructuring-
plan.md Phase A).

Geometry-first: material is optional (schema/cell.py's Tier-2 Cell can
reference this sphere by its YAML key in a region expression regardless).
Set `material` directly on the schema only when you also want this Sphere
placeable as a solid object via SinglePlacement (expander.py promotes it
to a Cell, matching Box's role="solid" pattern) — see cone.py/torus.py for
the same convention on the other bounded-volume primitives.
"""

from __future__ import annotations

from pydantic import Field

from ...domain.geometry import BoundaryType
from .base import BaseComponentSchema


class SphereSchema(BaseComponentSchema):
    """A single sphere surface (Surface.type_ = SPHERE).

    Example:
        s_sphere:
          type: Sphere
          radius: 1.0
          x: 0.0
          y: 0.0
          z: 0.0
    """

    radius: float = Field(..., gt=0, description="Sphere radius (cm).")
    x: float = Field(0.0, description="X of the sphere's center (cm).")
    y: float = Field(0.0, description="Y of the sphere's center (cm).")
    z: float = Field(0.0, description="Z of the sphere's center (cm).")
    boundary_type: BoundaryType = Field(default=BoundaryType.NONE)
    material: str | None = Field(
        default=None,
        description=(
            "Optional. If set, this Sphere can be placed as a solid Cell "
            "via SinglePlacement (see expander._expand_single_placement_objects), "
            "exactly like Box's role=\"solid\". Leave unset to use this "
            "Sphere as pure geometry referenced from a Tier-2 Cell region "
            "expression or a boolean composite operand instead."
        ),
    )

    model_config = {"frozen": True}
