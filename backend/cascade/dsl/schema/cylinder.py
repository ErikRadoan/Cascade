"""Cylinder primitive schemas — Tier-1 primitives (geometry-restructuring-
plan.md Phase A). Each describes exactly ONE infinite cylinder surface
whose axis is parallel to the named coordinate axis; the two remaining
coordinates fix the cylinder's centerline. Each carries its own position —
no external placement/translation step, unlike the legacy FuelPin/Box
templates.
"""

from __future__ import annotations

from pydantic import Field

from ...domain.geometry import BoundaryType
from .base import BaseComponentSchema


class CylinderXSchema(BaseComponentSchema):
    """Cylinder with axis parallel to X (Surface.type_ = CYLINDER_X)."""

    radius: float = Field(..., gt=0, description="Cylinder radius (cm).")
    y: float = Field(0.0, description="Y of the cylinder's centerline (cm).")
    z: float = Field(0.0, description="Z of the cylinder's centerline (cm).")
    boundary_type: BoundaryType = Field(default=BoundaryType.NONE)
    material: str | None = Field(
        default=None,
        description=(
            "Optional. If set, this Cylinder can be placed as a solid Cell "
            "via SinglePlacement (see expander._expand_single_placement_objects). "
            "Note the cylinder is infinite along its axis unless bounded by "
            "additional planes in a Tier-2 Cell region expression instead."
        ),
    )

    model_config = {"frozen": True}


class CylinderYSchema(BaseComponentSchema):
    """Cylinder with axis parallel to Y (Surface.type_ = CYLINDER_Y)."""

    radius: float = Field(..., gt=0, description="Cylinder radius (cm).")
    x: float = Field(0.0, description="X of the cylinder's centerline (cm).")
    z: float = Field(0.0, description="Z of the cylinder's centerline (cm).")
    boundary_type: BoundaryType = Field(default=BoundaryType.NONE)
    material: str | None = Field(
        default=None,
        description=(
            "Optional. If set, this Cylinder can be placed as a solid Cell "
            "via SinglePlacement (see expander._expand_single_placement_objects). "
            "Note the cylinder is infinite along its axis unless bounded by "
            "additional planes in a Tier-2 Cell region expression instead."
        ),
    )

    model_config = {"frozen": True}


class CylinderZSchema(BaseComponentSchema):
    """Cylinder with axis parallel to Z (Surface.type_ = CYLINDER_Z).

    This is the axis used by essentially every pin-cell/lattice geometry
    today (FuelPin's radial layers, etc.) — the other two orientations
    exist for completeness but are rarely needed in reactor geometry.

    SHAPE ONLY, NO POSITION. Like Box and FuelPin (and unlike the other
    Tier-1 primitives, for now), this template carries no x/y: WHERE the
    cylinder is, and how it is rotated, belongs to the SinglePlacement that
    places it. (It used to carry its own x/y, which stacked on top of the
    placement's x/y, so the same object had two position knobs.) A stray
    x/y in an old document is ignored, exactly as Box.x/y/z are.

    FINITE vs INFINITE. With `height` set the cylinder is a finite solid:
    centred on the placement's x/y and standing on z = 0 (z from 0 to
    `height`), the same convention as Box and FuelPin. Only a finite
    cylinder can be rotated, because a rotated placement needs a bounding
    volume to confine its local universe. Without `height` it is the bare
    infinite surface: still usable as a Tier-2 Cell region operand and as a
    boolean operand bounded by other surfaces, but a standalone placement of
    it cannot be rotated.
    """

    radius: float = Field(..., gt=0, description="Cylinder radius (cm).")
    height: float | None = Field(
        None, gt=0,
        description=(
            "Optional. Height along Z (cm). Set it to make a finite solid "
            "(z from 0 to height, centred on the placement's x/y) that can be "
            "rotated. Leave unset for an infinite cylinder."
        ),
    )
    boundary_type: BoundaryType = Field(
        default=BoundaryType.NONE,
        description="Boundary condition. A finite cylinder applies it to its end caps too.",
    )
    material: str | None = Field(
        default=None,
        description=(
            "Optional. If set, this Cylinder can be placed as a solid Cell "
            "via SinglePlacement (see expander._expand_single_placement_objects). "
            "Note the cylinder is infinite along its axis unless bounded by "
            "additional planes in a Tier-2 Cell region expression instead."
        ),
    )

    model_config = {"frozen": True}
