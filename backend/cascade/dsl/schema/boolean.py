from __future__ import annotations
from pydantic import Field
from .base import BaseComponentSchema

class _BooleanSchemaBase(BaseComponentSchema):
    a: str = Field(...)
    b: str = Field(...)
    material: str = Field(default="H2O")
    model_config = {"frozen": True}

class UnionSchema(_BooleanSchemaBase):
    pass

class SubtractionSchema(_BooleanSchemaBase):
    pass

class IntersectionSchema(_BooleanSchemaBase):
    pass