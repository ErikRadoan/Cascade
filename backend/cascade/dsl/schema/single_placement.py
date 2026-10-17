"""SinglePlacement schema — places one instance of a template at a position."""

from __future__ import annotations

from pydantic import Field, model_validator

from .base import BaseComponentSchema


class SinglePlacementSchema(BaseComponentSchema):
    """Places exactly one instance of a template at an explicit position.

    The template field references the name of any template defined earlier
    in the same YAML document (FuelPin, Box, etc.).

    All position values are in centimeters and can be swept independently:

        my_rod:
          type: SinglePlacement
          template: my_fuel_pin
          x: sweep(-5 to 5, step=1)
          y: 0.0
          z: 0.0

    Example — place a pin at the origin:
        center_pin:
          type: SinglePlacement
          template: my_fuel_pin
          x: 0.0
          y: 0.0
          z: 0.0

    Example — place a bounding box:
        boundary:
          type: SinglePlacement
          template: my_box
          x: 0.0
          y: 0.0
          z: 0.0
    """

    template: str = Field(
        ...,
        description="Name of the template to place. Must be defined earlier in the same YAML.",
    )

    # Position components as separate scalars so each can be individually swept
    x: float = Field(default=0.0, description="X position of the template origin (cm).")
    y: float = Field(default=0.0, description="Y position of the template origin (cm).")
    z: float = Field(default=0.0, description="Z position of the template origin (cm).")

    # Rotation — intrinsic XYZ Euler angles in degrees, OpenMC's native
    # cell-rotation convention. (0, 0, 0) is the identity and takes the
    # existing zero-overhead translate-only expansion path (see
    # expander._rotation_of / _place_rotated); only placements that actually
    # set one of these incur the local-universe/fill-cell machinery.
    rx: float = Field(default=0.0, description="Rotation about X (degrees).")
    ry: float = Field(default=0.0, description="Rotation about Y (degrees).")
    rz: float = Field(default=0.0, description="Rotation about Z (degrees).")

    model_config = {"frozen": True}

    def position(self) -> tuple[float, float, float]:
        return (self.x, self.y, self.z)

    def rotation(self) -> tuple[float, float, float]:
        return (self.rx, self.ry, self.rz)

    def is_rotated(self) -> bool:
        return self.rx != 0.0 or self.ry != 0.0 or self.rz != 0.0