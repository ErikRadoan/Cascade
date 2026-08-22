"""Box schema — a rectangular cuboid template.

Position comes from SinglePlacement. z_size replaces z_min/z_max because the
template describes shape, not location.

role:
  universe — outer / moderator box: registers axial bounds for FuelPins and
             builds a fill cell (box interior minus pin outermost surfaces).
  solid    — ordinary solid region with the box material; does not provide
             axial bounds and does not produce a fill cell.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from ...domain.geometry import BoundaryType
from .base import BaseComponentSchema


class BoxSchema(BaseComponentSchema):
    """Rectangular cuboid template — shape, material, boundary, and role."""

    x_size: float = Field(default=1.26, gt=0,
        description="Full width in X (cm). Box spans -x_size/2 to +x_size/2 relative to placement position.")
    y_size: float = Field(default=1.26, gt=0,
        description="Full width in Y (cm). Box spans -y_size/2 to +y_size/2 relative to placement position.")
    z_size: float = Field(default=365.76, gt=0,
        description="Full height in Z (cm). Box spans 0 to z_size relative to placement z position.")

    material: str = Field(default="H2O",
        description="Material ID for the box region (fill for universe, solid body for solid).")

    boundary_type: BoundaryType = Field(default=BoundaryType.REFLECTIVE,
        description="Boundary condition on all six faces.")

    role: Literal["universe", "solid"] = Field(
        default="universe",
        description=(
            "universe: outer box providing FuelPin axial bounds and a moderator fill cell. "
            "solid: independent solid box (no axial registration, no fill cell)."
        ),
    )

    model_config = {"frozen": True}

    def half_x(self) -> float:
        return self.x_size / 2.0

    def half_y(self) -> float:
        return self.y_size / 2.0

    def is_universe(self) -> bool:
        return self.role == "universe"
