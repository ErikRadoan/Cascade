"""Primitive surface expansion — Tier-1 of geometry-restructuring-plan.md.

Each primitive schema (Sphere, PlaneX/Y/Z, CylinderX/Y/Z) describes ONE
domain.geometry.Surface, at its own authored position. Unlike a composite
template (FuelPin) or the legacy Box (position-free, wrapped in a
SinglePlacement), a primitive carries its own x/y/z directly — matching how
a real OpenMC Surface works.

This module owns:
    - the PrimitiveExpander protocol every primitive schema implements
      (plan §3.1)
    - PRIMITIVE_REGISTRY, mapping schema class -> PrimitiveExpander instance
      (plan §3.4)
    - the concrete expander for every schema in schema/plane.py,
      schema/sphere.py, schema/cylinder.py

Primitives self-register at import time (see PRIMITIVE_REGISTRY below)
rather than waiting on a later registry-wiring pass — this file already
IS the registry; expander.py imports it directly.

`ExpansionContext` is declared here as a small Protocol (rather than
importing expander.Context directly) so this module has no import-time
dependency on expander.py — expander.py is the one that imports FROM this
module, never the reverse. expander.Context already satisfies this
Protocol structurally (it has a matching `fresh_id` method), so no
adapter code is needed at the call site.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..domain.geometry import Inside, Region, Surface, SurfaceType
from .schema.base import BaseComponentSchema
from .schema.cylinder import CylinderXSchema, CylinderYSchema, CylinderZSchema
from .schema.plane import PlaneXSchema, PlaneYSchema, PlaneZSchema
from .schema.sphere import SphereSchema


@runtime_checkable
class ExpansionContext(Protocol):
    """The minimal shape expander.Context provides — just enough for a
    primitive expander to mint fresh surface IDs."""

    def fresh_id(self, prefix: str = "s") -> str: ...


class PrimitiveExpander(Protocol):
    """Implemented once per Tier-1 primitive schema type
    (geometry-restructuring-plan.md §3.1)."""

    def expand_primitive(
        self, ctx: ExpansionContext, schema: BaseComponentSchema, name: str,
    ) -> tuple[list[Surface], Region]:
        """Return the Surface(s) this primitive creates (already at its own
        authored position — primitives carry their own x/y/z, no external
        translation step) and a Region expression (usually
        Inside(surface.id)) that a Tier-2 Cell can reference as an operand.

        `name` is the YAML key for this primitive — used only for
        debugging/error messages, never for ID generation (fresh_id owns
        that, via `ctx`).
        """
        ...


# ---------------------------------------------------------------------------
# Concrete expanders — one per schema, each a thin, stateless wrapper that
# builds exactly one Surface at the position/params the schema was authored
# with, then wraps it as Inside(surface.id) — the region a Cell gets back
# when it names this primitive as an operand.
# ---------------------------------------------------------------------------

class _PlaneXExpander:
    def expand_primitive(self, ctx: ExpansionContext, schema: PlaneXSchema, name: str):
        s = Surface(
            id=ctx.fresh_id("s"), type_=SurfaceType.PLANE_X,
            params={"x0": schema.x}, boundary_type=schema.boundary_type,
        )
        return [s], Inside(s.id)


class _PlaneYExpander:
    def expand_primitive(self, ctx: ExpansionContext, schema: PlaneYSchema, name: str):
        s = Surface(
            id=ctx.fresh_id("s"), type_=SurfaceType.PLANE_Y,
            params={"y0": schema.y}, boundary_type=schema.boundary_type,
        )
        return [s], Inside(s.id)


class _PlaneZExpander:
    def expand_primitive(self, ctx: ExpansionContext, schema: PlaneZSchema, name: str):
        s = Surface(
            id=ctx.fresh_id("s"), type_=SurfaceType.PLANE_Z,
            params={"z0": schema.z}, boundary_type=schema.boundary_type,
        )
        return [s], Inside(s.id)


class _SphereExpander:
    def expand_primitive(self, ctx: ExpansionContext, schema: SphereSchema, name: str):
        s = Surface(
            id=ctx.fresh_id("s"), type_=SurfaceType.SPHERE,
            params={"x0": schema.x, "y0": schema.y, "z0": schema.z, "r": schema.radius},
            boundary_type=schema.boundary_type,
        )
        return [s], Inside(s.id)


class _CylinderXExpander:
    def expand_primitive(self, ctx: ExpansionContext, schema: CylinderXSchema, name: str):
        s = Surface(
            id=ctx.fresh_id("s"), type_=SurfaceType.CYLINDER_X,
            params={"y0": schema.y, "z0": schema.z, "r": schema.radius},
            boundary_type=schema.boundary_type,
        )
        return [s], Inside(s.id)


class _CylinderYExpander:
    def expand_primitive(self, ctx: ExpansionContext, schema: CylinderYSchema, name: str):
        s = Surface(
            id=ctx.fresh_id("s"), type_=SurfaceType.CYLINDER_Y,
            params={"x0": schema.x, "z0": schema.z, "r": schema.radius},
            boundary_type=schema.boundary_type,
        )
        return [s], Inside(s.id)


class _CylinderZExpander:
    def expand_primitive(self, ctx: ExpansionContext, schema: CylinderZSchema, name: str):
        s = Surface(
            id=ctx.fresh_id("s"), type_=SurfaceType.CYLINDER_Z,
            params={"x0": schema.x, "y0": schema.y, "r": schema.radius},
            boundary_type=schema.boundary_type,
        )
        return [s], Inside(s.id)


# ---------------------------------------------------------------------------
# Registry — schema class -> expander instance (plan §3.4)
# ---------------------------------------------------------------------------

PRIMITIVE_REGISTRY: dict[type[BaseComponentSchema], PrimitiveExpander] = {
    PlaneXSchema:    _PlaneXExpander(),
    PlaneYSchema:    _PlaneYExpander(),
    PlaneZSchema:    _PlaneZExpander(),
    SphereSchema:    _SphereExpander(),
    CylinderXSchema: _CylinderXExpander(),
    CylinderYSchema: _CylinderYExpander(),
    CylinderZSchema: _CylinderZExpander(),
}


def is_primitive(schema: BaseComponentSchema) -> bool:
    """True if `schema`'s exact type is a registered Tier-1 primitive."""
    return type(schema) in PRIMITIVE_REGISTRY


def expand_primitive(
    ctx: ExpansionContext, schema: BaseComponentSchema, name: str,
) -> tuple[list[Surface], Region]:
    """Dispatch to the registered expander for `schema`'s type.

    Raises:
        TypeError: if schema's type isn't registered as a primitive —
        callers should only reach this after checking `is_primitive()`.
    """
    expander = PRIMITIVE_REGISTRY.get(type(schema))
    if expander is None:
        raise TypeError(
            f"'{name}' (type {type(schema).__name__}) is not a registered "
            f"Tier-1 primitive. Registered types: "
            f"{[t.__name__ for t in PRIMITIVE_REGISTRY]}."
        )
    return expander.expand_primitive(ctx, schema, name)
