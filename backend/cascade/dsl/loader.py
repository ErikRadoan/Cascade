"""DSL loader — YAML text to validated schema objects."""

from __future__ import annotations

import yaml
from pydantic import ValidationError

from .schema.base import BaseComponentSchema
from .schema.boolean import BooleanPlacementSchema
from .schema.box import BoxSchema
from .schema.cell import CellSchema
from .schema.cone import ConeZSchema
from .schema.cylinder import CylinderXSchema, CylinderYSchema, CylinderZSchema
from .schema.fuel_pin import FuelPinSchema
from .schema.lattice import HexLatticeSchema, SquareLatticeSchema
from .schema.plane import PlaneXSchema, PlaneYSchema, PlaneZSchema
from .schema.single_placement import SinglePlacementSchema
from .schema.sphere import SphereSchema
from .schema.torus import TorusSchema


SCHEMA_MAP: dict[str, type[BaseComponentSchema]] = {
    # Templates (legacy sugar — expand to Tier-1/2 objects internally)
    "FuelPin":         FuelPinSchema,
    "Box":             BoxSchema,
    # Placements
    "SinglePlacement":   SinglePlacementSchema,
    "SquareLattice":     SquareLatticeSchema,
    "HexLattice":        HexLatticeSchema,
    "BooleanPlacement":  BooleanPlacementSchema,
    # Tier-1 primitives (geometry-restructuring-plan.md Phase A)
    "PlaneX":          PlaneXSchema,
    "PlaneY":          PlaneYSchema,
    "PlaneZ":          PlaneZSchema,
    "Sphere":          SphereSchema,
    "CylinderX":       CylinderXSchema,
    "CylinderY":       CylinderYSchema,
    "CylinderZ":       CylinderZSchema,
    "ConeZ":           ConeZSchema,
    "Torus":           TorusSchema,
    # Tier-2 region-expression cells (geometry-restructuring-plan.md Phase B)
    "Cell":            CellSchema,
}


# Template-level boolean composites were removed in favour of BooleanPlacement.
# They are kept here only so old documents get a precise migration message
# instead of a generic "unknown type".
_REMOVED_BOOLEAN_TEMPLATES = {"Union": "union", "Subtraction": "subtraction", "Intersection": "intersection"}


def _removed_boolean_message(component_type: str, name: str) -> str:
    op = _REMOVED_BOOLEAN_TEMPLATES[component_type]
    return (
        f"'{name}' uses the removed template type '{component_type}'. Boolean shapes are now "
        f"built with BooleanPlacement: place each operand with a SinglePlacement, then combine "
        f"the placements, e.g.\n"
        f"  {name}:\n"
        f"    type: BooleanPlacement\n"
        f"    op: {op}\n"
        f"    children: [<placement_a>, <placement_b>]\n"
        f"Each child keeps its own material; the first child is the kept body for a "
        f"subtraction, and the earlier child wins where a union overlaps."
    )


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

    BooleanPlacement children are other *placements* (not templates).
    The expander is responsible for detecting cycles and for skipping
    independent expansion of any placement that is listed as a child of
    a BooleanPlacement.
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

        if component_type in _REMOVED_BOOLEAN_TEMPLATES:
            raise LoadError(_removed_boolean_message(component_type, name), component_name=name)

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
