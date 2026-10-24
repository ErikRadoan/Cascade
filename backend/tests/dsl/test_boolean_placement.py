"""BooleanPlacement is the only boolean path; the old template types are gone."""

import pytest

from cascade.dsl.expander import expand
from cascade.dsl.loader import LoadError, SCHEMA_MAP, load

WORLD = """
world:
  type: Box
  x_size: 20
  y_size: 20
  z_min: -10
  z_max: 10
  material: H2O
  boundary_type: vacuum
"""


@pytest.mark.parametrize("legacy", ["Union", "Subtraction", "Intersection"])
def test_removed_template_types_give_a_migration_message(legacy):
    text = f"shape:\n  type: {legacy}\n  items: [a, b]\n  material: H2O\n"
    with pytest.raises(LoadError) as err:
        load(text)
    assert legacy in err.value.message
    assert "BooleanPlacement" in err.value.message
    assert err.value.component_name == "shape"


def test_template_types_are_not_registered():
    assert not {"Union", "Subtraction", "Intersection"} & set(SCHEMA_MAP)
    assert "BooleanPlacement" in SCHEMA_MAP


def test_stray_box_offsets_in_old_documents_are_ignored():
    # Box x/y/z only meant something as a template-boolean operand offset.
    schemas = load(WORLD + "  x: 3.0\n  y: 1.0\n")
    assert not hasattr(schemas["world"], "x")


SPHERES = """
ball:
  type: Sphere
  radius: 2.0
  material: UO2
cutter:
  type: Sphere
  radius: 1.0
  material: He
a:
  type: SinglePlacement
  template: ball
  x: -1.0
b:
  type: SinglePlacement
  template: ball
  x: 1.5
c:
  type: SinglePlacement
  template: cutter
u:
  type: BooleanPlacement
  op: union
  children: [a, b]
cut:
  type: BooleanPlacement
  op: subtraction
  children: [u, c]
"""


def test_nested_boolean_placement_still_expands():
    geometry = expand(load(WORLD + SPHERES))
    cut = [c for c in geometry.cells if c.name == "cut"]
    assert cut, [c.name for c in geometry.cells]
