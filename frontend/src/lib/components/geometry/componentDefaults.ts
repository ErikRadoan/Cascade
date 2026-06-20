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