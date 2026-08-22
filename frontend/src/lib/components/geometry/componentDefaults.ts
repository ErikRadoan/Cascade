// Default field values for each component type — used when creating a new
// template or placement from the UI. Mirrors the backend Pydantic defaults
// so a freshly-created component is immediately valid (passes /validate)
// without the user having to fill in every field first.
//
// Keep in sync with backend/cascade/dsl/schema/*.py defaults.

export const TEMPLATE_DEFAULTS: Record<string, Record<string, unknown>> = {
  FuelPin: {
    type: 'FuelPin',
    pellet_radius: 0.4096,
    pellet_height: 365.76,
    pellet_material: 'UO2',
    gap_thickness: 0.0082,
    gap_material: 'He',
    clad_thickness: 0.0572,
    clad_material: 'Zr4',
  },
  Box: {
    type: 'Box',
    x_size: 1.26,
    y_size: 1.26,
    z_size: 365.76,
    material: 'H2O',
    boundary_type: 'reflective',
  },
  Sphere: {
    type: 'Sphere',
    radius: 1.0,
    x: 0.0,
    y: 0.0,
    z: 0.0,
    boundary_type: 'none',
  },
  // Boolean composites (templates) reference two other templates by name (a, b).
  // Created blank via TemplatePanel; user sets a/b in Parameters panel.
  // Placeholder values are invalid as real operands — validation will flag
  // until the user picks real templates (min_length=1 on the backend).
  Union: {
    type: 'Union',
    a: 'operand_a',
    b: 'operand_b',
    material: 'H2O',
  },
  Subtraction: {
    type: 'Subtraction',
    a: 'operand_a',
    b: 'operand_b',
    material: 'H2O',
  },
  Intersection: {
    type: 'Intersection',
    a: 'operand_a',
    b: 'operand_b',
    material: 'H2O',
  },
};

export const PLACEMENT_DEFAULTS: Record<string, (templateName: string) => Record<string, unknown>> = {
  SinglePlacement: (template) => ({
    type: 'SinglePlacement',
    template,
    x: 0.0,
    y: 0.0,
    z: 0.0,
  }),
  SquareLattice: (template) => ({
    type: 'SquareLattice',
    template,
    nx: 3,
    ny: 3,
    pitch_x: 1.26,
    pitch_y: 1.26,
    origin_x: 0.0,
    origin_y: 0.0,
    origin_z: 0.0,
  }),
  HexLattice: (template) => ({
    type: 'HexLattice',
    template,
    n_rings: 2,
    pitch: 1.26,
    orientation: 'pointy_top',
    center_x: 0.0,
    center_y: 0.0,
    center_z: 0.0,
  }),
  /**
   * BooleanPlacement is created from the Objects tab (multi-select → Boolean),
   * not from "Place template". The factory still accepts a dummy templateName
   * for API uniformity; callers should pass the selected child names via the
   * returned object's `children` field after creation.
   *
   * materials: [] means "all materials present in the children".
   * The Parameters panel should compute the available material set from the
   * children and present a multi-select; the stored list is the user's choice.
   */
  BooleanPlacement: (_templateName: string) => ({
    type: 'BooleanPlacement',
    op: 'union',
    children: [] as string[], // caller must set ≥2 placement names
    materials: [] as string[], // empty = all materials from children
    x: 0.0,
    y: 0.0,
    z: 0.0,
  }),
};

export const TEMPLATE_TYPES = Object.keys(TEMPLATE_DEFAULTS);
export const PLACEMENT_TYPES = Object.keys(PLACEMENT_DEFAULTS);

/** Generate a unique component name given a base ("my_fuel_pin", "core") */
export function uniqueName(base: string, existingNames: Set<string>): string {
  if (!existingNames.has(base)) return base;
  let i = 2;
  while (existingNames.has(`${base}_${i}`)) i++;
  return `${base}_${i}`;
}
