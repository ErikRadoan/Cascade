from pydantic import ValidationError

from cascade.dsl.loader import load
from cascade.dsl.expander import expand
from pprint import pprint

from cascade.dsl.sweep import expand_sweep

payload = """
fuel_pin:
  type: FuelPin
  clad_thickness: sweep(1 to 15, step=1)
  pellet_radius: sweep(1 to 6, step=1)
"""



GREEN= '\033[0;32m'
RED = '\033[0;31m'
NC='\033[0m'

try:
    results = expand_sweep(payload)
    print(f"{GREEN} Expanded sweep results: {NC}")
    for param_values, geometry in results:
        print(f"pellet_radius={param_values['fuel_pin.pellet_radius']:.2f} clad_thickness={param_values['fuel_pin.clad_thickness']:.2f} → "
              f"{len(geometry.surfaces)} surfaces, {len(geometry.cells)} cells")
except ValidationError as e:
    print(f"{RED} Validation failed:")
    print(e)


"""

try:
    components = load(payload)
    print("Loaded successfully!")
    print(f"{GREEN} Loaded component: {NC}")

    pprint({
        name: component.model_dump()
        for name, component in components.items()
    })
except ValidationError as e:
    print(f"{RED} Validation failed:")
    print(e)


try:
    geometry = expand(components)
    print(f"{GREEN} Expanded geometry: {NC}")
    pprint(geometry)
except Exception as e:
    print(f"{RED} Expansion failed:")
    print(e)
"""