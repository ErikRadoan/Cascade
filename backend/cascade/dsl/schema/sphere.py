"""Sphere primitive schema — Tier-1 primitive (geometry-restructuring-
plan.md Phase A).

Pure geometry, no material — exactly like a real OpenMC surface. Material
assignment happens one layer up, in a Tier-2 Cell (schema/cell.py), which
references this sphere by its YAML key inside a region expression.
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

    model_config = {"frozen": True}
