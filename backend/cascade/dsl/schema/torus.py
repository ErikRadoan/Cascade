"""Torus primitive schema — Tier-1 primitive (geometry-restructuring-
plan.md Phase A). Describes ONE ring-torus surface whose axis of
revolution is parallel to Z (Surface.type_ = TORUS) — the domain model
and OpenMC adapter only define the Z-axis torus today (matching OpenMC's
own z-torus/x-torus/y-torus family; only z-torus is wired through
adapters/openmc_adapter.py at present).

Carries its own position, like Sphere/Cylinder*/Plane* — no external
placement/translation step for a standalone declaration.
"""

from __future__ import annotations

from pydantic import Field

from ...domain.geometry import BoundaryType
from .base import BaseComponentSchema


class TorusSchema(BaseComponentSchema):
    """Ring torus with axis of revolution parallel to Z (Surface.type_ =
    TORUS).

    OpenMC's z-torus equation is
        ((sqrt((x-x0)^2 + (y-y0)^2) - a)^2) / b^2 + (z-z0)^2 / c^2 = 1
    where `a` is the distance from the Z axis to the center of the tube,
    and `b`/`c` are the tube's semi-axes in the radial (x-y) and Z
    directions respectively. This schema exposes those as `ring_radius`
    (a) and `tube_radius` (b), with an optional `tube_height` (c) for an
    elliptical tube cross-section — when omitted, `tube_height` defaults
    to `tube_radius` (a circular tube), which is what most reactor
    geometries (e.g. a toroidal vacuum vessel cross-section) want.

    Example:
        t_vessel:
          type: Torus
          ring_radius: 620.0
          tube_radius: 120.0
          z: 0.0
    """

    ring_radius: float = Field(
        ..., gt=0,
        description="Distance from the Z axis to the center of the tube (cm).",
    )
    tube_radius: float = Field(
        ..., gt=0,
        description="Tube semi-axis in the radial (x-y) direction (cm).",
    )
    tube_height: float | None = Field(
        None, gt=0,
        description=(
            "Tube semi-axis along Z (cm). Defaults to `tube_radius` for a "
            "circular tube cross-section (elliptical tube otherwise)."
        ),
    )
    x: float = Field(0.0, description="X of the torus center (cm).")
    y: float = Field(0.0, description="Y of the torus center (cm).")
    z: float = Field(0.0, description="Z of the plane the ring lies in (cm).")
    boundary_type: BoundaryType = Field(default=BoundaryType.NONE)
    material: str | None = Field(
        default=None,
        description=(
            "Optional. If set, this Torus can be placed as a solid Cell via "
            "SinglePlacement (see expander._expand_single_placement_objects). "
            "Torus is the one bounded-by-default Tier-1 primitive — no "
            "additional planes needed to get a finite solid."
        ),
    )

    model_config = {"frozen": True}
