"""Boolean composite templates and BooleanPlacement hierarchy nodes.

Template booleans (Union / Subtraction / Intersection) author shapes at the
origin. BooleanPlacement composes already-placed objects in the scene graph.

All three template booleans support both binary ``a``/``b`` and n-ary
``items`` for flat multi-way operations without intermediate named nodes.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from .base import BaseComponentSchema


class _BooleanTemplateBase(BaseComponentSchema):
    """Shared shape of the Union / Subtraction / Intersection templates.

    All three take their operands the same way: either ``items`` (n-ary,
    preferred) or the legacy ``a`` / ``b`` pair. The result is one solid cell
    that uses this template's ``material``; operand materials are ignored.
    An operand can be a primitive, a Box or another boolean template, and may
    carry its own ``x`` / ``y`` / ``z`` to shift it inside this composite.
    """

    a: str | None = Field(default=None, description="Legacy first operand (use items instead).")
    b: str | None = Field(default=None, description="Legacy second operand (use items instead).")
    items: list[str] = Field(
        default_factory=list,
        description="N-ary operand template names (≥2). Preferred over a/b.",
    )
    material: str = Field(default="H2O", description="Material ID for the resulting solid cell.")
    x: float = Field(default=0.0, description="Local X offset when used as an operand of another boolean.")
    y: float = Field(default=0.0, description="Local Y offset when used as an operand of another boolean.")
    z: float = Field(default=0.0, description="Local Z offset when used as an operand of another boolean.")
    model_config = {"frozen": True}

    @model_validator(mode="after")
    def require_operands(self) -> _BooleanTemplateBase:
        names = self.operand_names()
        if len(self.items) < 2 and not (self.a and self.b):
            raise ValueError(
                f"{type(self).__name__[:-len('Schema')]} requires either items: [name, ...] "
                f"with ≥2 entries, or both a and b set."
            )
        self.check_operands(names)
        return self

    def check_operands(self, names: list[str]) -> None:
        """Hook for operation-specific operand checks."""

    def operand_names(self) -> list[str]:
        if len(self.items) >= 2:
            return list(self.items)
        return [self.a or "", self.b or ""]


class UnionSchema(_BooleanTemplateBase):
    """Solid is the union of all operands."""


class SubtractionSchema(_BooleanTemplateBase):
    """Solid is the first operand minus every other one (a ∩ ¬b ∩ ¬c ...).

    Order matters: the first operand is kept, the rest are cut out of it.
    """

    def check_operands(self, names: list[str]) -> None:
        kept, *cutters = names
        if kept in cutters:
            raise ValueError(
                f"Subtraction cuts its own kept operand '{kept}' out of itself, "
                f"which always leaves an empty solid."
            )


class IntersectionSchema(_BooleanTemplateBase):
    """Solid is the intersection of all operands."""


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
