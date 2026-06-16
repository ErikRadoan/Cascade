"""BoundingBox schema — defines the outer boundary of a simulation problem.

A BoundingBox is a rectangular cuboid that surrounds all other geometry.
Its six surfaces carry a boundary_type that tells OpenMC what to do with
particles that reach the edge of the problem:

    reflective — particle bounces back (simulates infinite lattice)
    vacuum     — particle is killed (open geometry, leakage allowed)
    periodic   — particle wraps around (used for repeating lattices)

The fill material occupies the region inside the box but outside all
other registered geometry (fuel pins, rods, etc.). For a PWR pin cell
this is typically water (H2O).

Typical use — single fuel pin in infinite water lattice:

    boundary:
      type: BoundingBox
      x_size: 1.26
      y_size: 1.26
      z_min: 0.0
      z_max: 365.76
      material: H2O
      boundary_type: reflective

The x_size and y_size of 1.26 cm is the standard PWR pin pitch —
this models one pin cell in an infinite square lattice.
"""

from __future__ import annotations

from pydantic import Field, model_validator

from ...domain.geometry import BoundaryType
from .base import BaseComponentSchema


class BoundingBoxSchema(BaseComponentSchema):
    """Rectangular outer boundary for a simulation problem.

    All six bounding surfaces receive the same boundary_type.
    The fill material fills the region between the inner geometry
    and the box walls.

    All dimensions in centimeters.
    """

    # --- Lateral extents (full width, centred on origin) ---
    x_size: float = Field(
        default=1.26,
        gt=0,
        description=(
            "Full width of the box in X (cm). "
            "Centred on origin: box spans -x_size/2 to +x_size/2. "
            "Default is standard PWR pin pitch."
        ),
    )
    y_size: float = Field(
        default=1.26,
        gt=0,
        description=(
            "Full width of the box in Y (cm). "
            "Centred on origin: box spans -y_size/2 to +y_size/2."
        ),
    )

    # --- Axial extents (absolute Z coordinates) ---
    z_min: float = Field(
        default=0.0,
        description="Bottom Z coordinate of the box (cm).",
    )
    z_max: float = Field(
        default=365.76,
        description=(
            "Top Z coordinate of the box (cm). "
            "Default matches standard PWR active fuel height."
        ),
    )

    # --- Fill material ---
    material: str = Field(
        default="H2O",
        description=(
            "Material ID for the region inside the box but outside "
            "all inner geometry (coolant, moderator, air, void, etc.)."
        ),
    )

    # --- Boundary condition ---
    boundary_type: BoundaryType = Field(
        default=BoundaryType.REFLECTIVE,
        description=(
            "Boundary condition applied to all six box surfaces. "
            "'reflective' simulates an infinite repeating lattice. "
            "'vacuum' allows particles to escape (open geometry). "
            "'periodic' wraps particles for translational symmetry."
        ),
    )

    model_config = {"frozen": True}

    # ------------------------------------------------------------------
    # Validators
    # ------------------------------------------------------------------

    @model_validator(mode="after")
    def z_max_above_z_min(self) -> BoundingBoxSchema:
        if self.z_max <= self.z_min:
            raise ValueError(
                f"z_max ({self.z_max}) must be greater than z_min ({self.z_min})."
            )
        return self

    # ------------------------------------------------------------------
    # Convenience
    # ------------------------------------------------------------------

    def half_x(self) -> float:
        """Half-width in X — box spans -half_x to +half_x."""
        return self.x_size / 2.0

    def half_y(self) -> float:
        """Half-width in Y — box spans -half_y to +half_y."""
        return self.y_size / 2.0

    def axial_height(self) -> float:
        """Total axial height of the box (cm)."""
        return self.z_max - self.z_min