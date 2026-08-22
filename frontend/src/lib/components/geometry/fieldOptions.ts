// Field-level UI hints for the Parameters panel.
// Maps "componentType.fieldName" -> dropdown options (or a dynamic
// resolver function for fields whose options depend on the current
// document, like `template` which must list actual template names).
//
// Fields not listed here fall back to plain text/number inputs based
// on their runtime JS type — this table only opts specific fields into
// a <select>.

export type FieldOptions = string[] | ((doc: Record<string, { type?: string }>) => string[]);

const BOUNDARY_TYPES = ['reflective', 'vacuum', 'periodic', 'none'];
const HEX_ORIENTATIONS = ['pointy_top', 'flat_top'];
const BOOLEAN_OPS = ['union', 'subtraction', 'intersection'];

const KNOWN_MATERIALS = ['UO2', 'He', 'Zr4', 'H2O', 'B4C', 'SS316'];

const NON_TEMPLATE_TYPES = new Set([
  'SinglePlacement',
  'SquareLattice',
  'HexLattice',
  'BooleanPlacement',
]);

const MATERIAL_KEYS = [
  'material',
  'pellet_material',
  'gap_material',
  'clad_material',
];

function templateOptions(doc: Record<string, { type?: string }>): string[] {
  return Object.entries(doc)
    .filter(([, v]) => v && typeof v === 'object' && v.type && !NON_TEMPLATE_TYPES.has(v.type))
    .map(([name]) => name);
}

function placementOptions(doc: Record<string, { type?: string }>): string[] {
  return Object.entries(doc)
    .filter(([, v]) => v && typeof v === 'object' && v.type && (
      v.type === 'SinglePlacement' ||
      v.type === 'SquareLattice' ||
      v.type === 'HexLattice' ||
      v.type === 'BooleanPlacement'
    ))
    .map(([name]) => name);
}

/**
 * Collect material IDs reachable from a placement name by walking its
 * template (or nested BooleanPlacement children). Used to populate the
 * BooleanPlacement.materials multi-select.
 */
export function materialsFromPlacement(
  name: string,
  doc: Record<string, Record<string, unknown>>,
  seen: Set<string> = new Set(),
): string[] {
  if (seen.has(name)) return [];
  seen.add(name);
  const block = doc[name];
  if (!block || typeof block !== 'object') return [];

  const type = block.type as string | undefined;
  const out = new Set<string>();

  if (type === 'BooleanPlacement') {
    const children = (block.children as string[] | undefined) ?? [];
    for (const c of children) {
      for (const m of materialsFromPlacement(c, doc, seen)) out.add(m);
    }
    return [...out];
  }

  if (type === 'SinglePlacement' || type === 'SquareLattice' || type === 'HexLattice') {
    const tplName = block.template as string | undefined;
    if (tplName) {
      for (const m of materialsFromPlacement(tplName, doc, seen)) out.add(m);
    }
    return [...out];
  }

  // Template / primitive: gather any *material* fields
  for (const key of MATERIAL_KEYS) {
    const v = block[key];
    if (typeof v === 'string' && v.length > 0) out.add(v);
  }
  return [...out];
}

export const FIELD_OPTIONS: Record<string, FieldOptions> = {
  'Box.material':              KNOWN_MATERIALS,
  'Box.boundary_type':         BOUNDARY_TYPES,
  'FuelPin.pellet_material':   KNOWN_MATERIALS,
  'FuelPin.gap_material':      KNOWN_MATERIALS,
  'FuelPin.clad_material':     KNOWN_MATERIALS,
  'Sphere.material':           KNOWN_MATERIALS,
  'Sphere.boundary_type':      BOUNDARY_TYPES,
  'SinglePlacement.template':  templateOptions,
  'SquareLattice.template':    templateOptions,
  'HexLattice.template':       templateOptions,
  'HexLattice.orientation':    HEX_ORIENTATIONS,
  'Union.a':                   templateOptions,
  'Union.b':                   templateOptions,
  'Union.material':            KNOWN_MATERIALS,
  'Subtraction.a':             templateOptions,
  'Subtraction.b':             templateOptions,
  'Subtraction.material':      KNOWN_MATERIALS,
  'Intersection.a':            templateOptions,
  'Intersection.b':            templateOptions,
  'Intersection.material':     KNOWN_MATERIALS,
  'BooleanPlacement.op':       BOOLEAN_OPS,
  'BooleanPlacement.children': placementOptions,
  // materials options resolved dynamically in ParametersPanel from children
  'BooleanPlacement.materials': KNOWN_MATERIALS,
};

export function resolveFieldOptions(
  componentType: string,
  fieldKey: string,
  doc: Record<string, { type?: string }>,
): string[] | null {
  const entry = FIELD_OPTIONS[`${componentType}.${fieldKey}`];
  if (!entry) return null;
  return typeof entry === 'function' ? entry(doc) : entry;
}
