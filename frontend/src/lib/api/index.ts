// Typed API client — all fetch calls to the Cascade backend.
// Base URL reads from VITE_API_URL env var, defaults to localhost:8000.

import type {
  JobDetail,
  JobSummary,
  MaterialDetail,
  MaterialSummary,
  SceneResponse,
  SweepResponse,
  SweepResultsResponse,
  TallyResultSet,
  ValidationResponse,
  BackendProfile, ProfileCreatePayload, ProfileUpdatePayload,
} from '$lib/types';

const BASE = import.meta.env.VITE_API_URL ?? 'http://localhost:8000';

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
    const url = `${BASE}${path}`;

    console.log("GET", url);

    const res = await fetch(url, {
        headers: {
            "Content-Type": "application/json",
            ...options.headers,
        },
        ...options,
    });

    const text = await res.text();

    console.log(res.status, text);

    if (!res.ok) {
        throw new Error(`API ${res.status}: ${text}`);
    }

    return JSON.parse(text);
}

// ---------------------------------------------------------------------------
// Geometry
// ---------------------------------------------------------------------------

export const geometry = {
  validate: (text: string): Promise<ValidationResponse> =>
    request('/api/geometry/validate', {
      method: 'POST',
      body: JSON.stringify({ text }),
    }),

  scene: (text: string): Promise<SceneResponse> =>
    request('/api/geometry/scene', {
      method: 'POST',
      body: JSON.stringify({ text }),
    }),

  list: (): Promise<{ id: string; name: string; created_at: string; n_surfaces: number; n_cells: number }[]> =>
    request('/api/geometry/'),

  get: (id: string): Promise<{ id: string; name: string; created_at: string; n_surfaces: number; n_cells: number; yaml_text: string }> =>
    request(`/api/geometry/${id}`),

  save: (text: string, name?: string): Promise<{ id: string; name: string }> =>
    request('/api/geometry/', {
      method: 'POST',
      body: JSON.stringify({ text, name }),
    }),

  update: (id: string, text: string, name?: string): Promise<{ id: string; name: string }> =>
    request(`/api/geometry/${id}`, {
      method: 'PUT',
      body: JSON.stringify({ text, name }),
    }),

  delete: (id: string): Promise<{ deleted: boolean; id: string }> =>
    request(`/api/geometry/${id}`, { method: 'DELETE' }),
};

// ---------------------------------------------------------------------------
// Materials
// ---------------------------------------------------------------------------

export const materials = {
  search: (params: {
    search?: string;
    library_tag?: string;
    limit?: number;
    offset?: number;
  } = {}): Promise<{
    items: MaterialSummary[];
    total: number;
    limit: number;
    offset: number;
  }> => {
    const q = new URLSearchParams();
    if (params.search)      q.set('search',      params.search);
    if (params.library_tag) q.set('library_tag', params.library_tag);
    if (params.limit  != null) q.set('limit',  String(params.limit));
    if (params.offset != null) q.set('offset', String(params.offset));
    return request(`/api/materials/?${q}`);
  },

  libraries: (): Promise<string[]> =>
    request('/api/materials/libraries'),

  get: (id: string): Promise<MaterialDetail> =>
    request(`/api/materials/${id}`),

  create: (data: {
    name: string;
    density: number;
    composition: Record<string, number>;
  }, libraryTag = 'user'): Promise<MaterialDetail> =>
    request(`/api/materials/?library_tag=${libraryTag}`, {
      method: 'POST',
      body: JSON.stringify(data),
    }),

  update: (id: string, data: {
    name: string;
    density: number;
    composition: Record<string, number>;
  }): Promise<MaterialDetail> =>
    request(`/api/materials/${id}`, {
      method: 'PUT',
      body: JSON.stringify(data),
    }),

  delete: (id: string): Promise<{ deleted: boolean; id: string }> =>
    request(`/api/materials/${id}`, { method: 'DELETE' }),

  importJson: (file: File, libraryTag: string, overwrite = false): Promise<{
    imported: MaterialSummary[];
    skipped: string[];
    errors: string[];
  }> => {
    const form = new FormData();
    form.append('file', file);
    return request(
      `/api/materials/import/json?library_tag=${encodeURIComponent(libraryTag)}&overwrite=${overwrite}`,
      { method: 'POST', body: form, headers: {} },
    );
  },

  deleteLibrary: (libraryTag: string): Promise<{ deleted_count: number; library_tag: string }> =>
    request(`/api/materials/library/${encodeURIComponent(libraryTag)}`, { method: 'DELETE' }),
};

