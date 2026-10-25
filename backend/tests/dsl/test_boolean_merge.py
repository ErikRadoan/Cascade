"""BooleanPlacement merging: each cell keeps its material, and nothing overlaps."""

import random

import pytest

from cascade.domain.geometry import (
    Cell,
    Complement,
    Inside,
    Intersection,
    Outside,
    Region,
    Surface,
    SurfaceType,
    Union,
)
from cascade.dsl.expander import Context, _merge_children


def ball(surface_id: str, x: float = 0.0, r: float = 1.0) -> Surface:
    return Surface(id=surface_id, type_=SurfaceType.SPHERE, params={"x0": x, "y0": 0.0, "z0": 0.0, "r": r})


def solid(surface_id: str, material: str, x: float = 0.0, r: float = 1.0):
    """A one-cell child: a sphere of ``material``."""
    cell = Cell(id=f"c_{surface_id}", region=Inside(surface_id), material_id=material)
    return ([ball(surface_id, x, r)], [cell])


def shells(prefix: str, materials: list[str], x: float = 0.0):
    """A multi-cell child like a FuelPin: concentric shells, radius 1, 2, 3, ..."""
    surfaces, cells = [], []
    for k, material in enumerate(materials):
        surfaces.append(ball(f"{prefix}{k}", x, r=k + 1.0))
        inner = [Outside(f"{prefix}{k - 1}")] if k else []
        cells.append(Cell(id=f"c_{prefix}{k}", region=Intersection([Inside(f"{prefix}{k}"), *inner]), material_id=material))
    return (surfaces, cells)


def merge(children, op, materials=None):
    ctx = Context()
    objects = _merge_children(children, op, "u", set(materials) if materials else None, ctx)
    return [o for o in objects if isinstance(o, Cell)], ctx


def pieces(cells):
    return [(c.region, c.material_id) for c in cells]


# ---------------------------------------------------------------------------
# union
# ---------------------------------------------------------------------------

def test_union_of_disjoint_children_leaves_cells_untouched():
    cells, ctx = merge([solid("a", "fuel", x=0), solid("b", "water", x=10)], "union")
    assert pieces(cells) == [(Inside("a"), "fuel"), (Inside("b"), "water")]
    assert ctx.warnings == []


def test_union_clips_the_later_child_where_an_earlier_one_covers_it():
    cells, _ = merge([solid("a", "fuel", x=0), solid("b", "water", x=1)], "union")
    assert pieces(cells) == [
        (Inside("a"), "fuel"),
        (Intersection([Inside("b"), Complement(Inside("a"))]), "water"),
    ]


def test_union_drops_a_cell_that_an_earlier_cell_fully_covers():
    cells, _ = merge([solid("a", "fuel"), ([ball("b")], [Cell(id="cb", region=Inside("a"), material_id="water")])], "union")
    assert pieces(cells) == [(Inside("a"), "fuel")]


def test_union_keeps_every_material_of_a_multi_cell_child():
    fuel_pin = shells("p", ["fuel", "gap", "clad"], x=0)
    cells, ctx = merge([fuel_pin, solid("q", "water", x=20)], "union")
    assert [m for _, m in pieces(cells)] == ["fuel", "gap", "clad", "water"]
    assert ctx.warnings == []  # nothing is collapsed any more


def test_union_clips_against_each_overlapping_cell_only():
    pin = shells("p", ["fuel", "clad"], x=0)           # fuel radius 1, clad out to radius 2
    far = shells("f", ["fuel", "clad"], x=50)          # nowhere near
    cells, _ = merge([pin, far, solid("t", "water", x=1.5, r=0.4)], "union")
    water = cells[-1]
    # The water ball spans x = 1.1..1.9: out of reach of the fuel cell (x <= 1)
    # and of the far pin, so only the clad cell is a blocker.
    assert water.region == Intersection([
        Inside("t"),
        Complement(Intersection([Inside("p1"), Outside("p0")])),
    ])


# ---------------------------------------------------------------------------
# subtraction
# ---------------------------------------------------------------------------

def test_subtraction_keeps_the_body_materials_and_ignores_cutter_material():
    pin = shells("p", ["fuel", "gap", "clad"])
    cells, _ = merge([pin, solid("k", "water", x=0.5, r=0.3)], "subtraction")
    assert [m for _, m in pieces(cells)] == ["fuel", "gap", "clad"]
    assert "water" not in [m for _, m in pieces(cells)]
    # The cutter only reaches the pin's centre, so the two outer cells could be
    # tested against it too (the bounds overlap), but all carry the same cut.
    for cell in cells:
        assert Complement(Inside("k")) in cell.region.regions


def test_subtraction_with_a_distant_cutter_changes_nothing():
    cells, _ = merge([solid("a", "fuel"), solid("k", "water", x=30)], "subtraction")
    assert pieces(cells) == [(Inside("a"), "fuel")]


def test_subtraction_of_several_cutters_clips_once_with_their_union():
    cells, _ = merge([solid("a", "fuel", r=5), solid("k1", "x", x=1), solid("k2", "y", x=-1)], "subtraction")
    assert cells[0].region == Intersection([Inside("a"), Complement(Union([Inside("k1"), Inside("k2")]))])


