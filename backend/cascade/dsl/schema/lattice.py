"""Lattice schemas — macros that expand a template into a regular grid.

Both SquareLattice and HexLattice expand into N SinglePlacement-equivalent
instances at computed positions. The user never manually specifies each pin
position — the lattice computes them from the grid parameters.

All spacing/position parameters are separate scalars (not lists) so that
any individual parameter can be targeted by sweep().

Future mixed-type lattices:
    When needed, add a `cells: list[list[str]]` field (2D array of template
    names, like OpenMC's own lattice definition). The placement expander
    iterates over the cell array instead of repeating one template.
    This is a schema + expander change only — adapters and domain untouched.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from .base import BaseComponentSchema


class SquareLatticeSchema(BaseComponentSchema):
    """Rectangular NxM grid of a single template type.

    Pins are placed at positions:
        x_i = origin_x + i * pitch_x,  for i in 0..nx-1
        y_j = origin_y + j * pitch_y,  for j in 0..ny-1
        z   = origin_z + z_offset

    All lengths in centimeters. All parameters sweepable independently.

    Example — standard 17x17 PWR assembly:
        core:
          type: SquareLattice
          template: my_fuel_pin
          nx: 17
          ny: 17
          pitch_x: 1.26
          pitch_y: 1.26
          origin_x: 0.0
          origin_y: 0.0
          origin_z: 0.0

    Example — sweep pin pitch in X to study moderator ratio:
        core:
          type: SquareLattice
          template: my_fuel_pin
          nx: 5
          ny: 5
          pitch_x: sweep(1.0 to 1.5, step=0.1)
          pitch_y: 1.26
          origin_x: 0.0
          origin_y: 0.0
          origin_z: 0.0
    """

    template: str = Field(
        ...,
        description="Name of the template to replicate. Must be defined earlier in the YAML.",
    )

    # Grid dimensions
    nx: int = Field(default=1, gt=0,
        description="Number of pin positions in X direction.")
    ny: int = Field(default=1, gt=0,
        description="Number of pin positions in Y direction.")

    # Spacing — independent so X and Y can be swept separately
    pitch_x: float = Field(default=1.26, gt=0,
        description="Center-to-center distance between pins in X (cm).")
    pitch_y: float = Field(default=1.26, gt=0,
        description="Center-to-center distance between pins in Y (cm).")

    # Origin — position of the [0,0] pin
    # Separate scalars so each axis can be swept independently
    origin_x: float = Field(default=0.0,
        description="X position of the first pin (i=0, j=0) (cm).")
    origin_y: float = Field(default=0.0,
        description="Y position of the first pin (i=0, j=0) (cm).")
    origin_z: float = Field(default=0.0,
        description="Z position (bottom face) of all pins (cm).")

    model_config = {"frozen": True}

    @model_validator(mode="after")
    def warn_large_lattice(self) -> SquareLatticeSchema:
        total = self.nx * self.ny
        if total > 10_000:
            raise ValueError(
                f"Lattice would generate {total} pins ({self.nx}x{self.ny}). "
                f"Maximum is 10,000. Reduce nx or ny."
            )
        return self

    def pin_positions(self) -> list[tuple[float, float, float]]:
        """Return (x, y, z) for every pin in the lattice, row-major order."""
        positions = []
        for j in range(self.ny):
            for i in range(self.nx):
                x = self.origin_x + i * self.pitch_x
                y = self.origin_y + j * self.pitch_y
                z = self.origin_z
                positions.append((x, y, z))
        return positions

    def total_pins(self) -> int:
        return self.nx * self.ny


class HexLatticeSchema(BaseComponentSchema):
    """Hexagonal lattice of a single template type.

    Pins are arranged in concentric rings around a central pin.
    Ring 0 = center pin only (1 pin).
    Ring 1 = 6 pins.
    Ring n = 6n pins.
    Total pins = 1 + 3*n_rings*(n_rings-1) + ... = 3n²- 3n + 1

    All lengths in centimeters. All parameters sweepable independently.

    Example — 3-ring hex assembly:
        hex_core:
          type: HexLattice
          template: my_fuel_pin
          n_rings: 3
          pitch: 1.26
          center_x: 0.0
          center_y: 0.0
          center_z: 0.0
          orientation: pointy_top
    """

    template: str = Field(
        ...,
        description="Name of the template to replicate.",
    )

    # Grid
    n_rings: int = Field(default=1, ge=1,
        description="Number of rings including the center pin. n_rings=1 = single pin.")

    # Spacing
    pitch: float = Field(default=1.26, gt=0,
        description="Center-to-center distance between adjacent pins (cm).")

    # Orientation
    orientation: Literal["pointy_top", "flat_top"] = Field(
        default="pointy_top",
        description=(
            "Hex orientation. 'pointy_top' = vertex at top (common in PWR). "
            "'flat_top' = flat edge at top."
        ),
    )

    # Center position — separate scalars for independent sweep
    center_x: float = Field(default=0.0, description="X of the center pin (cm).")
    center_y: float = Field(default=0.0, description="Y of the center pin (cm).")
    center_z: float = Field(default=0.0, description="Z of the bottom face of all pins (cm).")

    model_config = {"frozen": True}

    @model_validator(mode="after")
    def warn_large_lattice(self) -> HexLatticeSchema:
        total = self.total_pins()
        if total > 10_000:
            raise ValueError(
                f"Hex lattice with {self.n_rings} rings would generate {total} pins. "
                f"Maximum is 10,000."
            )
        return self

    def total_pins(self) -> int:
        n = self.n_rings - 1
        return 3 * n * n + 3 * n + 1

    def pin_positions(self) -> list[tuple[float, float, float]]:
        """Return (x, y, z) for every pin, starting from center outward."""
        import math
        positions = [(self.center_x, self.center_y, self.center_z)]

        if self.n_rings == 1:
            return positions

        # Unit vectors for the six hex directions
        # pointy_top: first direction points right (+X)
        # flat_top:   first direction points upper-right (30° from X)
        if self.orientation == "pointy_top":
            angle_offset = 0.0
        else:
            angle_offset = math.pi / 6.0

        directions = [
            (math.cos(angle_offset + i * math.pi / 3),
             math.sin(angle_offset + i * math.pi / 3))
            for i in range(6)
        ]

        p = self.pitch
        for ring in range(1, self.n_rings):
            # Start at the "bottom" of this ring and walk the perimeter
            # Each ring has 6*ring pins
            cx = self.center_x + directions[4][0] * ring * p
            cy = self.center_y + directions[4][1] * ring * p

            for side in range(6):
                dx, dy = directions[(side + 2) % 6]
                for step in range(ring):
                    positions.append((cx, cy, self.center_z))
                    cx += dx * p
                    cy += dy * p

        return positions