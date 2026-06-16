"""Material HTTP routes."""
from __future__ import annotations
from fastapi import APIRouter
from ..domain.material import Material
from ..services.material_service import MaterialService
router = APIRouter(prefix="/materials", tags=["materials"])
material_service = MaterialService()
material_service.register(Material(id="mat-1", name="Steel", density=7.85, composition={"Fe": 1.0}))
@router.get("/")
def list_materials() -> list[dict[str, object]]:
    return [material.to_dict() for material in material_service.list()]
@router.get("/{material_id}")
def get_material(material_id: str) -> dict[str, object] | None:
    material = material_service.get(material_id)
    return material.to_dict() if material is not None else None

