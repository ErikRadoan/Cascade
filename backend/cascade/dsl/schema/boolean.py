"""Boolean composite templates and BooleanPlacement hierarchy nodes.

Two complementary mechanisms:

1. **Template booleans** (`Union` / `Subtraction` / `Intersection`)
   Author a reusable shape at the origin. Operands `a` / `b` are other
   *template* names. Place the result later with SinglePlacement.

2. **BooleanPlacement** (new)
   Scene-graph composition of already-placed objects. Select two (or more)
   placements in the Objects tab → create a BooleanPlacement parent that
   owns them. Positions live on the children; the boolean accumulates
   transforms. Works with SinglePlacement and lattice placements.

YAML example (template path — unchanged)::

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
      a: ball
      b: block
      material: UO2

    placed:
      type: SinglePlacement
      template: ball_minus_block
      x: 0.0
      y: 0.0
      z: 0.0

YAML example (BooleanPlacement path)::

    left_box:
      type: SinglePlacement
      template: Box
      x: -2.0
      y: 0.0
      z: 0.0

    right_sphere:
      type: SinglePlacement
      template: Sphere
      x: 2.0
      y: 0.0
      z: 0.0

    combined:
      type: BooleanPlacement
      op: union
      children: [left_box, right_sphere]
      # materials: []          # empty = all materials from children (default)
      # materials: [H2O, UO2]  # restrict the operation / result to these
      x: 0.0
      y: 0.0
      z: 0.0

Semantics (OpenMC / CSG region trees):

- Union         → a ∪ b
- Intersection  → a ∩ b
- Subtraction   → a \\ b  (a ∩ ¬b)   (first child is the kept body)

Materials
---------
`materials` is a filter over the materials present in the child placements.

- Empty list (default) → operate on / keep *all* materials found in the
  children. The UI should populate the available set from the union of
  every material ID reachable from the children.
- Non-empty list → only those materials participate in the boolean (or
  are assigned to the resulting solid region). Exact filtering semantics
  are applied in the expander (see BOOLEAN_PLACEMENTS.md).

Valid children for BooleanPlacement: SinglePlacement, SquareLattice,
HexLattice, and nested BooleanPlacement. FuelPin is allowed only when it
appears as a lattice template (the lattice expands to cells); a bare
FuelPin SinglePlacement is rejected by the expander with a clear error
because it is multi-cell.

Registered in loader.SCHEMA_MAP as ``Union``, ``Subtraction``,
``Intersection``, and ``BooleanPlacement``.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from .base import BaseComponentSchema


class _BooleanSchemaBase(BaseComponentSchema):
    """Shared fields for all binary boolean *templates*."""

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


class BooleanPlacementSchema(BaseComponentSchema):
    """Hierarchy node that combines already-placed objects with a boolean op.

    Children keep their own transforms. The expander walks the tree,
    accumulates parent→child transforms, and emits a single CSG region
    (or a set of cells when materials are heterogeneous).

    This is the interactive path: select placements in the Objects tab
    and create a BooleanPlacement parent. Distinct from the template
    booleans (Union/Subtraction/Intersection) which author shapes at the
    origin for later placement.
    """

    op: Literal["union", "subtraction", "intersection"] = Field(
        ...,
        description="Boolean operation applied to the children in order.",
    )
    children: list[str] = Field(
        ...,
        min_length=2,
        description=(
            "YAML keys of the child placements (SinglePlacement, Lattice, "
            "or nested BooleanPlacement). Order matters for subtraction: "
            "first child is the kept body."
        ),
    )
    materials: list[str] = Field(
        default_factory=list,
        description=(
            "Materials this boolean applies to / keeps. "
            "Empty list (default) means all materials present in the children. "
            "UI should derive the available set from the union of child materials "
            "and let the user multi-select a subset."
        ),
    )
    # Residual transform of the composite itself (usually left at origin;
    # children already carry the interesting positions).
    x: float = Field(default=0.0, description="X translation of the composite.")
    y: float = Field(default=0.0, description="Y translation of the composite.")
    z: float = Field(default=0.0, description="Z translation of the composite.")

    model_config = {"frozen": True}
