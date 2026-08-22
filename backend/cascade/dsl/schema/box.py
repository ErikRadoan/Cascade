"""Box schema — a rectangular cuboid template.

Position for scene placement comes from SinglePlacement. Optional x/y/z are
used only when the Box is a *boolean composite operand* (local offset relative
to the composite origin).

role:
  universe — outer / moderator box: registers axial bounds for FuelPins and
             builds a fill cell.
  solid    — ordinary solid region; no axial registration, no fill cell.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from ...domain.geometry import BoundaryType
from .base import BaseComponentSchema


class BoxSchema(BaseComponentSchema):
    """Rectangular cuboid template — shape, material, boundary, role, local offset."""

    x_size: float = Field(default=1.26, gt=0,
        description="Full width in X (cm).")
    y_size: float = Field(default=1.26, gt=0,
        description="Full width in Y (cm).")
    z_size: float = Field(default=365.76, gt=0,
        description="Full height in Z (cm).")

    material: str = Field(default="H2O",
        description="Material ID for the box region.")

    boundary_type: BoundaryType = Field(default=BoundaryType.REFLECTIVE,
        description="Boundary condition on all six faces.")

    role: Literal["universe", "solid"] = Field(
        default="universe",
        description=(
            "universe: outer box providing FuelPin axial bounds and a moderator fill cell. "
            "solid: independent solid box (no axial registration, no fill cell)."
        ),
    )

    # Local offset when used as a boolean operand (template Union/etc.).
    x: float = Field(default=0.0, description="Local X offset when used as a boolean operand.")
    y: float = Field(default=0.0, description="Local Y offset when used as a boolean operand.")
    z: float = Field(default=0.0, description="Local Z offset when used as a boolean operand.")

    model_config = {"frozen": True}

    def half_x(self) -> float:
        return self.x_size / 2.0

    def half_y(self) -> float:
        return self.y_size / 2.0

    def is_universe(self) -> bool:
        return self.role == "universe"