// ---------------------------------------------------------------------------
// Jobs
// ---------------------------------------------------------------------------

export const jobs = {
  list: (): Promise<JobSummary[]> =>
      request('/api/jobs/'),

  get: (id: string): Promise<JobDetail> =>
      request(`/api/jobs/${id}`),

  // NOTE: this is intentionally loosely typed (Record<string, unknown>)
  // rather than a precise interface. The real shape is mode-dependent
  // (JobSubmitRequest in api/jobs.py — eigenvalue/fixed_source/depletion
  // send `monte_carlo`+`results_config` [+`source`/`depletion`], r2s sends
  // `r2s`+`r2s_results_config` instead and must NOT set the others) and
  // validated server-side; a flat TS interface listing every field as
  // optional would silently allow nonsensical combinations (e.g. r2s +
  // `monte_carlo` — the exact bug this whole restructure fixed) without
  // catching them at compile time anyway. See job-settings-model.md.
  submit: (data: Record<string, unknown>): Promise<JobSummary | SweepResponse> =>
      request('/api/jobs/submit', {
        method: 'POST',
        body: JSON.stringify(data),
      }),

  // Uploads a file selected via a native file picker (depletion chain
  // files, r2s decay/activation libraries) and returns a reference to
  // store as the `chain_file`/`decay_library` value in a submit payload.
  // Follows the same FormData/no-Content-Type-header pattern as
  // materials.importJson below.
  uploadFile: (file: File, kind: 'chain' | 'decay_library'): Promise<{ file_id: string; filename: string }> => {
    const form = new FormData();
    form.append('file', file);
    form.append('kind', kind);
    return request('/api/jobs/files', { method: 'POST', body: form, headers: {} });
  },

  cancel: (id: string): Promise<JobSummary> =>
      request(`/api/jobs/${id}/cancel`, {method: 'POST'}),

  delete: (id: string): Promise<{ deleted: boolean; id: string }> =>
      request(`/api/jobs/${id}`, {method: 'DELETE'}),

  // Raw stdout from run.log — polled while job is running
  stdout: (id: string): Promise<{ lines: string; available: boolean }> =>
      request(`/api/jobs/${id}/stdout`),
};

// ---------------------------------------------------------------------------
// Backends
// ---------------------------------------------------------------------------

export const profiles = {
  list: (): Promise<BackendProfile[]> =>
    request('/api/jobs/backends/profiles/'),

  get: (name: string): Promise<BackendProfile> =>
    request(`/api/jobs/backends/profiles/${encodeURIComponent(name)}`),

  create: (data: ProfileCreatePayload): Promise<BackendProfile> =>
    request('/api/jobs/backends/profiles/', {
      method: 'POST',
      body: JSON.stringify(data),
    }),

  update: (name: string, data: ProfileUpdatePayload): Promise<BackendProfile> =>
    request(`/api/jobs/backends/profiles/${encodeURIComponent(name)}`, {
      method: 'PUT',
      body: JSON.stringify(data),
    }),

  delete: (name: string): Promise<void> =>
    request(`/api/jobs/backends/profiles/${encodeURIComponent(name)}`, {
      method: 'DELETE',
    }),
};

// ---------------------------------------------------------------------------
// Results
// ---------------------------------------------------------------------------

export const results = {
  summary: (id: string)  =>
      request(`/api/results/${id}/summary`),

  tallies: (id: string)  =>
      request(`/api/results/${id}/tallies`),

  mesh:    (id: string)  =>
      request(`/api/results/${id}/mesh`),
  spectra: (id: string)  =>
      request(`/api/results/${id}/spectra`),

  downloadUrl: (id: string) => `${BASE}/api/results/${id}/statepoint/path`,
};