// Field-level UI hints for the Parameters panel.
// Maps "componentType.fieldName" -> dropdown options (or a dynamic
// resolver function for fields whose options depend on the current
// document, like `template` which must list actual template names).
//
// Fields not listed here fall back to plain text/number inputs based
// on their runtime JS type — this table only opts specific fields into
// a <select>.

import type { editor as EditorStore } from '$lib/stores/index.svelte';

export type FieldOptions = string[] | ((doc: Record<string, { type?: string }>) => string[]);

const BOUNDARY_TYPES = ['reflective', 'vacuum', 'periodic'];
const HEX_ORIENTATIONS = ['pointy_top', 'flat_top'];

// Common material IDs pre-seeded on the backend (api/materials.py).
// This list is a UI convenience only — the backend remains the source
// of truth and will reject unknown material IDs at submit time. Until
// materials get their own GET /materials list wired into this panel,
// this static list covers the common case and the field still accepts
// free text for anything not listed (see "custom..." handling below).
const KNOWN_MATERIALS = ['UO2', 'He', 'Zr4', 'H2O', 'B4C', 'SS316'];

const NON_TEMPLATE_TYPES = new Set(['SinglePlacement', 'SquareLattice', 'HexLattice']);

/** Resolver for the `template` field — lists every template defined in the doc. */
function templateOptions(doc: Record<string, { type?: string }>): string[] {
  return Object.entries(doc)
    .filter(([, v]) => v && typeof v === 'object' && v.type && !NON_TEMPLATE_TYPES.has(v.type))
    .map(([name]) => name);
}

// Key format: "<ComponentType>.<fieldName>"
export const FIELD_OPTIONS: Record<string, FieldOptions> = {
  'Box.material':              KNOWN_MATERIALS,
  'Box.boundary_type':         BOUNDARY_TYPES,
  'FuelPin.pellet_material':   KNOWN_MATERIALS,
  'FuelPin.gap_material':      KNOWN_MATERIALS,
  'FuelPin.clad_material':     KNOWN_MATERIALS,
  'SinglePlacement.template':  templateOptions,
  'SquareLattice.template':    templateOptions,
  'HexLattice.template':       templateOptions,
  'HexLattice.orientation':    HEX_ORIENTATIONS,
};

/** Resolve the dropdown options for a field, or null if it should stay a free input. */
export function resolveFieldOptions(
  componentType: string,
  fieldKey: string,
  doc: Record<string, { type?: string }>,
): string[] | null {
  const entry = FIELD_OPTIONS[`${componentType}.${fieldKey}`];
  if (!entry) return null;
  return typeof entry === 'function' ? entry(doc) : entry;
}