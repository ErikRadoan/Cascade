"""BooleanPlacement: the one way to combine shapes with union / subtraction / intersection.

A BooleanPlacement composes already-placed objects (SinglePlacements, lattices,
or other BooleanPlacements) in the scene graph. The former template-level
Union / Subtraction / Intersection types were removed; see
``loader._removed_boolean_message`` for the migration hint users get.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from .base import BaseComponentSchema


class BooleanPlacementSchema(BaseComponentSchema):
    """Hierarchy node that combines already-placed objects with a boolean op.

    A child is made of one or more cells, each with its own material (a FuelPin
    is a pellet, a gap and cladding). The result keeps those cells and their
    materials; it is not collapsed into a single material. Where children
    overlap, cells are clipped so that nothing overlaps. Cells are named
    ``<name>_layer<k>`` when there is more than one.

    * ``union``: all the children's cells. Where children overlap, the earlier
      child in ``children`` wins, so its material is the one you get.
    * ``subtraction``: the first child's cells with every other child cut away.
      The cutters only define space; their materials do not appear.
    * ``intersection``: the first child's cells, kept only where every other
      child also is. The others only define space; their materials do not appear.

    ``materials`` chooses which child cells take part (see that field).

    With ``rx/ry/rz`` the whole composite is rotated about its x/y/z point and
    keeps every material. If instead a *child* is rotated (rx/ry/rz on its
    SinglePlacement), that child lives in its own universe and can't share cells
    with its siblings, so the composite is built as one material, the first
    child's, and a warning says so.
    """

    op: Literal["union", "subtraction", "intersection"] = Field(
        ...,
        description=(
            "union: all children, earlier child wins where they overlap. "
            "subtraction: first child minus the rest. "
            "intersection: first child where all the others are too."
        ),
    )
    children: list[str] = Field(
        ...,
        min_length=2,
        description=(
            "YAML keys of the child placements, in priority order: for a union the "
            "earlier child wins an overlap; for subtraction and intersection the "
            "first child is the body whose cells and materials are kept."
        ),
    )
    materials: list[str] = Field(
        default_factory=list,
        description=(
            "Restrict the operation to cells made of these materials. Cells of any other "
            "material are treated as absent: they add no volume to a union, cut nothing as "
            "a cutter, and don't count towards an intersection operand. A child left with "
            "no cells is the empty set, so a union skips it, a subtraction whose first "
            "child is empty (or an intersection with an empty operand) has an empty "
            "result, and an empty cutter cuts nothing. Empty list (default): every cell "
            "takes part."
        ),
    )
    x: float = Field(default=0.0, description="X translation of the composite.")
    y: float = Field(default=0.0, description="Y translation of the composite.")
    z: float = Field(default=0.0, description="Z translation of the composite.")

    # Rotation of the whole composite about its x/y/z point, intrinsic XYZ
    # Euler angles in degrees (same convention as SinglePlacement). Children
    # are positioned relative to that point. Zero keeps the plain path.
    rx: float = Field(default=0.0, description="Rotation of the composite about X (degrees).")
    ry: float = Field(default=0.0, description="Rotation of the composite about Y (degrees).")
    rz: float = Field(default=0.0, description="Rotation of the composite about Z (degrees).")

    model_config = {"frozen": True}
