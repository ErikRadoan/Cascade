"""Geometry routes — YAML validation, scene building, geometry CRUD."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..dsl import loader
from ..dsl import sweep
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
    raw_errors = sweep.validate_preview(body.text)
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
    errors = sweep.validate_preview(body.text)
    if errors:
        return SceneResponse(
            components=[],
            material_colors={},
            bounds=BoundsOut(x_min=0,x_max=1,y_min=0,y_max=1,z_min=0,z_max=1),
            error=errors[0]["message"],
        )

    try:
        schemas = sweep.preview_load(body.text)
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
    """List all saved geometry definitions, most recently created first."""
    result = [
        GeometrySummary(
            id=gid,
            name=entry["name"],
            created_at=entry["created_at"],
            n_surfaces=entry["n_surfaces"],
            n_cells=entry["n_cells"],
        )
        for gid, entry in _geometry_store.items()
    ]
    result.sort(key=lambda g: g.created_at, reverse=True)
    return result


@router.post("/", response_model=GeometrySummary, status_code=201)
async def save_geometry(body: GeometryTextRequest) -> GeometrySummary:
    """Save a geometry project's YAML text.

    Unlike the old behaviour, this does NOT require the geometry to be
    valid or expandable. A geometry project tab in the frontend autosaves
    on every edit, and the user is typing invalid/incomplete YAML for
    most of that time — rejecting drafts would make autosave useless.

    Validity is only enforced where it actually matters: job submission
    (POST /jobs/submit) parses and expands the YAML itself and will
    reject an invalid geometry there.

    The only requirement here is that the YAML parses as a mapping —
    i.e. loader.load() doesn't raise a structural LoadError. Pydantic
    field-validation errors on individual components (e.g. a FuelPin
    with a missing required field) are fine; n_surfaces/n_cells will
    just read 0 for those components until they're fixed.
    """
    from datetime import datetime, timezone
    from ..dsl import expander
    import uuid

    surfaces_count = 0
    cells_count = 0

    try:
        schemas = sweep.preview_load(body.text)
        try:
            geom = expander.expand(schemas)
            surfaces_count = len(geom.surfaces)
            cells_count = len(geom.cells)
        except Exception:
            # Expansion failed (e.g. references an undefined template,
            # or a placement is missing) — still a valid SAVE, just
            # reports 0 surfaces/cells until the user fixes it.
            pass
    except loader.LoadError as e:
        # Only reject truly malformed YAML (not a mapping, bad syntax)
        raise HTTPException(status_code=422, detail=str(e))

    gid = str(uuid.uuid4())
    created = datetime.now(timezone.utc)
    _geometry_store[gid] = {
        "name":       body.name or f"geometry_{gid[:8]}",
        "text":       body.text,
        "n_surfaces": surfaces_count,
        "n_cells":    cells_count,
        "created_at": created,
    }

    return GeometrySummary(
        id=gid,
        name=_geometry_store[gid]["name"],
        created_at=created,
        n_surfaces=surfaces_count,
        n_cells=cells_count,
    )


@router.put("/{geometry_id}", response_model=GeometrySummary)
async def update_geometry(geometry_id: str, body: GeometryTextRequest) -> GeometrySummary:
    """Update an existing geometry project's text (autosave target).

    Same relaxed-validity rules as save_geometry — drafts save freely.
    """
    from datetime import datetime, timezone
    from ..dsl import expander

    entry = _geometry_store.get(geometry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"Geometry '{geometry_id}' not found.")

    surfaces_count = 0
    cells_count = 0

    try:
        schemas = sweep.preview_load(body.text)
        try:
            geom = expander.expand(schemas)
            surfaces_count = len(geom.surfaces)
            cells_count = len(geom.cells)
        except Exception:
            pass
    except loader.LoadError as e:
        raise HTTPException(status_code=422, detail=str(e))

    entry["text"]       = body.text
    entry["n_surfaces"] = surfaces_count
    entry["n_cells"]    = cells_count
    if body.name:
        entry["name"] = body.name

    return GeometrySummary(
        id=geometry_id,
        name=entry["name"],
        created_at=entry["created_at"],
        n_surfaces=surfaces_count,
        n_cells=cells_count,
    )


@router.get("/{geometry_id}", response_model=GeometryDetail)
async def get_geometry(geometry_id: str) -> GeometryDetail:
    """Retrieve a saved geometry by ID."""
    entry = _geometry_store.get(geometry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"Geometry '{geometry_id}' not found.")

    return GeometryDetail(
        id=geometry_id,
        name=entry["name"],
        created_at=entry["created_at"],
        n_surfaces=entry["n_surfaces"],
        n_cells=entry["n_cells"],
        yaml_text=entry["text"],
        param_values={},
    )


@router.delete("/{geometry_id}", response_model=DeletedResponse)
async def delete_geometry(geometry_id: str) -> DeletedResponse:
    """Delete a saved geometry by ID."""
    if geometry_id not in _geometry_store:
        raise HTTPException(status_code=404, detail=f"Geometry '{geometry_id}' not found.")
    del _geometry_store[geometry_id]
    return DeletedResponse(id=geometry_id)