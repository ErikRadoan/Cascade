// Mirrors OpenMCAdapter's import_summary/import_tallies/import_mesh/
// import_spectra response shapes — see results-dashboard-spec.md §2.
// Keep in sync with backend, same convention as $lib/types/index.ts.

export interface KEffEstimate {
  mean: number;
  std_dev: number;
}

export interface ImportSummaryResponse {
  job_id: string;
  batches: number;
  inactive: number;
  particles_per_batch: number;
  n_realizations: number;
  // Empty/absent on fixed-source legs — panel must hide gracefully, not error.
  k_effective: {
    combined: KEffEstimate;
    col_abs: KEffEstimate;
    abs_tra: KEffEstimate;
    col_tra: KEffEstimate;
  } | Record<string, never>;
  entropy_history: number[];
  keff_history: number[];
  timing: Record<string, number>;
}

export function hasKEff(s: ImportSummaryResponse): boolean {
  return s.k_effective != null && 'combined' in s.k_effective;
}

export interface TallyScore {
  mean: number;
  std_dev: number;
  rel_err: number;
}

export interface ImportTally {
  tally_id: number;
  name: string; // join key onto SceneResponse cell_name
  scores: Record<string, TallyScore>;
}

export interface ImportTalliesResponse {
  job_id: string;
  tallies: ImportTally[];
}

export type MeshType = 'regular' | 'cylindrical';

export interface ImportMeshResponse {
  job_id: string;
  tally_id: number;
  mesh: {
    type: MeshType;
    shape: [number, number, number];
    lower_left: [number, number, number];
    upper_right: [number, number, number];
  };
  scores: string[];
  data: Record<string, number>[]; // {ix,iy,iz, [score]_mean, [score]_std_dev}
}

export interface ImportSpectrum {
  tally_id: number;
  name: string;
  group_boundaries_ev: number[];
  group_midpoints_ev: number[];
  flux_mean: number[];
  flux_std_dev: number[];
}

export interface ImportSpectraResponse {
  job_id: string;
  group_structure: string;
  spectra: ImportSpectrum[];
}