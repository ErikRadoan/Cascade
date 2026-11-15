Title: CylinderZ is shape-only and can be finite; placement owns position and rotation
Date: 2026-11-15
Status: Accepted

Context
-------
A CylinderZ template carried its own `x`/`y` AND was positioned by the
SinglePlacement that placed it, so one object had two position knobs that
silently added together. Box and FuelPin never worked this way (ADR 004 removed
Box.x/y/z for the same reason).

Separately, CylinderZ was always an infinite surface. A standalone placement of
it could not be rotated, because a rotated placement is stamped in through a
fill cell that must be confined to a finite bounding volume. Changing the
placement's rotation therefore failed with a 422 ("unbounded extent"), and the
object was drawn as an endless column.

Decision
--------
1. CylinderZ has no `x`/`y`. Position and rotation come from the SinglePlacement.
   A stray `x`/`y` in an existing document is ignored (as Box.x/y/z are).
2. CylinderZ gains an optional `height`. With it the cylinder is a finite solid,
   centred on the placement's x/y and standing on z = 0 (the Box/FuelPin
   convention), built from two end caps and the cylinder surface. Its rotation
   bound is hypot(radius, height) about the base centre, so it can be rotated.
   `boundary_type` is applied to the caps as well, so a vacuum/reflective
   cylinder is sealed at both ends.
3. Without `height` it stays the bare infinite surface (Tier-2 Cell operand,
   boolean operand bounded by other surfaces). The rotation error now says to
   set `height`.
4. The UI's "new CylinderZ" template defaults to `height: 1.0`, so new objects
   are finite and rotatable.
5. A Tier-2 Cell that names a CylinderZ refers to the cylinder surface only
   (infinite); the caps exist only for placements and are kept out of the global
   surface list.

Consequences
------------
- Moving a CylinderZ happens in one place (the placement). Existing documents
  whose template carried x/y lose that offset; move it into the placement.
- A Tier-2 Cell can no longer place a CylinderZ off-axis through the template.
  Bound or offset it through a placement or a boolean composite instead.
- Sphere, ConeZ, Torus and CylinderX/Y still carry their own position. They
  have the same double-knob problem and are candidates for the same change.
