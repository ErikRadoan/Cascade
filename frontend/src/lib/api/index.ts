export type ApiResponse<T> = Promise<T>;

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
};

// ---------------------------------------------------------------------------
// Materials
// ---------------------------------------------------------------------------

export const materials = {
  list: (): Promise<MaterialSummary[]> =>
    request('/api/materials/'),

  get: (id: string): Promise<MaterialDetail> =>
    request(`/api/materials/${id}`),

  create: (data: {
    name: string;
    density: number;
    composition: Record<string, number>;
  }): Promise<MaterialDetail> =>
    request('/api/materials/', { method: 'POST', body: JSON.stringify(data) }),

  delete: (id: string): Promise<{ deleted: boolean; id: string }> =>
    request(`/api/materials/${id}`, { method: 'DELETE' }),
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