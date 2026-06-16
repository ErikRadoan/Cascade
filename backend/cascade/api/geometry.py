"""Geometry routes — YAML validation, scene building, geometry CRUD."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..dsl import loader
from ..services.scene_builder import SceneBuilder
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

# In-memory geometry store — replace with repository when DB is wired up.
# Maps geometry_id -> {"name": str, "text": str, "geometry": CascadeGeometry}
_geometry_store: dict[str, dict] = {}


# ---------------------------------------------------------------------------
# Validation and scene
# ---------------------------------------------------------------------------

@router.post("/validate", response_model=ValidationResponse)
async def validate_geometry(body: GeometryTextRequest) -> ValidationResponse:
    """Validate YAML geometry text without expanding or storing it.

    Returns immediately — no containers, no files. Suitable for calling
    on every editor keystroke (with debounce on the frontend).

    Returns a list of structured errors with component and field names
    so the editor can underline the offending lines.
    """
    raw_errors = loader.validate(body.text)
    errors = [ValidationError(**e) for e in raw_errors]
    return ValidationResponse(valid=len(errors) == 0, errors=errors)


@router.post("/scene", response_model=SceneResponse)
async def build_scene(body: SceneRequest) -> SceneResponse:
    """Build a 3D scene description from YAML geometry text.

    Parses and expands the YAML, then maps each component to
    Three.js-renderable objects (cylinder layers, wireframe boxes).
    Returns colors, opacity, and bounds — the frontend renders directly.

    Called when validation passes and the preview needs to update.
    """
    errors = loader.validate(body.text)
    if errors:
        return SceneResponse(
            components=[],
            material_colors={},
            bounds=BoundsOut(x_min=0,x_max=1,y_min=0,y_max=1,z_min=0,z_max=1),
            error=errors[0]["message"],
        )

    try:
        schemas = loader.load(body.text)
        scene = _scene_builder.build(schemas)
    except Exception as e:
        return SceneResponse(
            components=[],
            material_colors={},
            bounds=BoundsOut(x_min=0,x_max=1,y_min=0,y_max=1,z_min=0,z_max=1),
            error=str(e),
        )

    components_out = []
    for comp in scene.components:
        layers_out = [
            CylinderLayerOut(
                r_inner=l.r_inner, r_outer=l.r_outer,
                height=l.height,   z_base=l.z_base,
                material_id=l.material_id, color=l.color,
                opacity=l.opacity, label=l.label,
            )
            for l in comp.layers
        ]
        box_out = None
        if comp.box:
            b = comp.box
            box_out = WireframeBoxOut(
                x_size=b.x_size, y_size=b.y_size, z_size=b.z_size,
                z_base=b.z_base, color=b.color,
                boundary_type=b.boundary_type,
                fill_material_id=b.fill_material_id,
                fill_color=b.fill_color, fill_opacity=b.fill_opacity,
            )
        components_out.append(SceneComponentOut(
            type=comp.type, name=comp.name,
            position=list(comp.position),
            layers=layers_out, box=box_out,
        ))

    b = scene.bounds
    return SceneResponse(
        components=components_out,
        material_colors=scene.material_colors,
        bounds=BoundsOut(
            x_min=b[0], x_max=b[1],
            y_min=b[2], y_max=b[3],
            z_min=b[4], z_max=b[5],
        ),
    )


# ---------------------------------------------------------------------------
# CRUD — saved geometries
# ---------------------------------------------------------------------------

@router.get("/", response_model=list[GeometrySummary])
async def list_geometries() -> list[GeometrySummary]:
    """List all saved geometry definitions."""
    from datetime import timezone
    result = []
    for gid, entry in _geometry_store.items():
        geom = entry["geometry"]
        result.append(GeometrySummary(
            id=gid,
            name=entry["name"],
            created_at=entry["created_at"],
            n_surfaces=len(geom.surfaces),
            n_cells=len(geom.cells),
        ))
    return result


@router.post("/", response_model=GeometrySummary, status_code=201)
async def save_geometry(body: GeometryTextRequest) -> GeometrySummary:
    """Parse, expand, and save a geometry definition.

    Validates and expands the YAML — if either step fails, returns 422.
    On success, stores the resolved geometry and returns a summary.
    """
    from datetime import datetime, timezone
    from ..dsl import expander
    import uuid

    errors = loader.validate(body.text)
    if errors:
        raise HTTPException(
            status_code=422,
            detail=errors,
        )

    schemas = loader.load(body.text)

    try:
        geom = expander.expand(schemas)
    except Exception as e:
        raise HTTPException(status_code=422, detail=str(e))

    gid = str(uuid.uuid4())
    created = datetime.now(timezone.utc)
    _geometry_store[gid] = {
        "name":       body.name or f"geometry_{gid[:8]}",
        "text":       body.text,
        "geometry":   geom,
        "created_at": created,
    }

    return GeometrySummary(
        id=gid,
        name=_geometry_store[gid]["name"],
        created_at=created,
        n_surfaces=len(geom.surfaces),
        n_cells=len(geom.cells),
    )


@router.get("/{geometry_id}", response_model=GeometryDetail)
async def get_geometry(geometry_id: str) -> GeometryDetail:
    """Retrieve a saved geometry by ID."""
    entry = _geometry_store.get(geometry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"Geometry '{geometry_id}' not found.")

    geom = entry["geometry"]
    return GeometryDetail(
        id=geometry_id,
        name=entry["name"],
        created_at=entry["created_at"],
        n_surfaces=len(geom.surfaces),
        n_cells=len(geom.cells),
        yaml_text=entry["text"],
        param_values=geom.param_values,
    )


@router.delete("/{geometry_id}", response_model=DeletedResponse)
async def delete_geometry(geometry_id: str) -> DeletedResponse:
    """Delete a saved geometry by ID."""
    if geometry_id not in _geometry_store:
        raise HTTPException(status_code=404, detail=f"Geometry '{geometry_id}' not found.")
    del _geometry_store[geometry_id]
    return DeletedResponse(id=geometry_id)