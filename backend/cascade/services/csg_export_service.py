"""CSG export — serializes a CascadeGeometry into the flat surfaces+cells+
region-tree JSON shape the frontend's CSG viewers (2D slice plot, 3D
raymarcher) consume.

Shared by:
    - api/jobs.py's GET /jobs/{job_id}/csg      (a submitted job's geometry)
    - api/geometry.py's POST /geometry/csg       (the live editor's YAML)

Kept as one function so the two call sites can't drift apart in shape.
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
    }