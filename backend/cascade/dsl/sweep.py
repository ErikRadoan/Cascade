"""Sweep support for parametric geometry studies.

Two sweep syntaxes are supported in YAML field values:

    Numeric range:  sweep(0.38 to 0.43, step=0.01)
                     -> [0.38, 0.39, 0.40, 0.41, 0.42, 0.43]

    Discrete list:   sweep(UO2, ThO2, UC)
                     -> ["UO2", "ThO2", "UC"]

The discrete form exists for fields that aren't numeric — material IDs,
template names, boundary_type, lattice orientation, etc. Anything that
would otherwise be a fixed dropdown value in the frontend can be swept
across its full set of options using this syntax.

Two public functions:

    parse_sweep(value) -> list[float] | list[str] | None
        Detects and parses "sweep(...)" strings from YAML field values.
        Returns None if the value is not a sweep expression.

    expand_sweep(yaml_text) -> list[tuple[dict[str, float | str], CascadeGeometry]]
        Full pipeline: YAML with sweep values -> list of (param_values, geometry).
        This is what GeometryService calls for a parametric study.
"""

from __future__ import annotations

import itertools
import re
from typing import Any
from copy import deepcopy

from .loader import load
from .expander import expand
from ..domain.geometry import CascadeGeometry


# ---------------------------------------------------------------------------
# Sweep expression parsers
# ---------------------------------------------------------------------------

# Numeric range form: sweep(0.38 to 0.43, step=0.01)
#                  or: sweep(20 to 30)              <- step defaults to 1.0
_SWEEP_RANGE_RE = re.compile(
    r"^sweep\(\s*"
    r"(?P<start>-?[\d.]+)"
    r"\s+to\s+"
    r"(?P<stop>-?[\d.]+)"
    r"(?:\s*,\s*step\s*=\s*(?P<step>[\d.]+))?"
    r"\s*\)$",
    re.IGNORECASE,
)

# Discrete list form: sweep(UO2, ThO2, UC)
# Deliberately does NOT match if the content contains " to " — that's the
# range form's job, and we want range syntax to fail loudly (via the range
# regex's own validation) rather than silently being parsed as a one-token
# discrete list containing the literal string "0.38 to 0.43".
_SWEEP_LIST_RE = re.compile(
    r"^sweep\(\s*(?P<items>[^()]+?)\s*\)$",
    re.IGNORECASE,
)


def parse_sweep(value: Any) -> list[float] | list[str] | None:
    """Parse a sweep expression string into an explicit list of values.

    Tries the numeric range form first, then the discrete list form.
    Args:
        value: Any YAML field value. Non-string values return None
               immediately — only strings can be sweep expressions.

    Returns:
        list[float] for a numeric range sweep,
        list[str] for a discrete list sweep,
        None if this is not a sweep expression at all.

    Examples:
        parse_sweep("sweep(20 to 30, step=2)")     -> [20.0, 22.0, ..., 30.0]
        parse_sweep("sweep(0.38 to 0.40, step=0.01)") -> [0.38, 0.39, 0.40]
        parse_sweep("sweep(UO2, ThO2, UC)")         -> ["UO2", "ThO2", "UC"]
        parse_sweep("sweep(reflective, vacuum)")    -> ["reflective", "vacuum"]
        parse_sweep(0.4096)                          -> None
        parse_sweep("UO2")                           -> None
    """
    if not isinstance(value, str):
        return None

    stripped = value.strip()

    range_match = _SWEEP_RANGE_RE.match(stripped)
    if range_match:
        return _parse_range(range_match)

    # Only attempt the list form if it actually looks like a sweep(...)
    # call — otherwise plain strings like "UO2" would never reach here
    # anyway since they don't start with "sweep(".
    if stripped.lower().startswith("sweep(") and stripped.endswith(")"):
        list_match = _SWEEP_LIST_RE.match(stripped)
        if list_match:
            return _parse_list(list_match)
        # Looked like a sweep(...) call but matched neither form —
        # this is a malformed sweep expression, not a bare string.
        raise ValueError(
            f"Could not parse sweep expression: '{value}'. "
            f"Use 'sweep(start to stop, step=X)' for numeric ranges, "
            f"or 'sweep(a, b, c)' for a discrete list of values."
        )

    return None


def _parse_range(match: re.Match) -> list[float]:
    start = float(match.group("start"))
    stop  = float(match.group("stop"))
    step  = float(match.group("step")) if match.group("step") else 1.0

    if step <= 0:
        raise ValueError(f"Sweep step must be positive, got {step}.")
    if start > stop:
        raise ValueError(
            f"Sweep start ({start}) must be less than or equal to stop ({stop})."
        )

    # Build explicit list — don't use range() on floats (accumulates error).
    # Round each point to avoid 0.30000000000000004-style artifacts.
    n_decimals = max(
        _decimal_places(start),
        _decimal_places(stop),
        _decimal_places(step),
    )
    values = []
    current = start
    while current <= stop + step * 1e-9:   # small epsilon avoids missing stop
        values.append(round(current, n_decimals))
        current += step

    if len(values) > 500:
        raise ValueError(
            f"Sweep produces {len(values)} points. "
            f"If this is intentional, increase the step size. "
            f"Large sweeps should be submitted as batch jobs."
        )

    return values


