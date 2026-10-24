"""BooleanPlacement merging: what an operand that keeps no cells does."""

from cascade.domain.geometry import Cell, Complement, Inside, Intersection, Surface, SurfaceType, Union
from cascade.dsl.expander import Context, _combine_regions, _merge_children


def _child(surface_id: str, material: str):
    surface = Surface(id=surface_id, type_=SurfaceType.SPHERE, params={"x0": 0, "y0": 0, "z0": 0, "r": 1})
    cell = Cell(id=f"c_{surface_id}", region=Inside(surface_id), material_id=material)
    return ([surface], [cell])


def _cells(objects):
    return [o for o in objects if isinstance(o, Cell)]


def test_union_ignores_a_child_with_no_matching_material():
    ctx = Context()
    objs = _merge_children([_child("a", "fuel"), _child("b", "water")], "union", "u", {"fuel"}, ctx)
    (cell,) = _cells(objs)
    assert cell.region == Inside("a")


def test_subtraction_with_unmatched_kept_body_is_empty_not_the_cutter():
    ctx = Context()
    objs = _merge_children([_child("a", "water"), _child("b", "fuel")], "subtraction", "s", {"fuel"}, ctx)
    assert _cells(objs) == []


def test_subtraction_with_unmatched_cutter_cuts_nothing():
    ctx = Context()
    objs = _merge_children([_child("a", "fuel"), _child("b", "water")], "subtraction", "s", {"fuel"}, ctx)
    (cell,) = _cells(objs)
    assert cell.region == Inside("a")


def test_intersection_with_an_unmatched_child_is_empty():
    ctx = Context()
    objs = _merge_children([_child("a", "fuel"), _child("b", "water")], "intersection", "i", {"fuel"}, ctx)
    assert _cells(objs) == []


def test_no_filter_behaviour_is_unchanged():
    ctx = Context()
    (cell,) = _cells(_merge_children([_child("a", "m"), _child("b", "m")], "union", "u", None, ctx))
    assert cell.region == Union([Inside("a"), Inside("b")])
    (cell,) = _cells(_merge_children([_child("a", "m"), _child("b", "m")], "subtraction", "s", None, ctx))
    assert cell.region == Intersection([Inside("a"), Complement(Inside("b"))])


def test_combine_regions_is_flat():
    a, b, c = Inside("a"), Inside("b"), Inside("c")
    assert _combine_regions("union", [Union([a, b]), c]) == Union([a, b, c])
    assert _combine_regions("subtraction", [a, b, c]) == Intersection([a, Complement(b), Complement(c)])
    assert _combine_regions("intersection", [Intersection([a, b]), c]) == Intersection([a, b, c])
