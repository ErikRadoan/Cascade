"""Sphere schema — a standalone spherical shape.

Unlike FuelPin (radial-surfaces-only, dependent on a placed Box for its
axial bounds — see expander.py's _place_fuel_pin), a Sphere is fully
self-contained: it carries its own bounding surface and can be placed
anywhere via SinglePlacement without requiring a Box to be placed first.
This is also what makes it usable as an operand inside Union/Subtraction/
Intersection (see boolean.py) — those recurse into whatever templates
their `a`/`b` fields name, and every shape they can reach must be able to
produce its own surfaces without depending on shared context the way
FuelPin does.
"""
from __future__ import annotations

from pydantic import Field

from ...domain.geometry import BoundaryType
from .base import BaseComponentSchema


class SphereSchema(BaseComponentSchema):
    """A solid sphere — fill material inside, one bounding surface.

    Example:
        my_sphere:
          type: Sphere
          radius: 2.0
          material: H2O

    boundary_type defaults to 'none' (an ordinary interior object) since
    a bare Sphere is usually placed as an inner absorber/void/moderator
    shape, not the outer problem boundary — set it explicitly (e.g.
    'vacuum') if you do want a Sphere to serve as the boundary.
    """
    radius: float = Field(default=1.0, gt=0, description="Sphere radius (cm).")
    material: str = Field(default="H2O", description="Fill material ID.")
    boundary_type: BoundaryType = Field(
        default=BoundaryType.NONE,
        description="Boundary condition on the sphere's surface. 'none' for an ordinary interior shape.",
    )

    model_config = {"frozen": True}