"""Materials routes — CRUD for the user's material library.

Storage is now file-backed (~/.cascade/materials.json) via MaterialRepository.
The in-memory dict is gone — mutations persist across server restarts.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Query, UploadFile, File

from ..domain.material import Material
from ..repositories.material_repository import MaterialRepository
from .schemas import (
    DeletedResponse,
    MaterialCreateRequest,
    MaterialDetail,
    MaterialImportResponse,
    MaterialSummary,
)

router = APIRouter(prefix="/materials", tags=["materials"])

# Module-level singleton — one repository per server process, shared
# across requests. Thread-safe for reads; writes use atomic file replace.
_repo = MaterialRepository()


def _to_summary(mat: Material, library_tag: str | None = None) -> MaterialSummary:
    return MaterialSummary(id=mat.id, name=mat.name, density=mat.density)


def _to_detail(mat: Material) -> MaterialDetail:
    return MaterialDetail(
        id=mat.id, name=mat.name,
        density=mat.density,
        composition=mat.composition,
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/", response_model=dict)
async def list_materials(
    search:      str        = Query("",   description="Free-text search by id, name, or nuclide."),
    library_tag: str | None = Query(None, description="Filter by library tag."),
    limit:       int        = Query(50,   ge=1, le=500),
    offset:      int        = Query(0,    ge=0),
) -> dict:
    """List materials with optional search and pagination.

    Returns:
        {
            "items": [ MaterialSummary, ... ],
            "total": int,           # total count before pagination
            "limit": int,
            "offset": int,
        }
    """
    materials, total = _repo.search(
        query=search, library_tag=library_tag, limit=limit, offset=offset,
    )
    return {
        "items":  [_to_summary(m) for m in materials],
        "total":  total,
        "limit":  limit,
        "offset": offset,
    }


@router.get("/libraries", response_model=list[str])
async def list_libraries() -> list[str]:
    """Return all distinct library tags in the material library.

    Used by the frontend to populate the library filter dropdown and the
    'which library does this belong to' label in the material editor.
    """
    return _repo.list_libraries()


@router.post("/", response_model=MaterialDetail, status_code=201)
async def create_material(
    body:        MaterialCreateRequest,
    library_tag: str = Query("user", description="Library tag for this material."),
) -> MaterialDetail:
    """Add a new material to the library."""
    mat_id = body.name.replace(" ", "_").upper()

    mat = Material(
        id=mat_id, name=body.name,
        density=body.density, composition=body.composition,
    )
    try:
        _repo.save(mat, library_tag=library_tag)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))

    return _to_detail(mat)


@router.get("/{material_id}", response_model=MaterialDetail)
async def get_material(material_id: str) -> MaterialDetail:
    """Get a material by ID."""
    mat = _repo.get(material_id)
    if mat is None:
        raise HTTPException(status_code=404, detail=f"Material '{material_id}' not found.")
    return _to_detail(mat)


@router.put("/{material_id}", response_model=MaterialDetail)
async def update_material(material_id: str, body: MaterialCreateRequest) -> MaterialDetail:
    """Update an existing material's composition or density."""
    existing = _repo.get(material_id)
    if existing is None:
        raise HTTPException(status_code=404, detail=f"Material '{material_id}' not found.")

    updated = Material(
        id=material_id, name=body.name,
        density=body.density, composition=body.composition,
    )
    try:
        _repo.update(updated)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))

    return _to_detail(updated)


@router.delete("/{material_id}", response_model=DeletedResponse)
async def delete_material(material_id: str) -> DeletedResponse:
    """Delete a material from the library."""
    try:
        _repo.delete(material_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Material '{material_id}' not found.")
    return DeletedResponse(id=material_id)


@router.post("/import/json", response_model=MaterialImportResponse)
async def import_json(
    file:        UploadFile = File(...),
    library_tag: str        = Query("imported", description="Tag for all imported materials."),
    overwrite:   bool       = Query(False, description="Overwrite existing materials with same ID."),
) -> MaterialImportResponse:
    """Import materials from a Cascade-format JSON file.

    Expected format — a JSON array of material objects:
    [
        {
            "id": "UO2_5pct",
            "name": "UO2 5% enriched",
            "density": 10.97,
            "composition": {"U235": 0.05, "U238": 0.95, "O16": 2.0}
        },
        ...
    ]

    All imported materials are tagged with library_tag so they can be
    filtered or removed as a group later.
    """
    content = await file.read()

    try:
        data = json.loads(content)
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {e}")

    if not isinstance(data, list):
        raise HTTPException(
            status_code=400,
            detail="Expected a JSON array of material objects at the top level.",
        )

    imported_ids, skipped_ids, errors = _repo.import_batch(
        records=data, library_tag=library_tag, overwrite=overwrite,
    )

    imported_mats = [_to_summary(_repo.get(mid)) for mid in imported_ids if _repo.get(mid)]

    return MaterialImportResponse(
        imported=imported_mats,
        skipped=skipped_ids,
        errors=errors,
    )


@router.delete("/library/{library_tag}", response_model=dict)
async def delete_library(library_tag: str) -> dict:
    """Delete all materials belonging to a specific library tag.

    Useful for removing an entire imported library set at once.
    Does not affect materials with different tags.
    Builtin materials (library_tag='builtin') cannot be deleted this way.
    """
    if library_tag == "builtin":
        raise HTTPException(
            status_code=403,
            detail="Built-in materials cannot be deleted as a library. Delete them individually.",
        )

    all_mats = _repo.list_all()
    # Re-read with tags — need raw data for this
    raw = _repo._read()
    to_delete = [
        mid for mid, record in raw.items()
        if record.get("library_tag") == library_tag
    ]

    for mid in to_delete:
        try:
            _repo.delete(mid)
        except KeyError:
            pass

    return {"deleted_count": len(to_delete), "library_tag": library_tag}


def get_materials_by_ids(ids: list[str]) -> list[Material]:
    """Internal helper used by the jobs router to resolve material IDs."""
    result: list[Material] = []
    missing: list[str] = []

    for mid in ids:
        mat = _repo.get(mid)
        if mat is None:
            missing.append(mid)
        else:
            result.append(mat)

    if missing:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown material IDs: {missing}. Add them to the library first.",
        )
    return result