def test_subtraction_removes_a_cell_the_cutter_equals():
    cells, _ = merge([solid("a", "fuel"), ([ball("k")], [Cell(id="ck", region=Inside("a"), material_id="water")])], "subtraction")
    assert cells == []


# ---------------------------------------------------------------------------
# intersection
# ---------------------------------------------------------------------------

def test_intersection_uses_the_first_childs_cells_and_materials():
    cells, _ = merge([shells("p", ["fuel", "clad"]), solid("b", "water", x=1)], "intersection")
    assert [m for _, m in pieces(cells)] == ["fuel", "clad"]
    assert cells[0].region == Intersection([Inside("p0"), Inside("b")])


def test_intersection_of_disjoint_children_is_empty():
    cells, _ = merge([solid("a", "fuel", x=0), solid("b", "water", x=10)], "intersection")
    assert cells == []


# ---------------------------------------------------------------------------
# materials filter: which child cells take part
# ---------------------------------------------------------------------------

def test_filtered_out_cells_are_absent_for_a_union():
    cells, _ = merge([solid("a", "fuel"), solid("b", "water", x=10)], "union", materials=["fuel"])
    assert pieces(cells) == [(Inside("a"), "fuel")]


def test_filtering_the_kept_body_away_empties_a_subtraction():
    cells, _ = merge([solid("a", "water"), solid("b", "fuel")], "subtraction", materials=["fuel"])
    assert cells == []


def test_filtering_a_cutter_away_means_nothing_is_cut():
    cells, _ = merge([solid("a", "fuel"), solid("b", "water")], "subtraction", materials=["fuel"])
    assert pieces(cells) == [(Inside("a"), "fuel")]


def test_filtering_an_intersection_operand_away_empties_it():
    cells, _ = merge([solid("a", "fuel"), solid("b", "water")], "intersection", materials=["fuel"])
    assert cells == []


def test_filter_can_pick_one_material_out_of_a_multi_cell_child():
    cells, _ = merge([shells("p", ["fuel", "gap", "clad"])], "union", materials=["fuel", "clad"])
    assert [m for _, m in pieces(cells)] == ["fuel", "clad"]


# ---------------------------------------------------------------------------
# naming
# ---------------------------------------------------------------------------

def test_a_single_result_cell_is_named_after_the_placement():
    cells, _ = merge([solid("a", "fuel"), solid("b", "water", x=10)], "subtraction")
    assert [c.name for c in cells] == ["u"]


def test_several_result_cells_get_unique_layer_names():
    cells, _ = merge([shells("p", ["fuel", "gap", "clad"])], "union")
    assert [c.name for c in cells] == ["u_layer0", "u_layer1", "u_layer2"]
    assert len({c.id for c in cells}) == 3


# ---------------------------------------------------------------------------
# property test: the three ops against brute-force point sampling
# ---------------------------------------------------------------------------

def _inside_ball(surface: Surface, point) -> bool:
    p = surface.params
    return (point[0] - p["x0"]) ** 2 + (point[1] - p["y0"]) ** 2 + (point[2] - p["z0"]) ** 2 < p["r"] ** 2


def _contains(region: Region, surfaces: dict[str, Surface], point) -> bool:
    if isinstance(region, Inside):
        return _inside_ball(surfaces[region.surface_id], point)
    if isinstance(region, Outside):
        return not _inside_ball(surfaces[region.surface_id], point)
    if isinstance(region, Intersection):
        return all(_contains(r, surfaces, point) for r in region.regions)
    if isinstance(region, Union):
        return any(_contains(r, surfaces, point) for r in region.regions)
    if isinstance(region, Complement):
        return not _contains(region.region, surfaces, point)
    raise TypeError(region)


def _random_child(rng: random.Random, index: int):
    layers = rng.randint(1, 3)
    return shells(f"k{index}_", [f"m{index}{k}" for k in range(layers)], x=rng.uniform(-3, 3))


def _expected_material(op, children, surfaces, point):
    """Ground truth straight from the set definition, with 'earlier child wins'."""
    def owner(child):
        for cell in child[1]:
            if _contains(cell.region, surfaces, point):
                return cell.material_id
        return None

    owners = [owner(child) for child in children]
    if op == "union":
        return next((m for m in owners if m is not None), None)
    if op == "subtraction":
        return owners[0] if owners[0] is not None and all(m is None for m in owners[1:]) else None
    return owners[0] if all(m is not None for m in owners) else None  # intersection


@pytest.mark.parametrize("op", ["union", "subtraction", "intersection"])
@pytest.mark.parametrize("seed", range(8))
def test_result_matches_set_theory_and_never_overlaps(op, seed):
    rng = random.Random(seed * 31 + len(op))
    children = [_random_child(rng, i) for i in range(rng.randint(2, 4))]
    surfaces = {s.id: s for surfaces_, _ in children for s in surfaces_}

    cells, _ = merge(children, op)

    for _ in range(400):
        point = (rng.uniform(-6, 6), rng.uniform(-4, 4), rng.uniform(-4, 4))
        hits = [c for c in cells if _contains(c.region, surfaces, point)]
        assert len(hits) <= 1, f"{op}: cells overlap at {point}"
        got = hits[0].material_id if hits else None
        assert got == _expected_material(op, children, surfaces, point), f"{op} wrong at {point}"
