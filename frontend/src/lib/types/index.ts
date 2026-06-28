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
// Backend Profiles
// ---------------------------------------------------------------------------

export type BackendType = 'docker' | 'local' | 'slurm';

/** Config shapes per backend type — stored as config_data on a BackendProfile. */
export interface DockerBackendConfig {
  type:                          'docker';
  cli:                           'podman' | 'docker';
  image:                         string;
  openmc_bin:                    string;
  nuclear_data_path:             string;
  nuclear_data_container_path:   string;
  jobs_base_dir:                 string;
  memory_limit:                  string;
  cpu_limit:                     string;
}

export interface LocalBackendConfig {
  type:                'local';
  openmc_bin:          string;
  nuclear_data_path:   string;
  jobs_base_dir:       string;
}

export interface SlurmBackendConfig {
  type:              'slurm';
  host:              string;
  username:          string;
  ssh_key_path:      string;
  ssh_port:          number;
  partition:         string;
  nodes:             number;
  tasks_per_node:    number;
  walltime:          string;
  memory_per_node:   string;
  account:           string | null;
  openmc_module:     string | null;
  openmc_bin:        string;
  remote_work_dir:   string;
  nuclear_data_path: string;
  jobs_base_dir:     string;
}

export type BackendConfigData =
  | Omit<DockerBackendConfig, 'type'>
  | Omit<LocalBackendConfig,  'type'>
  | Omit<SlurmBackendConfig,  'type'>;

/** A named, saved backend configuration — mirrors backend BackendProfile domain model. */
export interface BackendProfile {
  name:         string;
  backend_type: BackendType;
  config_data:  Record<string, unknown>;
  description:  string | null;
  created_at:   string;   // ISO-8601 UTC
  updated_at:   string;   // ISO-8601 UTC
}

/** POST /api/jobs/backends/profiles/ */
export interface ProfileCreatePayload {
  name:          string;
  backend_type:  BackendType;
  config_data:   Record<string, unknown>;
  description?:  string | null;
}

/** PUT /api/jobs/backends/profiles/{name} */
export interface ProfileUpdatePayload {
  backend_type:  BackendType;
  config_data:   Record<string, unknown>;
  description?:  string | null;
}

// ---------------------------------------------------------------------------
// UI state types (not from backend)
// ---------------------------------------------------------------------------

export type ActiveTab = 'geometry' | 'jobs' | 'results';

export interface SelectedItem {
  kind: 'template' | 'placement';
  name: string;
}