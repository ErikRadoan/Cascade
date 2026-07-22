from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..dsl import expander
from ..services.csg_export_service import geometry_to_csg_dict
from ..dsl import loader
from ..dsl import sweep
from ..repositories.db import get_db
from ..repositories.geometry_repositroy import GeometryRepository
from ..services.scene_builder_service import SceneBuilder
from .schemas import (
    BoundsOut,
    CylinderLayerOut,
    DeletedResponse,
    GeometryDetail,
    GeometrySummary,
    GeometryTextRequest,
    SceneComponentOut,
    SceneRequest,
    SceneResponse,
    ValidationError,
    ValidationResponse,
    WireframeBoxOut,
)

router = APIRouter(prefix="/geometry", tags=["geometry"])
_scene_builder = SceneBuilder()

# _geometry_store is GONE — replaced by GeometryRepository (DB-backed).

# ---------------------------------------------------------------------------
# Scene building — UNCHANGED, still shared by /geometry/scene and
# /jobs/{job_id}/scene. Not shown again here.
# ---------------------------------------------------------------------------

_EMPTY_BOUNDS = BoundsOut(x_min=0, x_max=1, y_min=0, y_max=1, z_min=0, z_max=1)


def build_scene_response(text: str) -> SceneResponse:
    # ... unchanged, keep exactly as-is ...
    errors = sweep.validate_preview(text)
    if errors:
        return SceneResponse(
            components=[], material_colors={}, bounds=_EMPTY_BOUNDS,
            error=errors[0]["message"],
        )
    try:
        schemas = sweep.preview_load(text)
        scene = _scene_builder.build(schemas)
    except Exception as e:
        return SceneResponse(
            components=[], material_colors={}, bounds=_EMPTY_BOUNDS,
            error=str(e),
        )
    components_out = []
    for comp in scene.components:
        layers_out = [
            CylinderLayerOut(
                r_inner=l.r_inner, r_outer=l.r_outer, height=l.height, z_base=l.z_base,
                material_id=l.material_id, color=l.color, opacity=l.opacity,
                label=l.label, cell_name=l.cell_name,
            )
            for l in comp.layers
        ]
        box_out = None
        if comp.box:
            b = comp.box
            box_out = WireframeBoxOut(
                x_size=b.x_size, y_size=b.y_size, z_size=b.z_size, z_base=b.z_base,
                color=b.color, boundary_type=b.boundary_type,
                fill_material_id=b.fill_material_id, fill_color=b.fill_color,
                fill_opacity=b.fill_opacity, cell_name=b.cell_name,
            )
        components_out.append(SceneComponentOut(
            type=comp.type, name=comp.name, position=list(comp.position),
            layers=layers_out, box=box_out,
        ))
    b = scene.bounds
    return SceneResponse(
        components=components_out,
        material_colors=scene.material_colors,
        bounds=BoundsOut(x_min=b[0], x_max=b[1], y_min=b[2], y_max=b[3], z_min=b[4], z_max=b[5]),
    )


# ---------------------------------------------------------------------------
# Validation and scene — UNCHANGED
# ---------------------------------------------------------------------------

@router.post("/validate", response_model=ValidationResponse)
async def validate_geometry(body: GeometryTextRequest) -> ValidationResponse:
    raw_errors = sweep.validate_preview(body.text)
    errors = [ValidationError(**e) for e in raw_errors]
    return ValidationResponse(valid=len(errors) == 0, errors=errors)


@router.post("/scene", response_model=SceneResponse)
async def build_scene(body: SceneRequest) -> SceneResponse:
    return build_scene_response(body.text)


# ---------------------------------------------------------------------------
# CRUD — now DB-backed via GeometryRepository
# ---------------------------------------------------------------------------

def _compute_counts(text: str) -> tuple[int, int]:
    """Best-effort surface/cell counts — 0 if the geometry doesn't expand
    yet (drafts are allowed to be invalid, per this module's original
    docstring on save_geometry/update_geometry)."""
    from ..dsl import expander
    try:
        schemas = sweep.preview_load(text)
        geom = expander.expand(schemas)
        return len(geom.surfaces), len(geom.cells)
    except Exception:
        return 0, 0


