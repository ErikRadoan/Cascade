from __future__ import annotations
from pydantic import BaseModel, Field, model_validator

# ---------------------------------------------------------------------------
# PWR typical values (IAEA-TECDOC-1234 / Glasstone & Sesonske reference)
# All lengths in centimeters, all temperatures in Kelvin.
# ---------------------------------------------------------------------------
_PWR_PELLET_RADIUS = 0.4096  # cm  — typical UO2 pellet OD/2
_PWR_PELLET_HEIGHT = 365.76  # cm  — active fuel length (~12 ft)
_PWR_GAP_THICKNESS = 0.0082  # cm  — He-filled pellet-clad gap
_PWR_CLAD_THICKNESS = 0.0572  # cm  — Zr-4 cladding wall
_PWR_PELLET_MATERIAL = "UO2"
_PWR_GAP_MATERIAL = "He"
_PWR_CLAD_MATERIAL = "Zr4"


class FuelPinSchema(BaseModel):
    """Parametric description of a single PWR-style fuel pin.

    All dimensional fields are in centimeters.
    Material fields are material IDs that must exist in the project's
    material library (validated at geometry expansion time, not here).

    Example — minimal, uses all PWR defaults:
        fuel_pin:
          type: FuelPin

    Example — custom pellet radius sweep:
        fuel_pin:
          type: FuelPin
          pellet_radius: sweep(0.38 to 0.43, step=0.01)

    Example — fully explicit:
        fuel_pin:
          type: FuelPin
          pellet_radius: 0.4096
          pellet_height: 365.76
          pellet_material: UO2_3.5pct
          gap_thickness: 0.0082
          gap_material: He
          clad_thickness: 0.0572
          clad_material: Zr4
    """

    # --- Fuel pellet ---
    pellet_radius: float = Field(
        default=_PWR_PELLET_RADIUS,
        gt=0,
        description="Outer radius of the fuel pellet (cm).",
    )
    pellet_height: float = Field(
        default=_PWR_PELLET_HEIGHT,
        gt=0,
        description="Active height of the fuel column (cm).",
    )
    pellet_material: str = Field(
        default=_PWR_PELLET_MATERIAL,
        description="Material ID for the fuel pellet (e.g. 'UO2', 'UO2_3.5pct').",
    )

    # --- Pellet-clad gap ---
    gap_thickness: float = Field(
        default=_PWR_GAP_THICKNESS,
        ge=0,  # zero is valid — no gap model
        description=(
            "Radial thickness of the pellet-clad gap (cm). "
            "Set to 0.0 to omit the gap region entirely."
        ),
    )
    gap_material: str = Field(
        default=_PWR_GAP_MATERIAL,
        description="Material ID for the gap fill gas (e.g. 'He').",
    )

    # --- Cladding ---
    clad_thickness: float = Field(
        default=_PWR_CLAD_THICKNESS,
        gt=0,
        description="Radial wall thickness of the cladding tube (cm).",
    )
    clad_material: str = Field(
        default=_PWR_CLAD_MATERIAL,
        description="Material ID for cladding (e.g. 'Zr4', 'SS316').",
    )

    # --- Derived geometry (read-only, computed by validator) ---
    # These are exposed so the expander can read them directly
    # without repeating the arithmetic.
    r_pellet: float = Field(default=0.0, exclude=True)  # = pellet_radius
    r_gap: float = Field(default=0.0, exclude=True)  # = pellet_radius + gap_thickness
    r_clad: float = Field(default=0.0, exclude=True)  # = r_gap + clad_thickness

    model_config = {"frozen": True}  # schemas are immutable after validation

    @model_validator(mode="after")
    def compute_derived_radii(self) -> FuelPinSchema:
        """Compute derived radii and validate physical consistency."""
        # Use object.__setattr__ because the model is frozen
        object.__setattr__(self, "r_pellet", self.pellet_radius)
        object.__setattr__(self, "r_gap", self.pellet_radius + self.gap_thickness)
        object.__setattr__(self, "r_clad", self.pellet_radius + self.gap_thickness + self.clad_thickness)
        return self

    # ------------------------------------------------------------------
    # Physical sanity checks
    # ------------------------------------------------------------------

    @model_validator(mode="after")
    def pellet_height_must_exceed_diameter(self) -> FuelPinSchema:
        """Catch obvious input errors — a pellet taller than it is wide."""
        if self.pellet_height < 2 * self.pellet_radius:
            raise ValueError(
                f"pellet_height ({self.pellet_height} cm) is less than the pellet diameter "
                f"({2 * self.pellet_radius} cm). This is almost certainly a unit error "
                f"(did you mean mm instead of cm?)."
            )
        return self

    @model_validator(mode="after")
    def gap_material_required_when_gap_present(self) -> FuelPinSchema:
        """If a non-zero gap is defined, a gap material must be specified."""
        if self.gap_thickness > 0 and not self.gap_material.strip():
            raise ValueError(
                "gap_material must be set when gap_thickness > 0. "
                "Typical value: 'He'."
            )
        return self

    # ------------------------------------------------------------------
    # Convenience
    # ------------------------------------------------------------------

    def has_gap(self) -> bool:
        """Return True if this pin models an explicit pellet-clad gap."""
        return self.gap_thickness > 0

    def total_pin_radius(self) -> float:
        """Outer radius of the complete pin including cladding (cm)."""
        return self.r_clad

    def radial_layers(self) -> list[tuple[float, str]]:
        """Return ordered (outer_radius, material_id) pairs for all layers.

        Useful for the expander to iterate over layers without reimplementing
        the radius arithmetic.

        Returns:
            List of (outer_radius, material_id) from innermost to outermost.
            If gap_thickness == 0, the gap layer is omitted.
        """
        layers = [(self.r_pellet, self.pellet_material)]
        if self.has_gap():
            layers.append((self.r_gap, self.gap_material))
        layers.append((self.r_clad, self.clad_material))
        return layers
