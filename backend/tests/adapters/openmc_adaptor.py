from cascade.dsl.loader import load
from cascade.dsl.expander import expand
from cascade.adapters.openmc_adapter import OpenMCAdapter, OpenMCRunSettings
from cascade.domain.material import Material

# Build a geometry
yaml_text = """
    fuel_pin:
      type: FuelPin
      pellet_radius: 0.4096
      pellet_height: 365.76
      pellet_material: UO2
      gap_thickness: 0.0082
      gap_material: He
      clad_thickness: 0.0572
      clad_material: Zr4

    boundary:
      type: BoundingBox
      x_size: 1.26
      y_size: 1.26
      z_min: 0.0
      z_max: 365.76
      material: H2O
      boundary_type: reflective
    """
schemas = load(yaml_text)
geometry = expand(schemas)

# Minimal material library matching the material_ids in the geometry
def make_test_materials():

    return [
        Material(
            id="UO2",
            name="Uranium Dioxide",
            density=10.97,
            composition={
                "U235": 0.03072,
                "U238": 0.96928,
                "O16": 2.0,
            },
        ),
        Material(
            id="He",
            name="Helium Gap",
            density=0.0001786,
            composition={"He4": 1.0},
        ),
        Material(
            id="Zr4",
            name="Zircaloy-4",
            density=6.56,
            composition={
                "Zr90": 0.5145,
                "Zr91": 0.1122,
                "Zr92": 0.1715,
                "Zr94": 0.1738,
                "Zr96": 0.0280,
            },
        ),
        Material(
            id="H2O",
            name="Light Water",
            density=0.997,  # g/cm³ at ~25°C
            composition={
                "H1": 2.0,
                "O16": 1.0,
            },
        )
    ]

adapter = OpenMCAdapter()
files = adapter.export(geometry, make_test_materials())

for filename, content in files.items():
    print(f"\n{'='*40}")
    print(f"  {filename}")
    print('='*40)
    print(content)