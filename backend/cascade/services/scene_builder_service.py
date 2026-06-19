"""SceneBuilder v2 — understands templates vs placements."""

from __future__ import annotations

from dataclasses import dataclass, field

from ..dsl.schema.base import BaseComponentSchema
from ..dsl.schema.box import BoxSchema
from ..dsl.schema.fuel_pin import FuelPinSchema
from ..dsl.schema.lattice import HexLatticeSchema, SquareLatticeSchema
from ..dsl.schema.single_placement import SinglePlacementSchema


# ---------------------------------------------------------------------------
# Material colors and opacity (unchanged from v1)
# ---------------------------------------------------------------------------

_MATERIAL_COLORS: dict[str, str] = {
    "UO2": "#E8703A", "UO2_3.5pct": "#E8703A", "UO2_4pct": "#D4612E",
    "ThO2": "#C8A882", "UC": "#B06030",
    "He": "#D0EEF8", "He4": "#D0EEF8", "Air": "#E8F4F8",
    "Zr4": "#8BAFC0", "Zircaloy": "#8BAFC0", "Zircaloy-4": "#8BAFC0",
    "SS304": "#A0A8B0", "SS316": "#9098A8", "Steel": "#909090",
    "Al": "#C8D0D8",
    "H2O": "#4A90D9", "Water": "#4A90D9", "D2O": "#3A7AC0",
    "Na": "#F0C040", "LiFBeF2": "#90D0A0", "FLiBe": "#90D0A0",
    "B4C": "#303030", "Hafnium": "#606878", "Hf": "#606878",
    "AgInCd": "#788090", "Gd2O3": "#A08060",
    "Graphite": "#505050", "Be": "#B0C8B0", "Pb": "#787870",
    "void": "#101020", "Void": "#101020",
}
_DEFAULT_COLOR = "#A0A0A0"

_MATERIAL_OPACITY: dict[str, float] = {
    "He": 0.15, "He4": 0.15,
    "H2O": 0.25, "D2O": 0.25, "Water": 0.25,
    "void": 0.0, "Void": 0.0, "Air": 0.05,
    "Na": 0.30, "FLiBe": 0.30, "LiFBeF2": 0.30,
}
_DEFAULT_OPACITY = 1.0


def material_color(mid: str) -> str:
    if mid in _MATERIAL_COLORS:
        return _MATERIAL_COLORS[mid]
    for k, v in _MATERIAL_COLORS.items():
        if k.lower() == mid.lower():
            return v
    return _DEFAULT_COLOR


def material_opacity(mid: str) -> float:
    if mid in _MATERIAL_OPACITY:
        return _MATERIAL_OPACITY[mid]
    for k, v in _MATERIAL_OPACITY.items():
        if k.lower() == mid.lower():
            return v
    return _DEFAULT_OPACITY


# ---------------------------------------------------------------------------
# Output dataclasses (unchanged from v1)
# ---------------------------------------------------------------------------

@dataclass
class CylinderLayer:
    r_inner: float; r_outer: float; height: float; z_base: float
    material_id: str; color: str; opacity: float; label: str


@dataclass
class WireframeBox:
    x_size: float; y_size: float; z_size: float; z_base: float
    color: str; boundary_type: str
    fill_material_id: str; fill_color: str; fill_opacity: float


@dataclass
class SceneComponent:
    type:     str
    name:     str
    position: tuple[float, float, float] = field(default=(0.0, 0.0, 0.0))
    layers:   list[CylinderLayer]        = field(default_factory=list)
    box:      WireframeBox | None        = field(default=None)

    def to_dict(self) -> dict:
        d: dict = {"type": self.type, "name": self.name, "position": list(self.position)}
        if self.layers:
            d["layers"] = [
                {"r_inner": l.r_inner, "r_outer": l.r_outer,
                 "height": l.height, "z_base": l.z_base,
                 "material_id": l.material_id, "color": l.color,
                 "opacity": l.opacity, "label": l.label}
                for l in self.layers
            ]
        if self.box:
            b = self.box
            d["box"] = {
                "x_size": b.x_size, "y_size": b.y_size, "z_size": b.z_size,
                "z_base": b.z_base, "color": b.color,
                "boundary_type": b.boundary_type,
                "fill_material_id": b.fill_material_id,
                "fill_color": b.fill_color, "fill_opacity": b.fill_opacity,
            }
        return d


@dataclass
class SceneDescription:
    components:      list[SceneComponent]
    material_colors: dict[str, str]
    bounds:          tuple[float, float, float, float, float, float]

    def to_dict(self) -> dict:
        return {
            "components":      [c.to_dict() for c in self.components],
            "material_colors": self.material_colors,
            "bounds": {
                "x_min": self.bounds[0], "x_max": self.bounds[1],
                "y_min": self.bounds[2], "y_max": self.bounds[3],
                "z_min": self.bounds[4], "z_max": self.bounds[5],
            },
        }


# ---------------------------------------------------------------------------
# SceneBuilder v2
# ---------------------------------------------------------------------------

