"""CSG export — serializes a CascadeGeometry into the flat surfaces+cells+
region-tree JSON shape the frontend's CSG viewers consume.

Also includes ``warnings`` (soft expansion messages) so the editor can
show them without treating the geometry as failed.
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
                "inner_offsets": [list(p) for p in li.inner_offsets],
            }
            for li in geom.lattice_instances
        ],
        "warnings": list(getattr(geom, "warnings", None) or []),
    }
