"""Geometry HTTP routes."""

from __future__ import annotations

from fastapi import APIRouter

from ..domain.geometry import (
    CascadeGeometry, Cell, Surface, SurfaceType, Inside, Outside, Intersection,
)
from ..dsl import loader, expander
from ..services.geometry_service import GeometryService


router = APIRouter(prefix="/geometry", tags=["geometry"])
geometry_service = GeometryService()

# api/geometry.py

@router.post("/validate")
async def validate_geometry(body: GeometryTextRequest) -> ValidationResponse:
    errors = loader.validate(body.text)
    return ValidationResponse(errors=errors)

@router.post("/preview")
async def preview_geometry(body: GeometryTextRequest) -> PreviewResponse:
    try:
        schemas = loader.load(body.text)
        geometry = expander.expand(schemas)
        image_b64 = geometry_service.render_slice(geometry, axis="xy", position=0)
        return PreviewResponse(image=image_b64)
    except Exception as e:
        return PreviewResponse(error=str(e))


@router.get("/")
def list_geometries() -> list[dict[str, object]]:
    return [geometry.to_dict() for geometry in geometry_service.list()]


@router.get("/{geometry_id}")
def get_geometry(geometry_id: str) -> dict[str, object] | None:
    geometry = geometry_service.get(geometry_id)
    return geometry.to_dict() if geometry is not None else None
