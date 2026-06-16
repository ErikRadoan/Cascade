"""SceneBuilder service — converts validated schemas into a 3D scene description.

The scene description is a structured JSON-serializable object that the
Svelte frontend consumes to build a Three.js scene. The backend does all
the geometric reasoning here; the frontend is a dumb renderer.

Design:
    - One builder method per schema type (_build_fuel_pin, _build_bounding_box)
    - A material color registry with sensible defaults for common nuclear materials
    - Output is plain dataclasses → trivially serializable to dict/JSON

Pipeline position:
    loader.load(yaml_text)
        -> dict[str, BaseComponentSchema]
        -> SceneBuilder.build(schemas)
        -> SceneDescription
        -> FastAPI response (JSON)
        -> Svelte Three.js renderer

This runs synchronously on every editor update — no containers, no files,
no IO of any kind. Should complete in < 1ms for any realistic geometry.

TODO: implement the frontend for this:
    // For FuelPin — one CylinderGeometry per layer
    component.layers.forEach(layer => {
        const geo = new THREE.CylinderGeometry(
            layer.r_outer,   // top radius
            layer.r_outer,   // bottom radius (same — straight cylinder)
            layer.height,
            64,              // segments — smooth circle
            1,
            layer.r_inner > 0  // open-ended if hollow (gap, clad)
        );
        // If hollow: use r_inner to punch the hole via subtraction
        // or just render concentric cylinders from outside in
        const mat = new THREE.MeshPhongMaterial({
            color: layer.color,
            transparent: layer.opacity < 1.0,
            opacity: layer.opacity,
            side: THREE.DoubleSide,
        });
        const mesh = new THREE.Mesh(geo, mat);
        mesh.position.y = layer.z_base + layer.height / 2;  // Three.js Y = OpenMC Z
        scene.add(mesh);
    });
    // For BoundingBox — EdgesGeometry so you can see inside
    const geo = new THREE.BoxGeometry(box.x_size, box.z_size, box.y_size);
    const edges = new THREE.EdgesGeometry(geo);
    const line = new THREE.LineSegments(edges,
        new THREE.LineBasicMaterial({ color: box.color })
    );
    scene.add(line);

"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..dsl.schema.bounding_box import BoundingBoxSchema
from ..dsl.schema.fuel_pin import FuelPinSchema
from ..dsl.schema.base import BaseComponentSchema


# ---------------------------------------------------------------------------
# Material color palette
# ---------------------------------------------------------------------------
# Hex colors chosen for perceptual distinctness and domain convention.
# Nuclear engineers are used to certain color associations (UO2 = orange/red,
# water = blue, zircaloy = grey, etc.) — match those expectations.
#
# Users can override these via the material library editor in the frontend.
# Unknown materials fall back to _DEFAULT_COLOR.

_MATERIAL_COLORS: dict[str, str] = {
    # Fuel materials
    "UO2":          "#E8703A",   # warm orange — standard fuel colour
    "UO2_3.5pct":  "#E8703A",
    "UO2_4pct":    "#D4612E",
    "UO2_5pct":    "#C05020",
    "ThO2":        "#C8A882",   # thorium — sandy brown
    "UC":          "#B06030",   # uranium carbide

    # Gap / gas materials
    "He":          "#D0EEF8",   # very light blue, semi-transparent in renderer
    "He4":         "#D0EEF8",
    "Air":         "#E8F4F8",

    # Cladding / structural
    "Zr4":         "#8BAFC0",   # blue-grey — zircaloy
    "Zircaloy":    "#8BAFC0",
    "Zircaloy-4":  "#8BAFC0",
    "Zircaloy-2":  "#7FA0B0",
    "SS304":       "#A0A8B0",   # stainless steel — neutral grey
    "SS316":       "#9098A8",
    "Steel":       "#909090",
    "Al":          "#C8D0D8",   # aluminium — light grey

    # Moderator / coolant
    "H2O":         "#4A90D9",   # blue — water
    "Water":       "#4A90D9",
    "D2O":         "#3A7AC0",   # heavy water — slightly darker
    "HeavyWater":  "#3A7AC0",
    "CO2":         "#B8D8E8",   # CO2 coolant — pale blue
    "Na":          "#F0C040",   # sodium — yellow (fast reactor coolant)
    "LiFBeF2":     "#90D0A0",   # FLiBe salt — green (LFTR relevant)
    "FLiBe":       "#90D0A0",

    # Absorber / control materials
    "B4C":         "#303030",   # boron carbide — near black
    "Hafnium":     "#606878",
    "Hf":          "#606878",
    "AgInCd":      "#788090",   # PWR control rod alloy
    "Gd2O3":       "#A08060",   # gadolinium burnable absorber

    # Reflector / shield
    "Graphite":    "#505050",   # dark grey
    "Be":          "#B0C8B0",   # beryllium reflector — pale green
    "Pb":          "#787870",   # lead shield
    "Concrete":    "#C0B8A8",   # shielding concrete

    # Void / vacuum
    "void":        "#101020",   # near-black (rendered as transparent)
    "Void":        "#101020",
}

_DEFAULT_COLOR = "#A0A0A0"   # medium grey for unknown materials


def material_color(material_id: str) -> str:
    """Look up the display color for a material ID.

    Case-insensitive lookup with fallback to default grey.

    Args:
        material_id: Material identifier from the schema.

    Returns:
        Hex color string like "#E8703A".
    """
    # Try exact match first
    if material_id in _MATERIAL_COLORS:
        return _MATERIAL_COLORS[material_id]

    # Try case-insensitive
    lower = material_id.lower()
    for key, color in _MATERIAL_COLORS.items():
        if key.lower() == lower:
            return color

    return _DEFAULT_COLOR


# ---------------------------------------------------------------------------
# Material opacity
# ---------------------------------------------------------------------------
# Some materials should be rendered translucent so inner geometry is visible.
# 1.0 = fully opaque, 0.0 = invisible.

_MATERIAL_OPACITY: dict[str, float] = {
    "He":    0.15,   # gap is almost invisible — just a hint
    "He4":   0.15,
    "H2O":   0.25,   # water is translucent — you can see the pin inside
    "D2O":   0.25,
    "Water": 0.25,
    "void":  0.0,
    "Void":  0.0,
    "Air":   0.05,
    "CO2":   0.10,
    "Na":    0.30,   # sodium is opaque enough to matter
    "FLiBe": 0.30,
    "LiFBeF2": 0.30,
}

_DEFAULT_OPACITY = 1.0


def material_opacity(material_id: str) -> float:
    """Look up the display opacity for a material ID."""
    if material_id in _MATERIAL_OPACITY:
        return _MATERIAL_OPACITY[material_id]
    lower = material_id.lower()
    for key, opacity in _MATERIAL_OPACITY.items():
        if key.lower() == lower:
            return opacity
    return _DEFAULT_OPACITY


# ---------------------------------------------------------------------------
# Scene description dataclasses
# ---------------------------------------------------------------------------

@dataclass
class CylinderLayer:
    """One concentric cylindrical shell within a fuel pin.

    Maps directly to a Three.js CylinderGeometry with inner and outer radius.

    Attributes:
        r_inner:     Inner radius of this layer (cm). 0 for the innermost layer.
        r_outer:     Outer radius of this layer (cm).
        height:      Axial height (cm).
        z_base:      Z coordinate of the bottom of the cylinder (cm).
        material_id: Material identifier string.
        color:       Hex color for this material.
        opacity:     Render opacity (0.0 = transparent, 1.0 = opaque).
        label:       Human-readable label for UI tooltips.
    """
    r_inner:     float
    r_outer:     float
    height:      float
    z_base:      float
    material_id: str
    color:       str
    opacity:     float
    label:       str


@dataclass
class WireframeBox:
    """A wireframe box showing the problem boundary.

    Rendered as EdgesGeometry(BoxGeometry(...)) in Three.js so the
    interior geometry remains visible.

    Attributes:
        x_size, y_size, z_size: Full dimensions (cm).
        z_base:                 Z coordinate of the bottom face (cm).
        color:                  Wireframe line color.
        boundary_type:          "reflective", "vacuum", or "periodic".
                                Used by the frontend to choose line style
                                (e.g. dashed for vacuum, solid for reflective).
        fill_material_id:       Material filling the box exterior.
        fill_color:             Color for the fill material volume.
        fill_opacity:           Opacity for the fill volume.
    """
    x_size:          float
    y_size:          float
    z_size:          float
    z_base:          float
    color:           str
    boundary_type:   str
    fill_material_id: str
    fill_color:      str
    fill_opacity:    float


@dataclass
class SceneComponent:
    """One logical component in the 3D scene.

    A fuel pin is one SceneComponent with multiple CylinderLayers.
    A bounding box is one SceneComponent with one WireframeBox.

    Attributes:
        type:      Schema type string ("FuelPin", "BoundingBox").
        name:      Component name from the YAML definition.
        position:  (x, y, z) position of the component origin (cm).
        layers:    Cylinder layers, for FuelPin components.
        box:       Wireframe box, for BoundingBox components.
    """
    type:     str
    name:     str
    position: tuple[float, float, float]        = field(default=(0.0, 0.0, 0.0))
    layers:   list[CylinderLayer]               = field(default_factory=list)
    box:      WireframeBox | None               = field(default=None)

    def to_dict(self) -> dict:
        d: dict = {
            "type":     self.type,
            "name":     self.name,
            "position": list(self.position),
        }
        if self.layers:
            d["layers"] = [
                {
                    "r_inner":     l.r_inner,
                    "r_outer":     l.r_outer,
                    "height":      l.height,
                    "z_base":      l.z_base,
                    "material_id": l.material_id,
                    "color":       l.color,
                    "opacity":     l.opacity,
                    "label":       l.label,
                }
                for l in self.layers
            ]
        if self.box:
            b = self.box
            d["box"] = {
                "x_size":          b.x_size,
                "y_size":          b.y_size,
                "z_size":          b.z_size,
                "z_base":          b.z_base,
                "color":           b.color,
                "boundary_type":   b.boundary_type,
                "fill_material_id": b.fill_material_id,
                "fill_color":      b.fill_color,
                "fill_opacity":    b.fill_opacity,
            }
        return d


@dataclass
class SceneDescription:
    """Complete 3D scene ready for Three.js rendering.

    Attributes:
        components:      List of renderable components, in draw order.
                         BoundingBox is always last so it renders over inner geometry.
        material_colors: All material_id -> hex color mappings present in this scene.
                         Frontend uses this to build the material legend.
        bounds:          (x_min, x_max, y_min, y_max, z_min, z_max) of the scene (cm).
                         Used to set the initial Three.js camera position and zoom.
    """
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
# SceneBuilder
# ---------------------------------------------------------------------------

class SceneBuilder:
    """Converts a dict of validated schemas into a SceneDescription.

    Usage:
        builder = SceneBuilder()
        scene = builder.build(schemas)
        return scene.to_dict()   # JSON-serializable

    Adding a new component type:
        1. Write _build_<type>(name, schema) -> SceneComponent
        2. Add to _BUILDERS dispatch dict at the bottom of this class
        Nothing else changes.
    """

    def build(
        self,
        schemas: dict[str, BaseComponentSchema],
    ) -> SceneDescription:
        """Build a complete scene description from validated schemas.

        Args:
            schemas: Output of loader.load() — name -> schema instance.

        Returns:
            SceneDescription ready for JSON serialisation.

        Raises:
            TypeError: If a schema type has no registered builder.
                       Add it to _BUILDERS.
        """
        components: list[SceneComponent] = []
        material_colors: dict[str, str]  = {}

        # Build BoundingBox last so it renders on top of inner geometry
        ordered = sorted(
            schemas.items(),
            key=lambda kv: 1 if isinstance(kv[1], BoundingBoxSchema) else 0,
        )

        for name, schema in ordered:
            builder_fn = self._BUILDERS.get(type(schema))
            if builder_fn is None:
                raise TypeError(
                    f"No scene builder registered for schema type "
                    f"'{type(schema).__name__}' (component '{name}'). "
                    f"Add it to SceneBuilder._BUILDERS."
                )

            component = builder_fn(self, name, schema)
            components.append(component)

            # Collect all material colors present in this component
            for layer in component.layers:
                material_colors[layer.material_id] = layer.color
            if component.box:
                material_colors[component.box.fill_material_id] = component.box.fill_color

        bounds = self._compute_bounds(components)

        return SceneDescription(
            components=components,
            material_colors=material_colors,
            bounds=bounds,
        )

    # ------------------------------------------------------------------
    # Per-schema builders
    # ------------------------------------------------------------------

    def _build_fuel_pin(
        self,
        name:   str,
        schema: FuelPinSchema,
    ) -> SceneComponent:
        """Build cylinder layers for a fuel pin.

        Each radial layer (pellet → gap → cladding) becomes one CylinderLayer.
        The innermost layer has r_inner=0 (solid cylinder).
        All other layers are hollow (r_inner = previous layer's r_outer).
        """
        layers: list[CylinderLayer] = []
        prev_r = 0.0

        layer_labels = {
            schema.pellet_material: "Fuel pellet",
            schema.gap_material:    "Helium gap",
            schema.clad_material:   "Cladding",
        }

        for r_outer, mat_id in schema.radial_layers():
            layers.append(CylinderLayer(
                r_inner=     prev_r,
                r_outer=     r_outer,
                height=      schema.pellet_height,
                z_base=      0.0,
                material_id= mat_id,
                color=       material_color(mat_id),
                opacity=     material_opacity(mat_id),
                label=       layer_labels.get(mat_id, mat_id),
            ))
            prev_r = r_outer

        return SceneComponent(
            type=     "FuelPin",
            name=     name,
            position= (0.0, 0.0, 0.0),
            layers=   layers,
        )

    def _build_bounding_box(
        self,
        name:   str,
        schema: BoundingBoxSchema,
    ) -> SceneComponent:
        """Build a wireframe box for the problem boundary.

        The box itself is rendered as edges only (wireframe) so the
        fuel pin inside remains visible. The fill material (water) is
        rendered as a semi-transparent volume filling the box.
        """
        # Wireframe color matches boundary type convention:
        #   reflective → solid white (mirror-like)
        #   vacuum     → dashed red (open boundary)
        #   periodic   → dashed yellow
        wireframe_colors = {
            "reflective": "#FFFFFF",
            "vacuum":     "#FF4444",
            "periodic":   "#FFCC00",
            "none":       "#888888",
        }

        bt_str = schema.boundary_type.value
        box = WireframeBox(
            x_size=           schema.x_size,
            y_size=           schema.y_size,
            z_size=           schema.axial_height(),
            z_base=           schema.z_min,
            color=            wireframe_colors.get(bt_str, "#FFFFFF"),
            boundary_type=    bt_str,
            fill_material_id= schema.material,
            fill_color=       material_color(schema.material),
            fill_opacity=     material_opacity(schema.material),
        )

        return SceneComponent(
            type=     "BoundingBox",
            name=     name,
            position= (0.0, 0.0, schema.z_min + schema.axial_height() / 2),
            box=      box,
        )

    # ------------------------------------------------------------------
    # Bounds computation
    # ------------------------------------------------------------------

    def _compute_bounds(
        self,
        components: list[SceneComponent],
    ) -> tuple[float, float, float, float, float, float]:
        """Compute scene bounding box from all components.

        Used by the frontend to set camera position and zoom level.

        Returns:
            (x_min, x_max, y_min, y_max, z_min, z_max) in cm.
        """
        x_min = y_min = z_min =  float("inf")
        x_max = y_max = z_max = -float("inf")

        for comp in components:
            # Cylinder layers
            for layer in comp.layers:
                r = layer.r_outer
                x, y, z = comp.position
                x_min = min(x_min, x - r);  x_max = max(x_max, x + r)
                y_min = min(y_min, y - r);  y_max = max(y_max, y + r)
                z_min = min(z_min, z + layer.z_base)
                z_max = max(z_max, z + layer.z_base + layer.height)

            # Bounding box
            if comp.box:
                b = comp.box
                x_min = min(x_min, -b.x_size / 2)
                x_max = max(x_max,  b.x_size / 2)
                y_min = min(y_min, -b.y_size / 2)
                y_max = max(y_max,  b.y_size / 2)
                z_min = min(z_min,  b.z_base)
                z_max = max(z_max,  b.z_base + b.z_size)

        # Fallback for empty scene
        if x_min == float("inf"):
            return (-1.0, 1.0, -1.0, 1.0, 0.0, 1.0)

        return (x_min, x_max, y_min, y_max, z_min, z_max)

    # ------------------------------------------------------------------
    # Dispatch table
    # ------------------------------------------------------------------

    _BUILDERS: dict[type, object] = {}


# Register builders after class definition to avoid forward-reference issues
SceneBuilder._BUILDERS = {
    FuelPinSchema:     SceneBuilder._build_fuel_pin,
    BoundingBoxSchema: SceneBuilder._build_bounding_box,
}