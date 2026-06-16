"""Adapter protocol for converting between IR and simulator-specific formats."""
from __future__ import annotations
from typing import Protocol
from ..domain.geometry import CascadeGeometry
from ..domain.result import TallyResult
class AdapterProtocol(Protocol):
    name: str
    def export_geometry(self, geometry: CascadeGeometry) -> dict[str, object]:
        ...
    def import_results(self, payload: dict[str, object]) -> list[TallyResult]:
        ...

