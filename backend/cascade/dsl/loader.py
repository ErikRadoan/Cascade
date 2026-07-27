"""DSL loader — YAML text to validated schema objects."""

from __future__ import annotations

import yaml
from pydantic import ValidationError

from .schema.base import BaseComponentSchema
from .schema.box import BoxSchema
from .schema.cell import CellSchema
from .schema.cylinder import CylinderXSchema, CylinderYSchema, CylinderZSchema
from .schema.fuel_pin import FuelPinSchema
from .schema.lattice import HexLatticeSchema, SquareLatticeSchema
from .schema.plane import PlaneXSchema, PlaneYSchema, PlaneZSchema
from .schema.single_placement import SinglePlacementSchema
from .schema.sphere import SphereSchema


SCHEMA_MAP: dict[str, type[BaseComponentSchema]] = {
    # Templates (legacy sugar — expand to Tier-1/2 objects internally)
    "FuelPin":         FuelPinSchema,
    "Box":             BoxSchema,
    # Placements
    "SinglePlacement": SinglePlacementSchema,
    "SquareLattice":   SquareLatticeSchema,
    "HexLattice":       HexLatticeSchema,
    # Tier-1 primitives (geometry-restructuring-plan.md Phase A)
    "PlaneX":          PlaneXSchema,
    "PlaneY":          PlaneYSchema,
    "PlaneZ":          PlaneZSchema,
    "Sphere":          SphereSchema,
    "CylinderX":       CylinderXSchema,
    "CylinderY":       CylinderYSchema,
    "CylinderZ":       CylinderZSchema,
    # Tier-2 region-expression cells (geometry-restructuring-plan.md Phase B)
    "Cell":            CellSchema,
}


class LoadError(Exception):
    def __init__(self, message: str, component_name: str | None = None):
        super().__init__(message)
        self.component_name = component_name
        self.message = message


def load(text: str) -> dict[str, BaseComponentSchema]:
    """Parse and validate YAML geometry definition text.

    Returns ordered dict preserving YAML declaration order.

    Ordering note: legacy templates must still be declared before the
    placements that reference them (validated in expander.py). Tier-1
    primitives and Tier-2 Cells have NO such constraint — a Cell may
    reference a primitive declared later in the same document, because the
    expander resolves every primitive's Surface.id before resolving any
    Cell's region (see expander.py's Step 0 docstring). This is a
    deliberate, user-visible relaxation of the old rule for the new
    primitive/cell tier, not an oversight.
    """
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise LoadError(f"Invalid YAML: {e}") from e

    if not isinstance(raw, dict):
        raise LoadError(
            "Top-level YAML must be a mapping of component definitions."
        )

    result: dict[str, BaseComponentSchema] = {}

    for name, data in raw.items():
        if not isinstance(data, dict):
            raise LoadError(
                f"Component '{name}' must be a mapping of fields.",
                component_name=name,
            )

        data = dict(data)
        component_type = data.pop("type", None)

        if component_type is None:
            raise LoadError(
                f"Component '{name}' is missing the required 'type' field. "
                f"Known types: {', '.join(SCHEMA_MAP)}.",
                component_name=name,
            )

        schema_class = SCHEMA_MAP.get(component_type)
        if schema_class is None:
            raise LoadError(
                f"Unknown component type '{component_type}' in '{name}'. "
                f"Known types: {', '.join(SCHEMA_MAP)}.",
                component_name=name,
            )

        result[name] = schema_class(**data)

    return result


def validate(text: str) -> list[dict]:
    """Validate YAML and return structured errors. Never raises."""
    errors: list[dict] = []
    try:
        load(text)
    except LoadError as e:
        errors.append({
            "type":      "structure",
            "message":   e.message,
            "component": e.component_name,
            "field":     None,
        })
    except ValidationError as e:
        for err in e.errors():
            errors.append({
                "type":      "validation",
                "message":   err["msg"],
                "component": None,
                "field":     ".".join(str(loc) for loc in err["loc"]),
            })
    except Exception as e:
        errors.append({
            "type":      "yaml",
            "message":   str(e),
            "component": None,
            "field":     None,
        })
    return errors
