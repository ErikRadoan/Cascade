export type Id = string;

// Types mirroring the backend Pydantic schemas.
// Keep in sync with backend/cascade/api/schemas.py

// ---------------------------------------------------------------------------
// Geometry
// ---------------------------------------------------------------------------

export interface ValidationError {
  type: 'yaml' | 'structure' | 'validation';
  message: string;
  component: string | null;
  field: string | null;
  line: number | null;
}

export interface ValidationResponse {
  valid: boolean;
  errors: ValidationError[];
}

export interface CylinderLayer {
  r_inner: number;
  r_outer: number;
  height: number;
  z_base: number;
  material_id: string;
  color: string;
  opacity: number;
  label: string;
}

export interface WireframeBox {
  x_size: number;
  y_size: number;
  z_size: number;
  z_base: number;
  color: string;
  boundary_type: string;
  fill_material_id: string;
  fill_color: string;
  fill_opacity: number;
}

export interface SceneComponent {
  type: string;           // "FuelPin" | "Box"
  name: string;
  position: [number, number, number];
  layers: CylinderLayer[];
  box: WireframeBox | null;
}

export interface SceneBounds {
  x_min: number; x_max: number;
  y_min: number; y_max: number;
  z_min: number; z_max: number;
}

export interface SceneResponse {
  components: SceneComponent[];
  material_colors: Record<string, string>;
  bounds: SceneBounds;
  error: string | null;
}

// ---------------------------------------------------------------------------
// Materials
// ---------------------------------------------------------------------------

export interface MaterialSummary {
  id: string;
  name: string;
  density: number | null;
}

export interface MaterialDetail extends MaterialSummary {
  composition: Record<string, number>;
}

// ---------------------------------------------------------------------------
// Jobs
// ---------------------------------------------------------------------------

export type JobStatus = 'queued' | 'running' | 'completed' | 'failed' | 'cancelled';

export interface JobSummary {
  id: string;
  status: JobStatus;
  backend: string;
  param_values: Record<string, number>;
  created_at: string;
  notes: string | null;
}

export interface JobDetail extends JobSummary {
  geometry_id: string;
  started_at: string | null;
  finished_at: string | null;
  error: string | null;
  working_dir: string | null;
}

export interface SweepResponse {
  sweep_id: string;
  jobs: JobSummary[];
  total: number;
}

// ---------------------------------------------------------------------------
// Results
// ---------------------------------------------------------------------------

export interface TallyResultSet {
  job_id: string;
  param_values: Record<string, number>;
  tallies: unknown[];
  k_effective: number | null;
  k_uncertainty: number | null;
}

export interface SweepResultsResponse {
  sweep_id: string;
  points: TallyResultSet[];
}

// ---------------------------------------------------------------------------
// UI state types (not from backend)
// ---------------------------------------------------------------------------

export type ActiveTab = 'geometry' | 'jobs' | 'results';

export interface SelectedItem {
  kind: 'template' | 'placement';
  name: string;
}