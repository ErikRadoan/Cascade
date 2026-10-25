"""Conservative axis-aligned bounds of regions.

``region_bounds`` answers "where could a point of this region possibly be?".
The answer is always a *superset*: when a shape can't be bounded cheaply (a
complement, a cone, a surface this module doesn't know) the result is
``UNBOUNDED``. That makes it safe for pruning. If two bounds don't overlap, the
regions can't either; if they might, the caller keeps the exact region test.

Bounds are ``(xmin, xmax, ymin, ymax, zmin, zmax)``, or ``None`` for a region
that is provably empty.
"""

from __future__ import annotations

import math
from collections.abc import Mapping

from .geometry import (
    Complement,
    Inside,
    Intersection,
    Outside,
    Region,
    Surface,
    SurfaceType,
    Union,
)

Bounds = tuple[float, float, float, float, float, float]

_INF = math.inf
UNBOUNDED: Bounds = (-_INF, _INF, -_INF, _INF, -_INF, _INF)

# Two bounds that overlap by less than this (cm) count as merely touching.
OVERLAP_TOLERANCE = 1e-9

_ALIASES = {"x0": "x", "y0": "y", "z0": "z"}


def _param(surface: Surface, name: str) -> float | None:
    """A surface parameter, accepting the short x/y/z aliases the expander writes."""
    params = surface.params
    value = params.get(name)
    if value is None and name in _ALIASES:
        value = params.get(_ALIASES[name])
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def _around(centre: float, half: float, axis: int, box: list[float]) -> None:
    box[2 * axis] = centre - half
    box[2 * axis + 1] = centre + half


def surface_inside_bounds(surface: Surface) -> Bounds:
    """Bounds of the negative half-space (``Inside``) of a surface."""
    box = list(UNBOUNDED)
    t = surface.type_

    if t in (SurfaceType.PLANE_X, SurfaceType.PLANE_Y, SurfaceType.PLANE_Z):
        axis = {SurfaceType.PLANE_X: 0, SurfaceType.PLANE_Y: 1, SurfaceType.PLANE_Z: 2}[t]
        at = _param(surface, "xyz"[axis] + "0")
        if at is None:
            return UNBOUNDED
        box[2 * axis + 1] = at
        return tuple(box)  # type: ignore[return-value]

    if t in (SurfaceType.CYLINDER_X, SurfaceType.CYLINDER_Y, SurfaceType.CYLINDER_Z):
        axis = {SurfaceType.CYLINDER_X: 0, SurfaceType.CYLINDER_Y: 1, SurfaceType.CYLINDER_Z: 2}[t]
        r = _param(surface, "r")
        if r is None:
            return UNBOUNDED
        for other in (a for a in range(3) if a != axis):
            centre = _param(surface, "xyz"[other] + "0")
            if centre is None:
                return UNBOUNDED
            _around(centre, abs(r), other, box)
        return tuple(box)  # type: ignore[return-value]

    if t is SurfaceType.SPHERE:
        r = _param(surface, "r")
        centres = [_param(surface, f"{a}0") for a in "xyz"]
        if r is None or any(c is None for c in centres):
            return UNBOUNDED
        for axis, centre in enumerate(centres):
            _around(centre, abs(r), axis, box)  # type: ignore[arg-type]
        return tuple(box)  # type: ignore[return-value]

    if t is SurfaceType.TORUS:  # z-torus: ring radius a, radial half-width b, axial half-height c
        a, b, c = (_param(surface, n) for n in ("a", "b", "c"))
        centres = [_param(surface, f"{ax}0") for ax in "xyz"]
        if None in (a, b, c) or any(v is None for v in centres):
            return UNBOUNDED
        reach = abs(a) + abs(b)  # type: ignore[arg-type]
        _around(centres[0], reach, 0, box)  # type: ignore[arg-type]
        _around(centres[1], reach, 1, box)  # type: ignore[arg-type]
        _around(centres[2], abs(c), 2, box)  # type: ignore[arg-type]
        return tuple(box)  # type: ignore[return-value]

    return UNBOUNDED  # cones, and anything this module doesn't know


def surface_outside_bounds(surface: Surface) -> Bounds:
    """Bounds of the positive half-space (``Outside``): only a plane narrows it."""
    t = surface.type_
    axis = {SurfaceType.PLANE_X: 0, SurfaceType.PLANE_Y: 1, SurfaceType.PLANE_Z: 2}.get(t)
    if axis is None:
        return UNBOUNDED
    at = _param(surface, "xyz"[axis] + "0")
    if at is None:
        return UNBOUNDED
    box = list(UNBOUNDED)
    box[2 * axis] = at
    return tuple(box)  # type: ignore[return-value]


def intersect_bounds(a: Bounds | None, b: Bounds | None) -> Bounds | None:
    if a is None or b is None:
        return None
    merged = tuple(max(a[i], b[i]) if i % 2 == 0 else min(a[i], b[i]) for i in range(6))
    if any(merged[2 * i] > merged[2 * i + 1] for i in range(3)):
        return None
    return merged  # type: ignore[return-value]


def hull_bounds(a: Bounds | None, b: Bounds | None) -> Bounds | None:
    if a is None:
        return b
    if b is None:
        return a
    return tuple(min(a[i], b[i]) if i % 2 == 0 else max(a[i], b[i]) for i in range(6))  # type: ignore[return-value]


def region_bounds(region: Region, surfaces: Mapping[str, Surface]) -> Bounds | None:
    """Conservative bounds of ``region``; surfaces missing from the map are unbounded."""
    if isinstance(region, Inside):
        surface = surfaces.get(region.surface_id)
        return UNBOUNDED if surface is None else surface_inside_bounds(surface)

    if isinstance(region, Outside):
        surface = surfaces.get(region.surface_id)
        return UNBOUNDED if surface is None else surface_outside_bounds(surface)

    if isinstance(region, Intersection):
        result: Bounds | None = UNBOUNDED
        for child in region.regions:
            result = intersect_bounds(result, region_bounds(child, surfaces))
            if result is None:
                return None
        return result

    if isinstance(region, Union):
        hull: Bounds | None = None
        for child in region.regions:
            hull = hull_bounds(hull, region_bounds(child, surfaces))
        return hull

    if isinstance(region, Complement):
        return UNBOUNDED  # the complement of a bounded region is not bounded

    return UNBOUNDED


def bounds_overlap(a: Bounds | None, b: Bounds | None, tol: float = OVERLAP_TOLERANCE) -> bool:
    """Whether two bounds share volume (merely touching faces do not count)."""
    if a is None or b is None:
        return False
    return all(a[2 * i] < b[2 * i + 1] - tol and b[2 * i] < a[2 * i + 1] - tol for i in range(3))
