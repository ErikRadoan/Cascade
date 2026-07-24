"""Boolean composite schemas — Union, Subtraction, Intersection.

Each references two other templates by name (`a`, `b` — which may
themselves be Sphere, Box, or another Union/Subtraction/Intersection,
allowing arbitrary nesting) and combines their regions:

    Union:        a OR b
    Subtraction:  a AND NOT b   (a with b's volume removed)
    Intersection: a AND b

These are templates like Box/Sphere/FuelPin — they get placed via
SinglePlacement (not a lattice, yet — see expander.py) and produce ONE
filled cell at the placement position. See expander.py's
_expand_shape_at_origin(), which recursively resolves `a`/`b` into
(surfaces, region) pairs.

`a`/`b` may reference Box or Sphere, or another boolean composite. They
may NOT reference FuelPin — FuelPin has no axial surfaces of its own (it
borrows z-planes from a placed Box; see fuel_pin.py and expander.py's
_place_fuel_pin) and so cannot produce a self-contained region. Referencing
FuelPin here raises a clear error at expansion time.

Example — a spherical void carved out of a moderator block:
    outer_block:
      type: Box
      x_size: 4
      y_size: 4
      z_size: 4
      material: H2O

    void_sphere:
      type: Sphere
      radius: 1.2
      material: void

    carved_block:
      type: Subtraction
      a: outer_block
      b: void_sphere
      material: H2O

    placed:
      type: SinglePlacement
      template: carved_block
      x: 0.0
      y: 0.0
      z: 0.0
"""
from __future__ import annotations

from pydantic import Field

from .base import BaseComponentSchema


class _BooleanSchemaBase(BaseComponentSchema):
    a: str = Field(..., description="Name of the first operand template.")
    b: str = Field(..., description="Name of the second operand template.")
    material: str = Field(default="H2O", description="Fill material ID for the combined region.")

    model_config = {"frozen": True}


class UnionSchema(_BooleanSchemaBase):
    """Combined region: everything inside `a` OR inside `b`."""
    pass


class SubtractionSchema(_BooleanSchemaBase):
    """`a` with `b`'s volume removed: inside `a` AND NOT inside `b`."""
    pass


class IntersectionSchema(_BooleanSchemaBase):
    """Only the overlap: inside `a` AND inside `b`."""
    pass