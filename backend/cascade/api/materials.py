"""Materials routes — CRUD for the user's material library."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException, UploadFile, File

from ..domain.material import Material
from .schemas import (
    DeletedResponse,
    MaterialCreateRequest,
    MaterialDetail,
    MaterialImportResponse,
    MaterialSummary,
)

router = APIRouter(prefix="/materials", tags=["materials"])

# In-memory material library — replace with repository when DB is wired up.
# Pre-seeded with the most common PWR materials so the user can run a fuel
# pin simulation without importing anything first.
_materials: dict[str, Material] = {
    "UO2": Material(
        id="UO2", name="Uranium Dioxide", density=10.97,
        composition={"U235": 0.03072, "U238": 0.96928, "O16": 2.0},
    ),
    "He": Material(
        id="He", name="Helium Gap", density=0.0001786,
        composition={"He4": 1.0},
    ),
    "Zr4": Material(
        id="Zr4", name="Zircaloy-4", density=6.56,
        composition={
            "Zr90": 0.5145, "Zr91": 0.1122, "Zr92": 0.1715,
            "Zr94": 0.1738, "Zr96": 0.0280,
        },
    ),
    "H2O": Material(
        id="H2O", name="Light Water", density=0.7,
        composition={"H1": 2.0, "O16": 1.0},
    ),
    "B4C": Material(
        id="B4C", name="Boron Carbide", density=2.52,
        composition={"B10": 0.144, "B11": 0.576, "C12": 0.28},
    ),
    "SS316": Material(
        id="SS316", name="Stainless Steel 316", density=8.0,
        composition={
            "Fe56": 0.6395, "Cr52": 0.170, "Ni58": 0.120,
            "Mo98": 0.025,  "Mn55": 0.020, "Si28": 0.010,
        },
    ),
}


def _to_summary(mat: Material) -> MaterialSummary:
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

@router.get("/", response_model=list[MaterialSummary])
async def list_materials() -> list[MaterialSummary]:
    """List all materials in the library."""
    return [_to_summary(m) for m in _materials.values()]


@router.post("/", response_model=MaterialDetail, status_code=201)
async def create_material(body: MaterialCreateRequest) -> MaterialDetail:
    """Add a new material to the library.

    The material ID is derived from the name (spaces → underscores,
    lowercased). If a material with the same ID already exists, returns 409.
    """
    mat_id = body.name.replace(" ", "_").upper()
    if mat_id in _materials:
        raise HTTPException(
            status_code=409,
            detail=f"Material '{mat_id}' already exists. Delete it first or use a different name.",
        )

    mat = Material(
        id=mat_id,
        name=body.name,
        density=body.density,
        composition=body.composition,
    )
    _materials[mat_id] = mat
    return _to_detail(mat)


@router.get("/{material_id}", response_model=MaterialDetail)
async def get_material(material_id: str) -> MaterialDetail:
    """Get a material by ID."""
    mat = _materials.get(material_id)
    if mat is None:
        raise HTTPException(status_code=404, detail=f"Material '{material_id}' not found.")
    return _to_detail(mat)


@router.delete("/{material_id}", response_model=DeletedResponse)
async def delete_material(material_id: str) -> DeletedResponse:
    """Delete a material from the library."""
    if material_id not in _materials:
        raise HTTPException(status_code=404, detail=f"Material '{material_id}' not found.")
    del _materials[material_id]
    return DeletedResponse(id=material_id)


@router.post("/import", response_model=MaterialImportResponse)
async def import_materials(file: UploadFile = File(...)) -> MaterialImportResponse:
    """Import materials from a JSON file.

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

    Materials whose ID already exists are skipped (not overwritten).
    Returns counts of imported, skipped, and errored entries.
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

    imported: list[MaterialSummary] = []
    skipped:  list[str]             = []
    errors:   list[str]             = []

    for i, item in enumerate(data):
        if not isinstance(item, dict):
            errors.append(f"Item {i}: not an object, skipping.")
            continue

        mat_id = item.get("id")
        if not mat_id:
            errors.append(f"Item {i}: missing 'id' field.")
            continue

        if mat_id in _materials:
            skipped.append(mat_id)
            continue

        try:
            mat = Material(
                id=mat_id,
                name=item.get("name", mat_id),
                density=float(item["density"]),
                composition={k: float(v) for k, v in item.get("composition", {}).items()},
            )
            _materials[mat_id] = mat
            imported.append(_to_summary(mat))
        except (KeyError, ValueError, TypeError) as e:
            errors.append(f"Item {i} (id='{mat_id}'): {e}")

    return MaterialImportResponse(imported=imported, skipped=skipped, errors=errors)


def get_materials_by_ids(ids: list[str]) -> list[Material]:
    """Internal helper used by the jobs router to resolve material IDs."""
    missing = [mid for mid in ids if mid not in _materials]
    if missing:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown material IDs: {missing}. Add them to the library first.",
        )
    return [_materials[mid] for mid in ids]