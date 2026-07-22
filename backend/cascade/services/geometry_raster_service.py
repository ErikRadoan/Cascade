"""Server-side CSG slice rasterization.

Replaces the point-classification GeometryPlotPanel.svelte used to do in
JS (per-pixel loop through a hand-rolled spatial index over the CSG tree
fetched from /csg). That approach is O(pixels x cells) in interpreted
JS and stops scaling once a lattice gets into the thousands-of-cells
range.

Here the same signed-distance-function region evaluation is vectorized
with numpy: every surface's implicit function is evaluated ONCE over the
whole pixel grid (O(surfaces) array ops, each O(resolution^2)), and each
cell's region mask is built from a handful of boolean combinations of
already-computed grids — no per-pixel Python loop, ever.

Sign convention matches the domain model / OpenMC convention used
elsewhere (openmc_adapter.py, GeometryPlotPanel.svelte's old surfaceF):
    f(point) < 0  -> inside the surface (Region.Inside)
    f(point) >= 0 -> outside the surface (Region.Outside)
"""

from __future__ import annotations

import numpy as np

from ..domain.geometry import (
    CascadeGeometry, Complement, Inside, Intersection, Outside, Region,
    Surface, SurfaceType, Union,
)


def _resolve(params: dict, canonical: str, alias: str) -> float:
    if canonical in params:
        return float(params[canonical])
    if alias in params:
        return float(params[alias])
    return 0.0


def _surface_grid(surface: Surface, X: np.ndarray, Y: np.ndarray, Z: np.ndarray) -> np.ndarray:
    """Signed implicit-function value over the whole pixel grid.

    Mirrors GeometryPlotPanel.svelte's old surfaceF(), one branch per
    SurfaceType the expander actually emits (Box/FuelPin/lattices).
    cone_z/torus aren't emitted by anything today — same as the JS
    version, they degrade to "always outside" rather than erroring.
    """
    p = surface.params
    t = surface.type_

    if t == SurfaceType.PLANE_X:
        return X - _resolve(p, "x0", "x")
    if t == SurfaceType.PLANE_Y:
        return Y - _resolve(p, "y0", "y")
    if t == SurfaceType.PLANE_Z:
        return Z - _resolve(p, "z0", "z")
    if t == SurfaceType.CYLINDER_Z:
        x0, y0 = _resolve(p, "x0", "x"), _resolve(p, "y0", "y")
        r = float(p.get("r", 1.0))
        return (X - x0) ** 2 + (Y - y0) ** 2 - r * r
    if t == SurfaceType.CYLINDER_X:
        y0, z0 = _resolve(p, "y0", "y"), _resolve(p, "z0", "z")
        r = float(p.get("r", 1.0))
        return (Y - y0) ** 2 + (Z - z0) ** 2 - r * r
    if t == SurfaceType.CYLINDER_Y:
        x0, z0 = _resolve(p, "x0", "x"), _resolve(p, "z0", "z")
        r = float(p.get("r", 1.0))
        return (X - x0) ** 2 + (Z - z0) ** 2 - r * r
    if t == SurfaceType.SPHERE:
        x0, y0, z0 = _resolve(p, "x0", "x"), _resolve(p, "y0", "y"), _resolve(p, "z0", "z")
        r = float(p.get("r", 1.0))
        return (X - x0) ** 2 + (Y - y0) ** 2 + (Z - z0) ** 2 - r * r

    return np.ones_like(X)  # cone_z / torus: unsupported, always "outside"


def _region_mask(region: Region, grids: dict[str, np.ndarray], shape: tuple[int, int]) -> np.ndarray:
    if isinstance(region, Inside):
        g = grids.get(region.surface_id)
        return (g < 0) if g is not None else np.zeros(shape, dtype=bool)
    if isinstance(region, Outside):
        g = grids.get(region.surface_id)
        return (g >= 0) if g is not None else np.zeros(shape, dtype=bool)
    if isinstance(region, Intersection):
        mask = np.ones(shape, dtype=bool)
        for r in region.regions:
            mask &= _region_mask(r, grids, shape)
        return mask
    if isinstance(region, Union):
        mask = np.zeros(shape, dtype=bool)
        for r in region.regions:
            mask |= _region_mask(r, grids, shape)
        return mask
    if isinstance(region, Complement):
        return ~_region_mask(region.region, grids, shape)
    raise TypeError(f"Unknown Region type: {type(region).__name__}")


def rasterize_slice(
    geometry: CascadeGeometry,
    axis: str,
    coord: float,
    h_range: tuple[float, float],
    v_range: tuple[float, float],
    resolution: int,
) -> dict:
    """Rasterize one axis-aligned slice through `geometry`.

    `axis` is the FIXED axis (the one being sliced through, at `coord`).
    `h_range`/`v_range` are the bounds of the other two axes, in the same
    (horizontal, vertical) convention GeometryPlotPanel.svelte already
    used: axis='z' -> (x, y), axis='y' -> (x, z), axis='x' -> (y, z).
    Row 0 of the output is the TOP of the image (max of v_range), matching
    the "+axis points up" convention the frontend already renders with.

    Returns:
        {
          "width": int, "height": int,
          "cell_index": [int, ...],   # flattened row-major, len = width*height
                                       # -1 = void (no cell matched)
          "legend": [{"cell_name": str|None, "material_id": str|None}, ...]
                                       # indexed by the values in cell_index
        }
    """
    n = resolution
    h_lo, h_hi = h_range
    v_lo, v_hi = v_range
    if not (h_hi > h_lo and v_hi > v_lo):
        raise ValueError("h_range/v_range must each have max > min.")

    h = np.linspace(h_lo, h_hi, n)
    v = np.linspace(v_hi, v_lo, n)  # descending -> row 0 is +v (top of image)
    H, V = np.meshgrid(h, v)

    if axis == "z":
        X, Y, Z = H, V, np.full_like(H, coord)
    elif axis == "y":
        X, Y, Z = H, np.full_like(H, coord), V
    elif axis == "x":
        X, Y, Z = np.full_like(H, coord), H, V
    else:
        raise ValueError(f"axis must be 'x', 'y', or 'z', got {axis!r}.")

    shape = (n, n)
    grids = {s.id: _surface_grid(s, X, Y, Z) for s in geometry.surfaces}

    cell_index = np.full(shape, -1, dtype=np.int32)
    unresolved = np.ones(shape, dtype=bool)

    for idx, cell in enumerate(geometry.cells):
        if not unresolved.any():
            break
        hit = _region_mask(cell.region, grids, shape) & unresolved
        if hit.any():
            cell_index[hit] = idx
            unresolved &= ~hit

    legend = [
        {"cell_name": c.name, "material_id": c.material_id}
        for c in geometry.cells
    ]

    return {
        "width": n,
        "height": n,
        "cell_index": cell_index.reshape(-1).tolist(),
        "legend": legend,
    }