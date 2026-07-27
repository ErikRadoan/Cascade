"""Plane primitive schemas — Tier-1 primitives (geometry-restructuring-
plan.md Phase A). Each describes exactly ONE axis-aligned half-space plane
surface, carrying its own offset directly — unlike Box, which has no
position and must be wrapped in a SinglePlacement, a Plane* primitive is
positioned exactly like a real OpenMC surface is.

See dsl/primitives.py for the PrimitiveExpander implementations that turn
these into domain.geometry.Surface objects, and dsl/schema/cell.py for how
a Cell references one of these by name.
"""

from __future__ import annotations

from pydantic import Field

from ...domain.geometry import BoundaryType
from .base import BaseComponentSchema


class PlaneXSchema(BaseComponentSchema):
    """A single x=const half-space plane (Surface.type_ = PLANE_X).

    Example:
        s_xlo:
          type: PlaneX
          x: -5.0
          boundary_type: vacuum
    """

    x: float = Field(0.0, description="X offset of the plane (cm).")
    boundary_type: BoundaryType = Field(
        default=BoundaryType.NONE,
        description="Boundary condition on this surface. Defaults to an interior surface.",
    )

    model_config = {"frozen": True}


class PlaneYSchema(BaseComponentSchema):
    """A single y=const half-space plane (Surface.type_ = PLANE_Y)."""

    y: float = Field(0.0, description="Y offset of the plane (cm).")
    boundary_type: BoundaryType = Field(default=BoundaryType.NONE)

    model_config = {"frozen": True}


class PlaneZSchema(BaseComponentSchema):
    """A single z=const half-space plane (Surface.type_ = PLANE_Z)."""

    z: float = Field(0.0, description="Z offset of the plane (cm).")
    boundary_type: BoundaryType = Field(default=BoundaryType.NONE)

    model_config = {"frozen": True}
