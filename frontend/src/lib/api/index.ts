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
} from '$lib/types';

const BASE = import.meta.env.VITE_API_URL ?? 'http://localhost:8000';

async function request<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json', ...options.headers },
    ...options,
  });
  if (!res.ok) {
    const detail = await res.text();
    throw new Error(`API ${res.status}: ${detail}`);
  }
  return res.json() as Promise<T>;
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

  submit: (data: {
    geometry_text: string;
    material_ids: string[];
    backend_config?: Record<string, unknown>;
    particles?: number;
    inactive?: number;
    batches?: number;
    notes?: string;
  }): Promise<JobSummary> =>
    request('/api/jobs/submit', { method: 'POST', body: JSON.stringify(data) }),

  sweep: (data: {
    geometry_text: string;
    material_ids: string[];
    backend_config?: Record<string, unknown>;
    particles?: number;
    inactive?: number;
    batches?: number;
    notes?: string;
  }): Promise<SweepResponse> =>
    request('/api/jobs/sweep', { method: 'POST', body: JSON.stringify(data) }),

  cancel: (id: string): Promise<JobSummary> =>
    request(`/api/jobs/${id}/cancel`, { method: 'POST' }),

  delete: (id: string): Promise<{ deleted: boolean; id: string }> =>
    request(`/api/jobs/${id}`, { method: 'DELETE' }),

  backends: (): Promise<
    { type: string; label: string; description: string; schema: unknown; default: unknown }[]
  > => request('/api/jobs/backends/available'),
};

// ---------------------------------------------------------------------------
// Results
// ---------------------------------------------------------------------------

export const results = {
  get: (jobId: string): Promise<TallyResultSet> =>
    request(`/api/results/${jobId}`),

  sweep: (sweepId: string): Promise<SweepResultsResponse> =>
    request(`/api/results/sweep/${sweepId}`),

  downloadUrl: (jobId: string): string =>
    `${BASE}/api/results/${jobId}/download`,
};