class SceneBuilder:
    """Builds a 3D scene description from validated schemas.

    Understands the template/placement distinction:
    - Templates (FuelPin, Box) define appearance — layers, colors, sizes
    - Placements (SinglePlacement, lattices) define where instances appear

    Each placed instance becomes one SceneComponent at its position.
    A SquareLattice with nx=17, ny=17 produces 289 SceneComponents.
    """

    def build(self, schemas: dict[str, BaseComponentSchema]) -> SceneDescription:
        # Separate templates from placements
        templates:  dict[str, BaseComponentSchema] = {}
        placements: dict[str, BaseComponentSchema] = {}

        _PLACEMENT_TYPES = (SinglePlacementSchema, SquareLatticeSchema, HexLatticeSchema)
        for name, schema in schemas.items():
            if isinstance(schema, _PLACEMENT_TYPES):
                placements[name] = schema
            else:
                templates[name] = schema

        components:      list[SceneComponent] = []
        material_colors: dict[str, str]       = {}

        def _register_colors(comp: SceneComponent) -> None:
            for l in comp.layers:
                material_colors[l.material_id] = l.color
            if comp.box:
                material_colors[comp.box.fill_material_id] = comp.box.fill_color

        # Process placements — each produces one or more SceneComponents
        for name, schema in placements.items():
            if isinstance(schema, SinglePlacementSchema):
                tpl = templates.get(schema.template)
                if tpl is None:
                    continue
                comp = self._build_placed(name, tpl, schema.position())
                if comp:
                    components.append(comp)
                    _register_colors(comp)

            elif isinstance(schema, SquareLatticeSchema):
                tpl = templates.get(schema.template)
                if tpl is None:
                    continue
                for i, pos in enumerate(schema.pin_positions()):
                    comp = self._build_placed(f"{name}_{i}", tpl, pos)
                    if comp:
                        components.append(comp)
                        _register_colors(comp)

            elif isinstance(schema, HexLatticeSchema):
                tpl = templates.get(schema.template)
                if tpl is None:
                    continue
                for i, pos in enumerate(schema.pin_positions()):
                    comp = self._build_placed(f"{name}_{i}", tpl, pos)
                    if comp:
                        components.append(comp)
                        _register_colors(comp)

        bounds = self._compute_bounds(components)
        return SceneDescription(
            components=components,
            material_colors=material_colors,
            bounds=bounds,
        )

    def _build_placed(
        self,
        name: str,
        template: BaseComponentSchema,
        position: tuple[float, float, float],
    ) -> SceneComponent | None:
        """Build one SceneComponent for a template at a given position."""
        if isinstance(template, FuelPinSchema):
            return self._build_fuel_pin(name, template, position)
        if isinstance(template, BoxSchema):
            return self._build_box(name, template, position)
        return None

    def _build_fuel_pin(
        self, name: str, schema: FuelPinSchema,
        position: tuple[float, float, float],
    ) -> SceneComponent:
        layers: list[CylinderLayer] = []
        prev_r = 0.0
        labels = {
            schema.pellet_material: "Fuel pellet",
            schema.gap_material:    "Helium gap",
            schema.clad_material:   "Cladding",
        }
        for r_outer, mat_id in schema.radial_layers():
            layers.append(CylinderLayer(
                r_inner=prev_r, r_outer=r_outer,
                height=schema.pellet_height, z_base=position[2],
                material_id=mat_id, color=material_color(mat_id),
                opacity=material_opacity(mat_id),
                label=labels.get(mat_id, mat_id),
            ))
            prev_r = r_outer
        return SceneComponent(type="FuelPin", name=name, position=position, layers=layers)

    def _build_box(
        self, name: str, schema: BoxSchema,
        position: tuple[float, float, float],
    ) -> SceneComponent:
        bt_colors = {
            "reflective": "#FFFFFF", "vacuum": "#FF4444",
            "periodic": "#FFCC00",  "none": "#888888",
        }
        bt_str = schema.boundary_type.value
        box = WireframeBox(
            x_size=schema.x_size, y_size=schema.y_size, z_size=schema.z_size,
            z_base=position[2], color=bt_colors.get(bt_str, "#FFFFFF"),
            boundary_type=bt_str, fill_material_id=schema.material,
            fill_color=material_color(schema.material),
            fill_opacity=material_opacity(schema.material),
        )
        return SceneComponent(type="Box", name=name, position=position, box=box)

    def _compute_bounds(
        self, components: list[SceneComponent],
    ) -> tuple[float, float, float, float, float, float]:
        x_min = y_min = z_min =  float("inf")
        x_max = y_max = z_max = -float("inf")
        for comp in components:
            for l in comp.layers:
                r = l.r_outer
                x, y, z = comp.position
                x_min = min(x_min, x - r); x_max = max(x_max, x + r)
                y_min = min(y_min, y - r); y_max = max(y_max, y + r)
                z_min = min(z_min, z + l.z_base)
                z_max = max(z_max, z + l.z_base + l.height)
            if comp.box:
                b = comp.box
                x, y, z = comp.position
                x_min = min(x_min, x - b.x_size/2)
                x_max = max(x_max, x + b.x_size/2)
                y_min = min(y_min, y - b.y_size/2)
                y_max = max(y_max, y + b.y_size/2)
                z_min = min(z_min, b.z_base)
                z_max = max(z_max, b.z_base + b.z_size)
        if x_min == float("inf"):
            return (-1.0, 1.0, -1.0, 1.0, 0.0, 1.0)
        return (x_min, x_max, y_min, y_max, z_min, z_max)