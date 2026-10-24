"""Region round-trips: repr string, structured JSON, and the DB cell dict."""

import pytest

from cascade.domain.geometry import (
    Cell,
    Complement,
    Inside,
    Intersection,
    Outside,
    Union,
    intersection_of,
    region_from_json,
    region_from_str,
    region_from_yaml_dict,
    region_to_json,
    union_of,
)
from cascade.repositories.job_repository import _cell_from_dict

I, O = Inside, Outside

REGIONS = {
    "single": I("s1"),
    "flat union": Union([I("s1"), I("s2"), I("s3")]),
    "union of intersections": Union([Intersection([I("s1"), O("s2")]), Intersection([I("s3"), O("s4")])]),
    "complement in intersection": Intersection([I("s1"), Complement(I("s2"))]),
    "union of unions": Union([Union([I("s1"), I("s2")]), I("s3")]),
    "union inside intersection": Intersection([Union([I("s1"), I("s2")]), O("s3")]),
    "complement of union": Complement(Union([I("s1"), I("s2")])),
    "subtraction with union cutters": Intersection([I("s1"), Complement(Union([I("s2"), I("s3")]))]),
    "double complement": Complement(Complement(I("s1"))),
    "deep nesting": Union([Intersection([Union([I("a"), O("b")]), Complement(Union([I("c"), I("d")]))]), I("e")]),
}


@pytest.mark.parametrize("region", REGIONS.values(), ids=REGIONS.keys())
def test_repr_string_round_trips_structurally(region):
    assert region_from_str(repr(region)) == region


@pytest.mark.parametrize("region", REGIONS.values(), ids=REGIONS.keys())
def test_json_round_trips_structurally(region):
    assert region_from_json(region_to_json(region)) == region


def test_single_item_group_collapses():
    # Union([x]) prints as "(-s1)"; it parses back to the equivalent bare region.
    assert region_from_str(repr(Union([I("s1")]))) == I("s1")


def test_empty_string_is_all_of_space():
    assert region_from_str("") == Intersection([])


@pytest.mark.parametrize("bad", ["(-s1", "-s1)", "(-s1 : )", "~", "s1", "(-s1 :: -s2)"])
def test_malformed_strings_raise(bad):
    with pytest.raises(ValueError):
        region_from_str(bad)


def test_empty_union_is_rejected_when_parsed():
    with pytest.raises(ValueError):
        region_from_json({"op": "or", "items": []})
    with pytest.raises(ValueError):
        region_from_yaml_dict({"op": "or", "items": []}, {})


@pytest.mark.parametrize("region", REGIONS.values(), ids=REGIONS.keys())
def test_cell_dict_round_trip_prefers_json(region):
    cell = Cell(id="c1", region=region, material_id="UO2", name="x")
    assert _cell_from_dict(cell.to_dict()).region == region


def test_cell_dict_without_region_json_falls_back_to_string():
    # Rows saved before region_json existed only have the repr string.
    region = Intersection([I("s1"), Complement(Union([I("s2"), I("s3")]))])
    legacy = {"id": "c1", "region": repr(region), "material_id": "H2O", "name": None}
    assert _cell_from_dict(legacy).region == region


def test_cell_dict_keeps_fill_rotation_and_universe():
    cell = Cell(
        id="c1", region=I("s1"), name="rot", universe_id="u2",
        fill="u7", rotation=(10.0, 20.0, 30.0), translation=(1.0, 2.0, 3.0),
    )
    back = _cell_from_dict(cell.to_dict())
    assert (back.universe_id, back.fill) == ("u2", "u7")
    assert back.rotation == (10.0, 20.0, 30.0)
    assert back.translation == (1.0, 2.0, 3.0)


def test_material_cell_has_no_fill_fields():
    back = _cell_from_dict(Cell(id="c1", region=I("s1"), material_id="H2O").to_dict())
    assert back.fill is None and back.rotation is None and back.translation is None


def test_union_of_flattens_and_collapses():
    assert union_of([Union([I("a"), I("b")]), I("c")]) == Union([I("a"), I("b"), I("c")])
    assert union_of([I("a")]) == I("a")
    with pytest.raises(ValueError):
        union_of([])


def test_intersection_of_flattens():
    assert intersection_of([Intersection([I("a"), O("b")]), I("c")]) == Intersection([I("a"), O("b"), I("c")])
