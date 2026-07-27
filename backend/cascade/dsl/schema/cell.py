"""Cell schema — Tier-2 (geometry-restructuring-plan.md Phase B): assigns a
material to a region expression built from Tier-1 primitives by name.

The region tree is authored as a raw nested dict, matching
domain.geometry.region_to_json()'s shape exactly (see that function and its
inverse, region_from_yaml_dict(), also in domain/geometry.py). This schema
validates the TREE SHAPE (correct `op` values, correct nesting) at parse
time; it deliberately does NOT resolve `surface` name references to
Surface.id here — that requires seeing the whole document (a Cell may
reference a primitive declared *later* in the same YAML file), so name
resolution happens once in expander.py, after every Tier-1 primitive in
the document has already been expanded into a name -> Surface.id map.

Example:
    carved:
      type: Cell
      material: H2O
      region:
        op: and
        items:
          - { op: inside, surface: s_sphere }
          - { op: not, item: { op: inside, surface: s_cyl } }
"""

from __future__ import annotations

from typing import Any

from pydantic import Field, field_validator

from .base import BaseComponentSchema

_VALID_OPS = {"inside", "outside", "and", "or", "not"}


def _validate_region_shape(node: Any, path: str = "region") -> None:
    """Recursively validate the region tree's shape, raising ValueError with
    a path-qualified message on the first problem found — this is what lets
    POST /geometry/validate surface a clean, localized error instead of a
    generic Pydantic dict-shape failure."""
    if not isinstance(node, dict):
        raise ValueError(f"{path} must be a mapping, got {type(node).__name__}.")

    op = node.get("op")
    if op not in _VALID_OPS:
        raise ValueError(f"{path}.op must be one of {sorted(_VALID_OPS)}, got {op!r}.")

    if op in ("inside", "outside"):
        surface = node.get("surface")
        if not isinstance(surface, str) or not surface:
            raise ValueError(f"{path}.surface must be a non-empty string surface name.")

    elif op in ("and", "or"):
        items = node.get("items")
        if not isinstance(items, list) or not items:
            raise ValueError(f"{path}.items must be a non-empty list.")
        for i, item in enumerate(items):
            _validate_region_shape(item, f"{path}.items[{i}]")

    elif op == "not":
        item = node.get("item")
        if item is None:
            raise ValueError(f"{path}.item is required for a 'not' node.")
        _validate_region_shape(item, f"{path}.item")


class CellSchema(BaseComponentSchema):
    """Assigns a material to a region expression over Tier-1 primitives.

    `region`'s shape is validated here; surface *name* references inside it
    are resolved to Surface.id later, in expander.py, once the full
    document's primitives have been expanded (see module docstring).
    """

    material: str = Field(..., description="Material ID for this cell's region.")
    region: dict = Field(
        ...,
        description=(
            "Nested region-expression tree. Shape mirrors "
            "domain.geometry.region_to_json(): "
            "{op: inside|outside, surface: <name>} | "
            "{op: and|or, items: [<node>, ...]} | "
            "{op: not, item: <node>}."
        ),
    )

    model_config = {"frozen": True}

    @field_validator("region")
    @classmethod
    def _validate_region(cls, v: dict) -> dict:
        _validate_region_shape(v)
        return v
