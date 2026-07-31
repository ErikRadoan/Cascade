// csgCellGrouping.ts — Phase D of geometry-restructuring-plan.md.
//
// Derives the "owner" name for a raw CSG cell — i.e. which top-level YAML
// block (a directly-authored Tier-2 Cell, or a composite template's
// placement) produced it.
//
// A user-authored Cell's `cell.name` already IS its YAML key
// (dsl/schema/cell.py — expander.py names it directly after the YAML
// key), so this is an identity mapping for those. A composite-template-
// generated cell has a generated name instead:
//   - FuelPin layers:        "<placement>_layer<i>"      (templates.py)
//   - Lattice pin instances: "<lattice>_<pin_index>_layer<i>"
//   - Box's fill cell:       "<placement>"                (no suffix at all)
// Stripping the generated "_layerN" suffix, then a trailing lattice
// "_N" index, recovers the actual placement name — the thing that's
// really editable in ParametersPanel and toggleable as one unit in
// ObjectPanel, matching the pre-Phase-D SceneComponent-based grouping
// exactly (which did the same "_N" strip on `comp.name`, just with no
// FuelPin-layer suffix to worry about since SceneBuilder built one
// SceneComponent per placement instance, not one per material layer).
//
// Known limitation (shared with the pre-Phase-D code it replaces): a
// directly-authored Cell whose OWN name happens to end in "_layerN" or
// "_N" will be mis-grouped. Rare in practice, not worth guarding against
// here — same trade-off the old baseName stripping already made.
//
// Shared by ObjectPanel.svelte (grouping + the eye-icon visibility key)
// and CsgViewportPanel.svelte (visibility filtering) so the two can never
// drift apart on what counts as "the same object."

export function baseGroupName(cellName: string): string {
  return cellName.replace(/_layer\d+$/, '').replace(/_\d+$/, '');
}
