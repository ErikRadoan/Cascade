"""Geometry service."""
from __future__ import annotations
from ..domain.geometry import CascadeGeometry
from ..dsl import loader, expander, sweep


class GeometryService:
    def validate(self, text: str) -> list[dict]:
        return loader.validate(text)

    def preview(self, text: str) -> CascadeGeometry:
        schemas = loader.load(text)
        return expander.expand(schemas)

    def compile_sweep(self, text: str) -> list[tuple[dict, CascadeGeometry]]:
        return sweep.expand_sweep(text)
