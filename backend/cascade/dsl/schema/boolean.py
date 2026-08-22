"""Boolean composite templates — Union / Subtraction / Intersection.

These are *templates*, not placements. Author the boolean once, then place
it with SinglePlacement (same flow as Box / FuelPin).

YAML example::

    ball:
      type: Sphere
      radius: 2.0

    block:
      type: Box
      x_size: 3.0
      y_size: 3.0
      z_size: 3.0
      material: H2O

    ball_minus_block:
      type: Subtraction
      a: ball          # keep this shape
      b: block         # remove this shape
      material: UO2

    placed:
      type: SinglePlacement
      template: ball_minus_block
      x: 0.0
      y: 0.0
      z: 0.0

Semantics (OpenMC / CSG region trees):

- Union(a, b)         → a ∪ b
- Intersection(a, b)  → a ∩ b
- Subtraction(a, b)   → a \\ b  (a ∩ ¬b)

Operands ``a`` / ``b`` are YAML keys of other templates. Supported operand
types (see expander._expand_shape_at_origin):

- Box
- Tier-1 primitives (Sphere, PlaneX/Y/Z, CylinderX/Y/Z)
- Nested Union / Subtraction / Intersection

FuelPin is **not** a valid operand (multi-cell radial layers can't collapse
to a single region). Use Box / Sphere / boolean trees instead.

Registered in loader.SCHEMA_MAP as ``Union``, ``Subtraction``, ``Intersection``.
"""

from __future__ import annotations

from pydantic import Field

from .base import BaseComponentSchema


class _BooleanSchemaBase(BaseComponentSchema):
    """Shared fields for all binary boolean composites."""

    a: str = Field(
        ...,
        min_length=1,
        description="YAML key of the left-hand operand template.",
    )
    b: str = Field(
        ...,
        min_length=1,
        description="YAML key of the right-hand operand template.",
    )
    material: str = Field(
        default="H2O",
        description="Material ID assigned to the resulting solid cell.",
    )
    model_config = {"frozen": True}


class UnionSchema(_BooleanSchemaBase):
    """Solid is the union of operands a and b (a ∪ b)."""

    pass


class SubtractionSchema(_BooleanSchemaBase):
    """Solid is a minus b (a ∩ ¬b). Order matters: a is kept, b is cut out."""

    pass


class IntersectionSchema(_BooleanSchemaBase):
    """Solid is the intersection of operands a and b (a ∩ b)."""

    pass
