"""Conservative bounds: they may over-estimate, never under-estimate."""

import math

from cascade.domain.bounds import (
    UNBOUNDED,
    bounds_overlap,
    hull_bounds,
    intersect_bounds,
    region_bounds,
)
from cascade.domain.geometry import Complement, Inside, Intersection, Outside, Surface, SurfaceType, Union

INF = math.inf


def S(sid, kind, **params):
    return Surface(id=sid, type_=kind, params=params)


SURFACES = {
    "sph": S("sph", SurfaceType.SPHERE, x0=1, y0=2, z0=3, r=2),
    "px": S("px", SurfaceType.PLANE_X, x0=5),
    "pz_alias": S("pz_alias", SurfaceType.PLANE_Z, z=-4),  # short alias, as the expander writes it
    "cz": S("cz", SurfaceType.CYLINDER_Z, x0=0, y0=0, r=0.5),
    "cx": S("cx", SurfaceType.CYLINDER_X, y0=1, z0=-1, r=2),
    "tor": S("tor", SurfaceType.TORUS, x0=0, y0=0, z0=1, a=3, b=0.5, c=0.25),
    "cone": S("cone", SurfaceType.CONE_Z, x0=0, y0=0, z0=0, r2=0.25),
    "broken": S("broken", SurfaceType.SPHERE, x0=0, y0=0, z0=0),  # radius missing
}


def b(region):
    return region_bounds(region, SURFACES)


def test_sphere_and_alias_plane():
    assert b(Inside("sph")) == (-1, 3, 0, 4, 1, 5)
    assert b(Inside("px")) == (-INF, 5, -INF, INF, -INF, INF)
    assert b(Outside("px")) == (5, INF, -INF, INF, -INF, INF)
    assert b(Inside("pz_alias")) == (-INF, INF, -INF, INF, -INF, -4)


def test_cylinders_are_bounded_across_their_axis_only():
    assert b(Inside("cz")) == (-0.5, 0.5, -0.5, 0.5, -INF, INF)
    assert b(Inside("cx")) == (-INF, INF, -1, 3, -3, 1)


def test_torus_uses_ring_plus_tube_radially_and_c_axially():
    assert b(Inside("tor")) == (-3.5, 3.5, -3.5, 3.5, 0.75, 1.25)


def test_what_cannot_be_bounded_is_unbounded():
    assert b(Inside("cone")) == UNBOUNDED
    assert b(Inside("broken")) == UNBOUNDED
    assert b(Inside("not_a_surface")) == UNBOUNDED
    assert b(Outside("sph")) == UNBOUNDED
    assert b(Complement(Inside("sph"))) == UNBOUNDED


def test_intersection_narrows_and_can_prove_emptiness():
    slab = Intersection([Outside("px"), Inside("sph")])  # x >= 5 and inside the sphere (x <= 3)
    assert b(slab) is None
    capped = Intersection([Inside("sph"), Inside("px")])
    assert b(capped) == (-1, 3, 0, 4, 1, 5)
    assert b(Intersection([Inside("cz"), Inside("pz_alias")])) == (-0.5, 0.5, -0.5, 0.5, -INF, -4)


def test_union_is_the_hull_and_ignores_empty_members():
    assert b(Union([Inside("sph"), Inside("cz")])) == (-1, 3, -0.5, 4, -INF, INF)
    assert b(Union([Inside("sph"), Intersection([Outside("px"), Inside("sph")])])) == (-1, 3, 0, 4, 1, 5)


def test_helpers_handle_empty():
    box = (0, 1, 0, 1, 0, 1)
    assert intersect_bounds(box, None) is None
    assert hull_bounds(None, box) == box
    assert hull_bounds(None, None) is None


def test_overlap_requires_shared_volume():
    a = (0, 1, 0, 1, 0, 1)
    assert bounds_overlap(a, (0.5, 2, 0.5, 2, 0.5, 2))
    assert not bounds_overlap(a, (1, 2, 0, 1, 0, 1))          # touching faces
    assert not bounds_overlap(a, (2, 3, 0, 1, 0, 1))          # apart on one axis is enough
    assert bounds_overlap(a, UNBOUNDED)
    assert not bounds_overlap(a, None)
