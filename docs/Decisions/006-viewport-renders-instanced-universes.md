Title: The viewport renders instanced universes (rotated placements) from the exported fill cells
Date: 2026-11-15
Status: Accepted

Context
-------
A rotated placement is expanded as a LOCAL universe at the origin plus a fill
cell that stamps it into the root with a translation and rotation (ADR/rotation
work in dsl/expander._place_rotated). The CSG export dropped `universe_id`,
`fill`, `translation` and `rotation`, and the viewport had no notion of
universes, so it drew every material cell in the root frame. A rotated object
therefore appeared unrotated at the origin, together with its local "background"
cell (a moderator-filled bounding sphere), which looked like a stray sphere that
hid the object.

Decision
--------
1. The export adds `universe_id` (non-root only) and, for fill cells, `fill`,
   `translation`, `rotation`. All additive; ordinary cells keep their old shape.
2. `components/geometry/csgUniverses.ts` resolves root -> fill cell -> child
   universe into (cell, offset, quaternion) per drawable cell. A cell's local
   region is unchanged; the transform is carried by the instance.
3. The instance texture is two rows tall: row 0 = offset + prototype, row 1 =
   rotation quaternion. The shader inverse-rotates the ray into the instance's
   local frame (rigid, so ray parameters are shared), and rotates the normal back.
   The BVH uses the world AABB of the rotated local AABB.
4. Rotation convention is OpenMC's, verified against src/cell.cpp and
   src/geometry.cpp: world -> local is M (p - t), M = Rz(-rz) Ry(-ry) Rx(-rx), so
   local -> world is t + Rx(rx) Ry(ry) Rz(rz) p (intrinsic X-Y-Z). The unit test
   pins this against OpenMC's matrix.
5. `<placement>_background` cells in non-root universes are bookkeeping for the
   simulation (no gap inside the confining sphere) and are neither drawn nor
   listed in the Objects panel.

Consequences
------------
- Rotated SinglePlacements, FuelPins, Boxes and lattices render where and how the
  simulation will see them. Unrotated scenes are pixel-identical to before.
- Cells in a universe that no fill cell reaches are no longer drawn.
- A fill cell whose `fill` is a lattice id (not a universe) is skipped, as before.
- Background cells are identified by name suffix, the expander's convention; if
  that changes, `isBackgroundCell` must change with it.
