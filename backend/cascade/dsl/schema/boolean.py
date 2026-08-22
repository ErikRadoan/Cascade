"""Boolean composite templates and BooleanPlacement hierarchy nodes.

Template booleans (Union / Subtraction / Intersection) author shapes at the
origin. BooleanPlacement composes already-placed objects in the scene graph.

Union supports both binary ``a``/``b`` and n-ary ``items`` for flat multi-way
unions without intermediate named nodes.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from .base import BaseComponentSchema


class _BooleanSchemaBase(BaseComponentSchema):
    """Shared fields for binary boolean templates (Subtraction / Intersection)."""

    a: str = Field(..., min_length=1, description="YAML key of the left-hand operand template.")
    b: str = Field(..., min_length=1, description="YAML key of the right-hand operand template.")
    material: str = Field(default="H2O", description="Material ID assigned to the resulting solid cell.")
    model_config = {"frozen": True}


class UnionSchema(BaseComponentSchema):
    """Solid is the union of operands.

    Prefer ``items`` for 2+ shapes (flat n-ary). Legacy ``a``/``b`` still works.
    Only the outermost material is used; intermediate materials are ignored.
    """

    a: str | None = Field(default=None, description="Legacy left operand (use items instead).")
    b: str | None = Field(default=None, description="Legacy right operand (use items instead).")
    items: list[str] = Field(
        default_factory=list,
        description="N-ary operand template names (≥2). Preferred over a/b.",
    )
    material: str = Field(default="H2O", description="Material ID for the resulting solid cell.")
    model_config = {"frozen": True}

    @model_validator(mode="after")
    def require_operands(self) -> UnionSchema:
        if len(self.items) >= 2:
            return self
        if self.a and self.b:
            return self
        raise ValueError(
            "Union requires either items: [name, ...] with ≥2 entries, "
            "or both a and b set."
        )

    def operand_names(self) -> list[str]:
        if len(self.items) >= 2:
            return list(self.items)
        return [self.a or "", self.b or ""]


class SubtractionSchema(_BooleanSchemaBase):
    """Solid is a minus b (a ∩ ¬b). Order matters: a is kept, b is cut out."""

    pass


class IntersectionSchema(_BooleanSchemaBase):
    """Solid is the intersection of operands a and b (a ∩ b)."""

    pass


class BooleanPlacementSchema(BaseComponentSchema):
    """Hierarchy node that combines already-placed objects with a boolean op."""

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

    model_config = {"frozen": True}