def _parse_list(match: re.Match) -> list[str]:
    items_raw = match.group("items")
    # Split on commas not inside the (already-stripped) parens.
    # Items are plain tokens — material IDs, template names, enum values —
    # none of which legitimately contain commas, so a simple split is safe.
    items = [item.strip() for item in items_raw.split(",")]
    items = [item for item in items if item]  # drop empties from trailing commas

    if not items:
        raise ValueError(
            "Sweep list expression has no values: 'sweep()'. "
            "Provide at least one value, e.g. 'sweep(UO2, ThO2)'."
        )

    if len(items) > 500:
        raise ValueError(
            f"Sweep list produces {len(items)} values. "
            f"Reduce the number of items."
        )

    return items


def _decimal_places(value: float) -> int:
    """Count decimal places in a float's string representation."""
    s = str(value)
    if "." in s:
        return len(s.split(".")[1])
    return 0


# ---------------------------------------------------------------------------
# Sweep expansion
# ---------------------------------------------------------------------------

def expand_sweep(
    yaml_text: str,
    geom_name: str = "cascade_geometry",
) -> list[tuple[dict[str, float | str], CascadeGeometry]]:
    """Expand a YAML geometry definition with sweep parameters.

    Detects all sweep(...) values in the YAML (both numeric range and
    discrete list forms), builds the cartesian product of their value
    lists, then runs load() + expand() once per combination.

    For a YAML with no sweep values, returns a single-element list —
    the caller doesn't need to special-case the no-sweep path.

    Args:
        yaml_text: Raw YAML text from the editor, possibly containing
                   sweep(...) expressions as field values.
        geom_name: Passed through to expand() for geometry naming.

    Returns:
        List of (param_values, geometry) tuples. param_values is the dict
        of sweep parameter substitutions that produced that geometry —
        e.g. {"fuel_pin.pellet_radius": 0.40, "fuel_pin.pellet_material": "UO2"}.
        Values may be float or str depending on which sweep form was used.
        Empty dict if no sweep parameters were present.

    Raises:
        ValueError: If sweep expressions are malformed or produce too many points.
        pydantic.ValidationError: If any schema combination fails validation.
    """
    import yaml  # local import — only needed here

    raw = yaml.safe_load(yaml_text)

    # --- Detect sweep fields ---
    # Walk all component fields, pull out any that are sweep expressions.
    # The "type" field is never sweepable — sweeping the TYPE of a
    # component (e.g. turning a FuelPin into a Box mid-sweep) isn't a
    # coherent geometry operation, so it's explicitly excluded here in
    # addition to being excluded from the YAML form fields that produce
    # sweep(...) values in the frontend.
    sweep_fields: dict[str, dict[str, list]] = {}
    for comp_name, comp_data in raw.items():
        if not isinstance(comp_data, dict):
            continue
        for field_name, field_value in comp_data.items():
            if field_name == "type":
                continue
            values = parse_sweep(field_value)
            if values is not None:
                sweep_fields.setdefault(comp_name, {})[field_name] = values

    if not sweep_fields:
        # No sweeps — single expansion
        schemas = load(yaml_text)
        geometry = expand(schemas, param_values={}, geom_name=geom_name)
        return [({}, geometry)]

    # --- Build cartesian product ---
    # Flatten to a list of (component, field, values) triples for product().
    # Works identically whether values are floats or strings.
    sweep_keys: list[tuple[str, str]] = []
    sweep_value_lists: list[list] = []
    for comp_name, fields in sweep_fields.items():
        for field_name, values in fields.items():
            sweep_keys.append((comp_name, field_name))
            sweep_value_lists.append(values)

    total = 1
    for v in sweep_value_lists:
        total *= len(v)
    if total > 500:
        raise ValueError(
            f"Sweep cartesian product produces {total} geometries. "
            f"Reduce the number of sweep points or parameters."
        )

    results: list[tuple[dict[str, float | str], CascadeGeometry]] = []

    for combo in itertools.product(*sweep_value_lists):
        # Build a modified raw dict with sweep values substituted
        substituted = deepcopy(raw)
        param_values: dict[str, float | str] = {}

        for (comp_name, field_name), value in zip(sweep_keys, combo):
            substituted[comp_name][field_name] = value
            # Key for param_values: "component.field" for clarity in results
            param_values[f"{comp_name}.{field_name}"] = value

        # Re-serialize to YAML and load through normal pipeline
        # This lets load() do all schema validation — no duplication.
        import yaml as _yaml
        substituted_yaml = _yaml.dump(substituted)
        schemas = load(substituted_yaml)
        geometry = expand(schemas, param_values=param_values, geom_name=geom_name)
        results.append((param_values, geometry))

    return results