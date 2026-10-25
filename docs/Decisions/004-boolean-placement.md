Title: BooleanPlacement is the only boolean, and it keeps every cell's material
Date: 2026-10-25
Status: Accepted

Context
-------
Boolean shapes could be built two ways: template-level `Union` / `Subtraction` /
`Intersection` types (placed with a `SinglePlacement`), and `BooleanPlacement`,
which combines already-placed objects. The UI only ever created the second, the
templates couldn't be used in lattices or take a FuelPin as an operand, and they
duplicated the combine logic and carried a third operand spelling (`a`/`b`).

`BooleanPlacement` also collapsed its result into ONE cell with the first
child's material. Combining a FuelPin (pellet, gap, cladding) with anything
silently lost the other materials, and `materials` was the only way to dodge it.
The earlier alternative, one cell per child, left overlapping children as
overlapping cells, which OpenMC and Serpent treat as undefined.

Decision
--------
1. `BooleanPlacement` is the only way to build a boolean. The template types were
   removed. The loader answers them with a message that shows the equivalent
   `BooleanPlacement`. `Box.x/y/z` (which only meant "offset as a template-boolean
   operand") were removed too; unknown fields in old documents are ignored.

2. A child is one or more cells. The result keeps each surviving cell and its own
   material, and cells never overlap because overlaps are clipped away:

   | op             | result                                                          |
   |----------------|-----------------------------------------------------------------|
   | `union`        | all children's cells; an earlier child wins where they overlap   |
   | `subtraction`  | the first child's cells minus every other child                  |
   | `intersection` | the first child's cells, confined to where every other child is  |

   Cutters and the "other" operands of an intersection only define space; their
   materials never appear in the result.

3. `materials` selects which child cells take part. Cells of any other material
   are absent: they add no volume to a union, cut nothing as a cutter, and don't
   count towards an intersection operand. A child with no selected cells is the
   empty set. A union skips it; it empties an intersection, and a subtraction
   whose first child is empty; an empty cutter cuts nothing. An empty list means
   every cell takes part.

4. Clipping is pruned with conservative axis-aligned bounds (`domain/bounds.py`).
   A cell is only clipped against cells whose bounds could overlap it, so
   children that don't touch pass through unchanged. Bounds may over-estimate but
   never under-estimate (complements, cones and unknown surfaces are unbounded).

5. A result with several cells names them `<placement>_layer<k>` (one cell keeps
   the placement's name). Tallies and the viewer match results to cells by name,
   and the viewer groups `_layerN` cells under their placement.

6. Rotation. Rotating the whole composite (`rx/ry/rz` on the `BooleanPlacement`)
   keeps every material. A rotated *child* lives in its own universe and its
   siblings can't be drawn inside it, so that composite is still built as one
   material (the first child's) and a warning says so.

Consequences
------------
- Booleans keep their materials: cutting a hole in a FuelPin keeps the pellet,
  gap and cladding; a union of two different solids keeps both.
- Result regions stay short. Disjoint children are untouched, and a lattice child
  yields its ordinary pin cells instead of one region listing every pin. A 17x17
  lattice united with a nearby shape expands in about 20 ms.
- Cell counts grow: a lattice child is now one cell per pin layer. Clipping
  against many overlapping cells produces a warning past a threshold.
- Breaking: documents using `type: Union/Subtraction/Intersection` must be
  rewritten with `BooleanPlacement` (the error message says how). A reusable
  boolean *template* no longer exists; repeat the `BooleanPlacement` instead.
- Existing results differ: a boolean that used to be one cell of one material is
  now several cells, and an intersection with an operand that selects no cells
  is empty rather than silently ignoring that operand.
- Still limited: one rotated child per `BooleanPlacement`, and a rotated child
  forces a single material (see 6).