@router.get("/", response_model=list[GeometrySummary])
async def list_geometries(db: Session = Depends(get_db)) -> list[GeometrySummary]:
    """List all saved geometry definitions, most recently created first."""
    records = GeometryRepository(db).list()
    return [
        GeometrySummary(
            id=r.id, name=r.name, created_at=r.created_at,
            n_surfaces=r.n_surfaces, n_cells=r.n_cells,
        )
        for r in records
    ]


@router.post("/", response_model=GeometrySummary, status_code=201)
async def save_geometry(body: GeometryTextRequest, db: Session = Depends(get_db)) -> GeometrySummary:
    """Save a geometry project's YAML text.

    Same relaxed-validity rules as before: only rejects text that isn't
    even parseable as a YAML mapping (loader.LoadError). Field-validation
    errors on individual components are fine — n_surfaces/n_cells just
    read 0 until fixed. See _compute_counts().
    """
    try:
        sweep.preview_load(body.text)
    except loader.LoadError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception:
        pass  # non-structural error — still a valid save, counts will be 0

    n_surfaces, n_cells = _compute_counts(body.text)

    repo = GeometryRepository(db)
    record = repo.create(
        name=body.name or "",  # placeholder, fixed below once we have the id
        text=body.text,
        n_surfaces=n_surfaces,
        n_cells=n_cells,
    )
    if not body.name:
        record = repo.update(record.id, text=body.text, name=f"geometry_{record.id[:8]}",
                              n_surfaces=n_surfaces, n_cells=n_cells)

    return GeometrySummary(
        id=record.id, name=record.name, created_at=record.created_at,
        n_surfaces=record.n_surfaces, n_cells=record.n_cells,
    )


@router.put("/{geometry_id}", response_model=GeometrySummary)
async def update_geometry(geometry_id: str, body: GeometryTextRequest, db: Session = Depends(get_db)) -> GeometrySummary:
    """Update an existing geometry project's text (autosave target)."""
    try:
        sweep.preview_load(body.text)
    except loader.LoadError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception:
        pass

    n_surfaces, n_cells = _compute_counts(body.text)

    record = GeometryRepository(db).update(
        geometry_id, text=body.text, name=body.name,
        n_surfaces=n_surfaces, n_cells=n_cells,
    )
    if record is None:
        raise HTTPException(status_code=404, detail=f"Geometry '{geometry_id}' not found.")

    return GeometrySummary(
        id=record.id, name=record.name, created_at=record.created_at,
        n_surfaces=record.n_surfaces, n_cells=record.n_cells,
    )


@router.get("/{geometry_id}", response_model=GeometryDetail)
async def get_geometry(geometry_id: str, db: Session = Depends(get_db)) -> GeometryDetail:
    """Retrieve a saved geometry by ID."""
    record = GeometryRepository(db).get(geometry_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Geometry '{geometry_id}' not found.")

    return GeometryDetail(
        id=record.id, name=record.name, created_at=record.created_at,
        n_surfaces=record.n_surfaces, n_cells=record.n_cells,
        yaml_text=record.text, param_values={},
    )


@router.delete("/{geometry_id}", response_model=DeletedResponse)
async def delete_geometry(geometry_id: str, db: Session = Depends(get_db)) -> DeletedResponse:
    """Delete a saved geometry by ID."""
    deleted = GeometryRepository(db).delete(geometry_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Geometry '{geometry_id}' not found.")
    return DeletedResponse(id=geometry_id)

@router.post("/csg")
async def build_csg(body: SceneRequest) -> dict:
    """Fully-expanded CSG (surfaces + cells + region trees) for live editor
    text — the general-purpose counterpart to /scene's template-aware
    SceneBuilder output. Renders arbitrary surfaces/regions, including
    future Union/Subtraction component types, unlike /scene which only
    knows FuelPin/Box.

    Mirrors GET /jobs/{job_id}/csg for a job that hasn't been submitted yet.
    """
    errors = sweep.validate_preview(body.text)
    if errors:
        raise HTTPException(status_code=422, detail=errors[0]["message"])
    try:
        schemas = sweep.preview_load(body.text)
        geometry = expander.expand(schemas)
    except Exception as e:
        raise HTTPException(status_code=422, detail=str(e))
    return geometry_to_csg_dict(geometry)