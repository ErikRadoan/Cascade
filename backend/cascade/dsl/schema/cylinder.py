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

    model_config = {"frozen": True}


class CylinderYSchema(BaseComponentSchema):
    """Cylinder with axis parallel to Y (Surface.type_ = CYLINDER_Y)."""

    radius: float = Field(..., gt=0, description="Cylinder radius (cm).")
    x: float = Field(0.0, description="X of the cylinder's centerline (cm).")
    z: float = Field(0.0, description="Z of the cylinder's centerline (cm).")
    boundary_type: BoundaryType = Field(default=BoundaryType.NONE)

    model_config = {"frozen": True}


class CylinderZSchema(BaseComponentSchema):
    """Cylinder with axis parallel to Z (Surface.type_ = CYLINDER_Z).

    This is the axis used by essentially every pin-cell/lattice geometry
    today (FuelPin's radial layers, etc.) — the other two orientations
    exist for completeness but are rarely needed in reactor geometry.
    """

    radius: float = Field(..., gt=0, description="Cylinder radius (cm).")
    x: float = Field(0.0, description="X of the cylinder's centerline (cm).")
    y: float = Field(0.0, description="Y of the cylinder's centerline (cm).")
    boundary_type: BoundaryType = Field(default=BoundaryType.NONE)

    model_config = {"frozen": True}
