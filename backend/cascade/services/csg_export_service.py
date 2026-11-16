"""CSG export — serializes a CascadeGeometry into the flat surfaces+cells+
region-tree JSON shape the frontend's CSG viewers consume.

Also includes ``warnings`` (soft expansion messages) so the editor can
show them without treating the geometry as failed.
"""
from __future__ import annotations

from ..domain.geometry import CascadeGeometry, region_to_json


# The root universe id. A cell with any other ``universe_id`` is defined in the
# LOCAL frame of that universe, and only appears in the world through a fill
# cell that references it (see _cell_to_dict).
ROOT_UNIVERSE = "0"


def _cell_to_dict(c) -> dict:
    """One cell as the frontend consumes it.

    ``universe_id`` / ``fill`` / ``translation`` / ``rotation`` are ADDITIVE and
    only present when they matter, so consumers that ignore them (the lattice
    prototypes, older clients) see exactly the old shape. They are required for
    rotated placements: those are built as a local universe at the origin that a
    fill cell stamps into the root at ``translation`` / ``rotation``. Without them
    the viewer cannot tell a universe-local cell (origin frame, unrotated) from a
    root cell, and would draw it at the origin: for a rotated object that showed
    up as a stray sphere (the local background cell) instead of the object.

    ``rotation`` is the placement's (rx, ry, rz) in degrees, exactly what is
    written to OpenMC; the viewer must apply OpenMC's convention (see
    CsgViewportPanel.svelte, eulerXyzToQuat).
    """
    d = {
        "id": c.id,
        "material_id": c.material_id,
        "name": c.name,
        "region": region_to_json(c.region),
    }
    if c.universe_id is not None and c.universe_id != ROOT_UNIVERSE:
        d["universe_id"] = c.universe_id
    if c.fill is not None:
        d["fill"] = c.fill
        if c.translation is not None:
            d["translation"] = list(c.translation)
        if c.rotation is not None:
            d["rotation"] = list(c.rotation)
    return d


def geometry_to_csg_dict(geom: CascadeGeometry) -> dict:
    return {
        "surfaces": [
            {
                "id": s.id,
                "type": s.type_.value,
                "params": s.params,
                "boundary_type": s.boundary_type.value,
            }
            for s in geom.surfaces
        ],
        "cells": [_cell_to_dict(c) for c in geom.cells],
        "lattice_instances": [
            {
                "lattice_name": li.lattice_name,
                "prototype_key": li.prototype_key,
                "prototype_surfaces": [
                    {
                        "id": s.id,
                        "type": s.type_.value,
                        "params": s.params,
                        "boundary_type": s.boundary_type.value,
                    }
                    for s in li.prototype_surfaces
                ],
                "prototype_cells": [
                    {
                        "id": c.id,
                        "material_id": c.material_id,
                        "name": c.name,
                        "region": region_to_json(c.region),
                    }
                    for c in li.prototype_cells
                ],
                "instances": [list(p) for p in li.instances],
                "inner_offsets": [list(p) for p in li.inner_offsets],
            }
            for li in geom.lattice_instances
        ],
        "warnings": list(getattr(geom, "warnings", None) or []),
    }
