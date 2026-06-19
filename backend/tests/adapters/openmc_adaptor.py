from cascade.dsl.sweep import expand_sweep
from cascade.adapters.openmc_adapter import OpenMCAdapter
from cascade.domain.material import Material

yaml_text = """
my_fuel_pin:
  type: FuelPin
  pellet_radius: 0.4096
  pellet_height: 365.76
  pellet_material: UO2
  gap_thickness: 0.0082
  gap_material: He
  clad_thickness: 0.0572
  clad_material: Zr4
my_box:
  type: Box
  x_size: sweep(1.0 to 1.5, step=0.1)
  y_size: sweep(1.0 to 1.5, step=0.1)
  z_size: 365.76
  material: H2O
  boundary_type: reflective
core:
  type: SquareLattice
  template: my_fuel_pin
  nx: 3
  ny: 3
  pitch_x: 1.26
  pitch_y: 1.26
  origin_x: -1.26
  origin_y: -1.26
  origin_z: 0.0
boundary:
  type: SinglePlacement
  template: my_box
  x: 0.0
  y: 0.0
  z: 0.0
"""

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
            density=0.997,
            composition={
                "H1": 2.0,
                "O16": 1.0,
            },
        ),
    ]


adapter = OpenMCAdapter()
results = expand_sweep(yaml_text)

print(f"Sweep produced {len(results)} geometries.\n")

for i, (param_values, geometry) in enumerate(results):
    print(f"{'='*50}")
    print(f"  Geometry {i + 1}/{len(results)}")
    print(f"  Params: {param_values}")
    print(f"{'='*50}")

    files = adapter.export(geometry, make_test_materials())
    for filename, content in files.items():
        print(f"\n--- {filename} ---")
        print(content)