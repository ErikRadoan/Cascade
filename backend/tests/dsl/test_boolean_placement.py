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
ball2:
  type: Sphere
  radius: 2.0
  material: Zr4
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
  template: ball2
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


def _cells_of(geometry, prefix):
    return [c for c in geometry.cells if c.name == prefix or (c.name or "").startswith(f"{prefix}_layer")]


def test_union_keeps_both_materials_and_the_cutter_adds_none():
    geometry = expand(load(WORLD + SPHERES))
    cut = _cells_of(geometry, "cut")
    assert [c.name for c in cut] == ["cut_layer0", "cut_layer1"]
    assert {c.material_id for c in cut} == {"UO2", "Zr4"}   # He (the cutter) is gone
    assert geometry.warnings == []                          # nothing is collapsed


def test_a_nested_boolean_keeps_its_materials_and_is_renamed_by_its_parent():
    nested = SPHERES + """
far:
  type: SinglePlacement
  template: ball2
  x: 40.0
outer:
  type: BooleanPlacement
  op: union
  children: [cut, far]
"""
    geometry = expand(load(WORLD + nested))
    outer = _cells_of(geometry, "outer")
    assert [c.name for c in outer] == ["outer_layer0", "outer_layer1", "outer_layer2"]
    assert [c.material_id for c in outer] == ["UO2", "Zr4", "Zr4"]
    assert _cells_of(geometry, "cut") == []


def test_materials_picks_which_cells_take_part():
    only_fuel = SPHERES.replace("  children: [a, b]\n", "  children: [a, b]\n  materials: [UO2]\n", 1)
    geometry = expand(load(WORLD + only_fuel))
    assert {c.material_id for c in _cells_of(geometry, "cut")} == {"UO2"}


def test_a_rotated_child_still_collapses_to_one_material_and_says_so():
    rotated = WORLD + SPHERES.split("u:\n")[0] + """
r:
  type: SinglePlacement
  template: ball2
  x: 1.5
  ry: 30.0
w:
  type: SinglePlacement
  template: world
mix:
  type: BooleanPlacement
  op: union
  children: [a, r]
"""
    geometry = expand(load(rotated))
    assert any("'mix'" in w and "rotated child" in w and "single material" in w for w in geometry.warnings), geometry.warnings
