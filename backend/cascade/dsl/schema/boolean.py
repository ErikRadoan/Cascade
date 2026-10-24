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

    Every op, union included, produces a single cell. Its material is the
    first kept child's; use ``materials`` to choose which materials are kept.
    With ``rx/ry/rz`` the whole composite is rotated about its x/y/z point.
    """

    op: Literal["union", "subtraction", "intersection"] = Field(
        ...,
        description="Boolean operation applied to the children in order.",
    )
    children: list[str] = Field(
        ...,
        min_length=2,
        description=(
            "YAML keys of the child placements. Order matters for subtraction: "
            "first child is the kept body."
        ),
    )
    materials: list[str] = Field(
        default_factory=list,
        description=(
            "Materials this boolean applies to / keeps. "
            "Empty list (default) means all materials present in the children."
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
