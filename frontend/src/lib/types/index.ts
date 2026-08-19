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
  // Join key for matching a /results/{job_id}/tallies entry's `name` to
  // this specific layer — see openmc_adapter.py's _append_scalar_tallies
  // and expander.py's _build_fuel_pin_cells. Empty string for scenes built
  // before this field existed (backend defaults it to "").
  cell_name: string;
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
  // See CylinderLayer.cell_name — same join key, for the box's fill cell.
  cell_name: string;
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

// ---------------------------------------------------------------------------
// Job configuration — mirrors the body JobSubmitModal.svelte POSTs to
// /api/jobs/submit (see its `body = {...}` construction). These are NOT
// confirmed to be returned yet by GET /api/jobs/{id} — JobDetail below
// includes them as optional so the frontend degrades gracefully (fields
// just don't render) until the backend's job-detail response/schema is
// extended to echo the stored submission config back. The job record
// already has to persist this somewhere to run the job at all, so this
// is very likely a schema-serialization addition on the backend rather
// than new storage.
// ---------------------------------------------------------------------------

export type RunMode = 'eigenvalue' | 'fixed_source' | 'depletion' | 'r2s';

export interface MonteCarloSettings {
  particles: number;
  batches: number;
  seed: number;
  // Only present for eigenvalue / depletion (k-eigenvalue source
  // convergence — job-settings-model.md §2). Absent for fixed_source
  // and every r2s leg.
  inactive?: number;
}

export interface SourceSpec {
  particle: 'neutron' | 'photon';
  space_type: 'point' | 'box';
  space_params: number[];  // length 3 for 'point', length 6 for 'box'
  energy_mev?: number;      // required (by the modal) when particle === 'photon'
}

export interface DepletionSpec {
  power_W: number;
  timesteps: number[];
  chain_file: string;       // "{file_id}/{filename}" upload reference, not a bare filename
  integrator: string;
  substeps: number;
}

export type TallyScore =
  | 'flux' | 'fission' | 'absorption' | 'heating'
  | 'nu-fission' | 'heating-local' | 'scatter';

export interface ScalarTallyConfig {
  enabled: boolean;
  scores: TallyScore[];
  all_cells: boolean;
}

export interface MeshTallyConfig {
  enabled: boolean;
  mesh_type: 'regular' | 'cylindrical';
  nx: number; ny: number; nz: number;
  nr: number; nz_cyl: number;
  scores: TallyScore[];
}

export interface SpectraConfig {
  enabled: boolean;
  group_structure: '33' | '69' | '252';
  per_material: boolean;
}

export interface DiagnosticsConfig {
  stochastic_volumes: boolean;
  particle_tracks: boolean;
  n_tracks: number;
}

/** Single-leg results capture config — used directly for eigenvalue /
 *  fixed_source / depletion, and per-leg for r2s (see R2SResultsConfigSpec). */
export interface ResultsConfigSpec {
  particle_type: 'neutron' | 'photon';
  scalars: ScalarTallyConfig;
  mesh: MeshTallyConfig;
  // Neutron-only — group structures are neutron multigroup library names,
  // not meaningful for a photon leg (job-settings-model.md §5).
  spectra?: SpectraConfig;
  diagnostics?: DiagnosticsConfig;
  // Photon-leg-only — dose-conversion-weighted flux.
  apply_dose_conversion?: boolean;
}

export interface R2SNeutronLegSource {
  particle: 'neutron';
  space_type: 'point' | 'box';
  space_params: number[];
}

export interface R2SActivation {
  irradiation_schedule: {
    power_W: number;
    timesteps: number[];
  };
  cooling_times: number[];
  decay_library: string;  // "{file_id}/{filename}" upload reference
}

export interface R2SPhotonVR {
  weight_windows_enabled: boolean;
}

/** R2S is a pipeline, not a parameterized single run — independent MC
 *  settings per leg (job-settings-model.md §4). */
export interface R2SPipelineSpec {
  neutron_leg_source: R2SNeutronLegSource;
  neutron_leg_mc:     MonteCarloSettings;   // no `inactive` — always fixed-source-shaped
  activation:         R2SActivation;
  photon_leg_mc:      MonteCarloSettings;
  photon_leg_vr:      R2SPhotonVR;
}

export interface R2SResultsConfigSpec {
  neutron_leg: ResultsConfigSpec;
  photon_leg:  ResultsConfigSpec;
}

export interface JobDetail extends JobSummary {
  geometry_id: string;
  started_at: string | null;
  finished_at: string | null;
  error: string | null;
  working_dir: string | null;

  // --- Submission config — optional, see block comment above. ---
  run_mode?:      RunMode;
  material_ids?:  string[];
  // Present for eigenvalue / fixed_source / depletion; absent for r2s.
  monte_carlo?:   MonteCarloSettings;
  source?:        SourceSpec;         // fixed_source only
  depletion?:     DepletionSpec;      // depletion only
  results_config?: ResultsConfigSpec; // eigenvalue / fixed_source / depletion only
  // Present for r2s; absent for every other run_mode.
  r2s?:                R2SPipelineSpec;
  r2s_results_config?: R2SResultsConfigSpec;
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

// ---------------------------------------------------------------------------
// CSG geometry (for client-side geometry-plot rasterization)
// ---------------------------------------------------------------------------

export type CsgSurfaceType =
  | 'plane_x' | 'plane_y' | 'plane_z'
  | 'cylinder_x' | 'cylinder_y' | 'cylinder_z'
  | 'sphere' | 'cone_z' | 'torus';

export interface CsgSurface {
  id: string;
  type: CsgSurfaceType;
  params: Record<string, number>;
  boundary_type: string;
}

export type RegionNode =
  | { op: 'inside'; surface: string }
  | { op: 'outside'; surface: string }
  | { op: 'and'; items: RegionNode[] }
  | { op: 'or'; items: RegionNode[] }
  | { op: 'not'; item: RegionNode };

export interface CsgCell {
  id: string;
  material_id: string | null;
  name: string | null;
  region: RegionNode;
}

// ---------------------------------------------------------------------------
// Lattice instancing (CSG_VIEWER_SCALING_PLAN.md Phase C)
//
// Additive, side-channel data alongside the existing flat surfaces/cells —
// see backend/cascade/domain/geometry.py's LatticeInstance and
// services/csg_export_service.py. `instances` are (x, y, z) offsets
// relative to instance 0's own position (instances[0] is always
// [0, 0, 0]) — translation only, no rotation component, since neither
// SquareLatticeSchema nor HexLatticeSchema varies orientation per pin
// today. Consumers that don't use this field can ignore it entirely and
// fall back to the flat surfaces/cells lists, which are always complete
// on their own (this is a side-channel accelerator, not a replacement).
// ---------------------------------------------------------------------------

export interface LatticeInstance {
  lattice_name: string;
  prototype_key: string;
  prototype_surfaces: CsgSurface[];
  prototype_cells: CsgCell[];
  instances: [number, number, number][];
}

export interface CsgGeometry {
  surfaces: CsgSurface[];
  cells: CsgCell[];
  lattice_instances: LatticeInstance[];
}

export interface RasterLegendEntry {
  cell_name: string | null;
  material_id: string | null;
}

export interface RasterResponse {
  width: number;
  height: number;
  cell_index: number[]; // flattened row-major, -1 = void
  legend: RasterLegendEntry[];
}
