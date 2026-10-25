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
from collections import defaultdict
from collections.abc import Mapping, Sequence
from statistics import median

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
    x0 = a[0] if a[0] > b[0] else b[0]
    x1 = a[1] if a[1] < b[1] else b[1]
    y0 = a[2] if a[2] > b[2] else b[2]
    y1 = a[3] if a[3] < b[3] else b[3]
    z0 = a[4] if a[4] > b[4] else b[4]
    z1 = a[5] if a[5] < b[5] else b[5]
    if x0 > x1 or y0 > y1 or z0 > z1:
        return None
    return (x0, x1, y0, y1, z0, z1)


def hull_bounds(a: Bounds | None, b: Bounds | None) -> Bounds | None:
    if a is None:
        return b
    if b is None:
        return a
    return (
        a[0] if a[0] < b[0] else b[0], a[1] if a[1] > b[1] else b[1],
        a[2] if a[2] < b[2] else b[2], a[3] if a[3] > b[3] else b[3],
        a[4] if a[4] < b[4] else b[4], a[5] if a[5] > b[5] else b[5],
    )


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
    return (
        a[0] < b[1] - tol and b[0] < a[1] - tol
        and a[2] < b[3] - tol and b[2] < a[3] - tol
        and a[4] < b[5] - tol and b[4] < a[5] - tol
    )


def _is_finite(bounds: Bounds) -> bool:
    return all(math.isfinite(v) for v in bounds)


# An item whose bounds span more grid cells than this is not registered in the
# grid; it is tested directly against every query instead (see BoundsIndex).
_MAX_CELLS_PER_ITEM = 64


class BoundsIndex:
    """Which of a fixed list of bounds overlap a query box, without testing all of them.

    Finding every overlapping pair of N boxes by testing each pair costs N^2
    checks, which is what made lattice booleans slow down quadratically. This
    index hashes each box into a uniform grid, so a query only looks at the
    boxes that share a grid cell with it. For lattices and other evenly spread
    shapes that is a handful of boxes, so N queries cost about N in total.

    ``overlapping`` returns exactly the indices a plain scan with
    ``bounds_overlap`` would, in ascending order. Boxes the grid can't hold
    (unbounded, or much larger than a typical box) are checked directly on
    every query, so the result is never a subset.
    """

    def __init__(self, bounds: Sequence[Bounds | None]) -> None:
        self._bounds = bounds
        self._direct: list[int] = []
        self._cells: dict[tuple[int, int, int], list[int]] = defaultdict(list)

        gridded = [b for b in bounds if b is not None and _is_finite(b)]
        # The median extent keeps one huge box (a moderator body) from stretching
        # the cells so far that every pin lands in the same one.
        self._size = [
            max(median(b[2 * axis + 1] - b[2 * axis] for b in gridded), 1e-6) if gridded else 1.0
            for axis in range(3)
        ]

        for index, b in enumerate(bounds):
            if b is None:
                continue
            if not _is_finite(b):
                self._direct.append(index)
                continue
            ranges = self._ranges(b)
            count = len(ranges[0]) * len(ranges[1]) * len(ranges[2])
            if count > _MAX_CELLS_PER_ITEM:
                self._direct.append(index)
                continue
            for i in ranges[0]:
                for j in ranges[1]:
                    for k in ranges[2]:
                        self._cells[(i, j, k)].append(index)

    def _ranges(self, b: Bounds) -> tuple[range, range, range]:
        size = self._size
        return (
            range(math.floor(b[0] / size[0]), math.floor(b[1] / size[0]) + 1),
            range(math.floor(b[2] / size[1]), math.floor(b[3] / size[1]) + 1),
            range(math.floor(b[4] / size[2]), math.floor(b[5] / size[2]) + 1),
        )

    def overlapping(self, query: Bounds | None) -> list[int]:
        """Indices of the stored bounds that share volume with ``query``, ascending."""
        if query is None:
            return []
        bounds = self._bounds

        if not _is_finite(query):
            return [i for i, b in enumerate(bounds) if bounds_overlap(query, b)]

        ranges = self._ranges(query)
        if len(ranges[0]) * len(ranges[1]) * len(ranges[2]) > _MAX_CELLS_PER_ITEM * 8:
            # A query much larger than the grid cells would visit thousands of
            # cells; a straight scan is cheaper.
            return [i for i, b in enumerate(bounds) if bounds_overlap(query, b)]

        candidates: set[int] = set(self._direct)
        cells = self._cells
        for i in ranges[0]:
            for j in ranges[1]:
                for k in ranges[2]:
                    found = cells.get((i, j, k))
                    if found:
                        candidates.update(found)
        return sorted(i for i in candidates if bounds_overlap(query, bounds[i]))
