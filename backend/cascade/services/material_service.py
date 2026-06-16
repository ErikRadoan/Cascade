"""Material service."""
from __future__ import annotations
from ..domain.material import Material
class MaterialService:
    def __init__(self) -> None:
        self._materials: dict[str, Material] = {}
    def register(self, material: Material) -> Material:
        self._materials[material.id] = material
        return material
    def list(self) -> list[Material]:
        return list(self._materials.values())
    def get(self, material_id: str) -> Material | None:
        return self._materials.get(material_id)

