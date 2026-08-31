"""Cone primitive schema — Tier-1 primitive (geometry-restructuring-
plan.md Phase A). Describes ONE double-napped cone surface whose axis is
parallel to Z (Surface.type_ = CONE_Z) — the OpenMC/domain model doesn't
define X- or Y-axis cone variants today, matching upstream OpenMC's own
z-cone-only convenience surface (arbitrary-axis cones need the general
quadric surface, which this codebase doesn't model at all yet).

Carries its own position, like Sphere/Cylinder*/Plane* — no external
placement/translation step for a standalone declaration.
"""

from __future__ import annotations

from pydantic import Field

from ...domain.geometry import BoundaryType
from .base import BaseComponentSchema


class ConeZSchema(BaseComponentSchema):
    """Double-napped cone with axis parallel to Z (Surface.type_ = CONE_Z).

    OpenMC's z-cone equation is
        (x - x0)^2 + (y - y0)^2 = r2 * (z - z0)^2
    where `r2` is the SQUARE of the cone's radial slope dr/dz. This schema
    exposes the un-squared slope as `radius_slope` so authors reason about
    a physical quantity (dr/dz, or equivalently tan(half-angle)) rather
    than a pre-squared OpenMC-internal one; the expander squares it exactly
    once, at the surface's authored position (see primitives.py).

    `Inside` means inside the cone volume on BOTH nappes (the region is
    unbounded along Z in both directions) — the same "sign of the implicit
    function" convention every other primitive here uses. Combine with
    PlaneZ bounds via a Cell region to keep only one nappe or a finite
    section, exactly like Cylinder* needs PlaneZ bounds for a finite pin.

    Example:
        c_cone:
          type: ConeZ
          radius_slope: 0.5
          x: 0.0
          y: 0.0
          z: 10.0
    """

    radius_slope: float = Field(
        ..., gt=0,
        description=(
            "Radial slope dr/dz of the cone (dimensionless); the cone's "
            "half-angle is atan(radius_slope). Squared internally to "
            "OpenMC's r2 parameter."
        ),
    )
    x: float = Field(0.0, description="X of the cone apex (cm).")
    y: float = Field(0.0, description="Y of the cone apex (cm).")
    z: float = Field(0.0, description="Z of the cone apex (cm).")
    boundary_type: BoundaryType = Field(default=BoundaryType.NONE)
    material: str | None = Field(
        default=None,
        description=(
            "Optional. If set, this ConeZ can be placed as a solid Cell via "
            "SinglePlacement (see expander._expand_single_placement_objects). "
            "Note the cone is infinite along its axis (both nappes) unless "
            "bounded by additional planes in a Tier-2 Cell region expression "
            "instead."
        ),
    )

    model_config = {"frozen": True}
