"""CSG export — serializes a CascadeGeometry into the flat surfaces+cells+
region-tree JSON shape the frontend's CSG viewers (2D slice plot, 3D
raymarcher) consume.

Shared by:
    - api/jobs.py's GET /jobs/{job_id}/csg      (a submitted job's geometry)
    - api/geometry.py's POST /geometry/csg       (the live editor's YAML)

Kept as one function so the two call sites can't drift apart in shape.

CSG_VIEWER_SCALING_PLAN.md Phase C: also serializes `lattice_instances`
(prototype + per-instance offsets — see domain/geometry.py's
LatticeInstance) alongside the existing flat `surfaces`/`cells` lists.
This is a purely additive field on the existing endpoint response (per
the plan's §4.4 decision — extend rather than add a new endpoint, since
an added optional field can't break either existing consumer): a client
that doesn't know about `lattice_instances` sees the exact same
`surfaces`/`cells` shape it always did. Empty list for geometry with no
lattice placements.
"""
from __future__ import annotations

from ..domain.geometry import CascadeGeometry, region_to_json


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
        "cells": [
            {
                "id": c.id,
                "material_id": c.material_id,
                "name": c.name,
                "region": region_to_json(c.region),
            }
            for c in geom.cells
        ],
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
            }
            for li in geom.lattice_instances
        ],
    }
