"""Box schema — a rectangular cuboid template.

Unlike the old BoundingBoxSchema, Box has no position — position comes
from SinglePlacement. z_size replaces z_min/z_max because the template
describes shape, not location.

A BoundingBox is now just:
    my_box:
      type: Box
      ...

    boundary:
      type: SinglePlacement
      template: my_box
      position: [0, 0, 0]
"""

from __future__ import annotations

from pydantic import Field, model_validator

from ...domain.geometry import BoundaryType
from .base import BaseComponentSchema


class BoxSchema(BaseComponentSchema):
    """Rectangular cuboid template — defines shape and boundary condition.

    All dimensions in centimeters. Origin of the box is its geometric
    centre in X and Y, and its bottom face in Z — consistent with how
    SinglePlacement applies the position offset.

    Example:
        my_box:
          type: Box
          x_size: 1.26
          y_size: 1.26
          z_size: 365.76
          material: H2O
          boundary_type: reflective
    """

    x_size: float = Field(default=1.26, gt=0,
        description="Full width in X (cm). Box spans -x_size/2 to +x_size/2 relative to placement position.")
    y_size: float = Field(default=1.26, gt=0,
        description="Full width in Y (cm). Box spans -y_size/2 to +y_size/2 relative to placement position.")
    z_size: float = Field(default=365.76, gt=0,
        description="Full height in Z (cm). Box spans 0 to z_size relative to placement z position.")

    material: str = Field(default="H2O",
        description="Fill material ID for the region inside box but outside inner geometry.")

    boundary_type: BoundaryType = Field(default=BoundaryType.REFLECTIVE,
        description="Boundary condition on all six faces.")

    model_config = {"frozen": True}

    def half_x(self) -> float:
        return self.x_size / 2.0

    def half_y(self) -> float:
        return self.y_size / 2.0