// Typed API client — all fetch calls to the Cascade backend.
// Base URL reads from VITE_API_URL env var, defaults to localhost:8000.
//
// AUDIT NOTE (kept intentionally, don't delete on next edit): this file was
// compared line-by-line against the actual backend routers (geometry.py,
// materials.py, jobs.py, results.py) and OpenMCAdapter's import_* methods
// on 2026-07-12. Every comment below prefixed with "⚠️" documents either a
// fixed bug, a real backend quirk that isn't obvious from the endpoint name,
// or an *unverified* assumption — added so that a future editor (human or
// agent) doesn't have to re-derive backend behavior from scratch or
// silently trust a shape the backend doesn't actually guarantee.

import type {
    JobDetail, JobSummary, MaterialDetail, MaterialSummary, SceneResponse,
    SweepResponse, SweepResultsResponse, TallyResultSet, ValidationResponse,
    BackendProfile, ProfileCreatePayload, ProfileUpdatePayload,
    CsgGeometry, RasterResponse,
} from '$lib/types';

const BASE = import.meta.env.VITE_API_URL ?? 'http://localhost:8000';

/**
 * Shared fetch wrapper. Two behaviors worth knowing if you're editing this:
 *
 * 1. Content-Type is only set to `application/json` when the body is NOT
 *    FormData. File uploads (materials.importJson, jobs.uploadFile) send
 *    FormData and must NOT get an explicit Content-Type — the browser has
 *    to generate its own `multipart/form-data; boundary=...` value, and
 *    setting Content-Type manually breaks that.
 *
 *    ⚠️ Previously this was handled by call sites passing `headers: {}` and
 *    relying on `{ headers: {...}, ...options }` object-spread ordering to
 *    blow away the default. That happened to produce the right *result*
 *    (no Content-Type at all) but only by accident of spread order — the
 *    moment any call needed to combine a JSON body with a custom header
 *    (e.g. an Authorization header), the whole default Content-Type would
 *    have silently vanished too, because spreading `options` after `headers`
 *    replaces the headers object wholesale instead of merging into it.
 *    Fixed below by detecting FormData explicitly and merging headers as
 *    one object with `options` spread FIRST and `headers` computed LAST
 *    (so it always wins and is always a proper merge, never a full
 *    replacement).
 *
 * 2. Non-2xx responses throw `Error("API {status}: {raw body text}")`.
 *    Callers doing their own error handling should catch this and parse
 *    `.message` if they need the raw backend detail string.
 *
 * 3. A 204 (or any response with an empty body) resolves to `undefined`
 *    rather than being run through JSON.parse(). Most DELETE routes in
 *    this backend return a JSON DeletedResponse body, but at least one
 *    (backend_profiles.delete_profile, see `profiles.delete` below) uses
 *    `status_code=204` with no body — `JSON.parse("")` throws
 *    "Unexpected end of JSON input", so every 204 response needs this
 *    guard, not just the currently-known one. Callers expecting `void`
 *    get exactly that; callers expecting a real body just won't get one
 *    for a 204, which matches HTTP semantics anyway.
 */
async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
    const url = `${BASE}${path}`;

    const isFormData = typeof FormData !== 'undefined' && options.body instanceof FormData;

    const res = await fetch(url, {
        ...options,
        headers: {
            ...(isFormData ? {} : { "Content-Type": "application/json" }),
            ...options.headers,
        },
    });

    const text = await res.text();

    if (!res.ok) {
        throw new Error(`API ${res.status}: ${text}`);
    }

    if (res.status === 204 || text.length === 0) {
        return undefined as T;
    }

    return JSON.parse(text);
}

// ---------------------------------------------------------------------------
// Geometry
// ---------------------------------------------------------------------------
// Backend: geometry.py, router prefix "/geometry". Does NOT touch
// OpenMCAdapter — pure YAML validation / SceneBuilder expansion / CRUD
// over an in-memory dict (not yet persisted to DB, per geometry.py's
// module comment — expect this store to reset on backend restart).

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
    request(`/api/geometry/${encodeURIComponent(id)}`),

  // Backend returns the FULL GeometrySummary object (id, name, created_at,
  // n_surfaces, n_cells) on both save and update — previously this was
  // typed as just {id, name}, silently discarding created_at/n_surfaces/
  // n_cells that the response actually contains. Widened to match reality
  // so callers don't have to re-fetch immediately after saving just to get
  // the surface/cell counts they already received.
  //
  // ⚠️ save()/update() intentionally accept invalid/unparseable-YAML text
  // (geometry.py: "rejecting drafts would make autosave useless") — a 201/200
  // response here does NOT mean the geometry is valid, only that it was
  // stored. n_surfaces/n_cells will read 0 for a draft that doesn't expand
  // yet; that's expected, not an error state to surface to the user.
  save: (text: string, name?: string): Promise<{ id: string; name: string; created_at: string; n_surfaces: number; n_cells: number }> =>
    request('/api/geometry/', {
      method: 'POST',
      body: JSON.stringify({ text, name }),
    }),

  update: (id: string, text: string, name?: string): Promise<{ id: string; name: string; created_at: string; n_surfaces: number; n_cells: number }> =>
    request(`/api/geometry/${encodeURIComponent(id)}`, {
      method: 'PUT',
      body: JSON.stringify({ text, name }),
    }),

  // ⚠️ Return shape {deleted, id} is ASSUMED from the schema name
  // "DeletedResponse" and matches every *.py delete route's call pattern
  // (`DeletedResponse(id=...)`, no explicit `deleted=` passed) — but
  // schemas.py wasn't available to confirm `deleted` has a sensible
  // default (e.g. `deleted: bool = True`). If this type doesn't match
  // at runtime, check schemas.py's DeletedResponse definition first,
  // not this file.
  delete: (id: string): Promise<{ deleted: boolean; id: string }> =>
    request(`/api/geometry/${encodeURIComponent(id)}`, { method: 'DELETE' }),

  csg: (text: string): Promise<CsgGeometry> =>
    request('/api/geometry/csg', {
    method: 'POST',
    body: JSON.stringify({ text }),
    }),
};

// ---------------------------------------------------------------------------
// Materials
// ---------------------------------------------------------------------------
// Backend: materials.py, router prefix "/materials". File-backed CRUD via
// MaterialRepository (~/.cascade/materials.json) — no OpenMCAdapter
// involvement. Material IDs are server-generated as
// `body.name.replace(" ", "_").upper()` (Python replace() replaces ALL
// spaces, not just the first — this is not the JS-style single-replace
// footgun it might look like). IDs can still contain characters from the
// original name that aren't URL-safe (e.g. "/"), hence encodeURIComponent
// on every path use of `id` below.

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
    request(`/api/materials/${encodeURIComponent(id)}`),

  // FIXED: libraryTag was interpolated raw into the query string here
  // (`?library_tag=${libraryTag}`) while deleteLibrary() two functions
  // down correctly used encodeURIComponent for the exact same parameter.
  // A tag containing a space or `&` would previously either 400 on the
  // backend or silently truncate/split into the wrong query params.
  create: (data: {
    name: string;
    density: number;
    composition: Record<string, number>;
  }, libraryTag = 'user'): Promise<MaterialDetail> =>
    request(`/api/materials/?library_tag=${encodeURIComponent(libraryTag)}`, {
      method: 'POST',
      body: JSON.stringify(data),
    }),

  update: (id: string, data: {
    name: string;
    density: number;
    composition: Record<string, number>;
  }): Promise<MaterialDetail> =>
    request(`/api/materials/${encodeURIComponent(id)}`, {
      method: 'PUT',
      body: JSON.stringify(data),
    }),

  delete: (id: string): Promise<{ deleted: boolean; id: string }> =>
    request(`/api/materials/${encodeURIComponent(id)}`, { method: 'DELETE' }),

  // FormData body — see request()'s doc comment for why no Content-Type
  // header is passed here. This is correct as-is, not a bug.
  importJson: (file: File, libraryTag: string, overwrite = false): Promise<{
    imported: MaterialSummary[];
    skipped: string[];
    errors: string[];
  }> => {
    const form = new FormData();
    form.append('file', file);
    return request(
      `/api/materials/import/json?library_tag=${encodeURIComponent(libraryTag)}&overwrite=${overwrite}`,
      { method: 'POST', body: form },
    );
  },

  deleteLibrary: (libraryTag: string): Promise<{ deleted_count: number; library_tag: string }> =>
    request(`/api/materials/library/${encodeURIComponent(libraryTag)}`, { method: 'DELETE' }),
};

// ---------------------------------------------------------------------------
// Jobs
// ---------------------------------------------------------------------------
// Backend: jobs.py, router prefix "/jobs". Does NOT call OpenMCAdapter
// directly anywhere — /scene goes through SceneBuilder the same way
// geometry.scene() does, over the job's persisted geometry_text.

export const jobs = {
  list: (): Promise<JobSummary[]> =>
      request('/api/jobs/'),

  get: (id: string): Promise<JobDetail> =>
      request(`/api/jobs/${encodeURIComponent(id)}`),

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
  // FormData body — see request()'s doc comment; no Content-Type here is
  // correct, not an oversight.
  uploadFile: (file: File, kind: 'chain' | 'decay_library'): Promise<{ file_id: string; filename: string }> => {
    const form = new FormData();
    form.append('file', file);
    form.append('kind', kind);
    return request('/api/jobs/files', { method: 'POST', body: form });
  },

  cancel: (id: string): Promise<JobSummary> =>
      request(`/api/jobs/${encodeURIComponent(id)}/cancel`, { method: 'POST' }),

  delete: (id: string): Promise<{ deleted: boolean; id: string }> =>
      request(`/api/jobs/${encodeURIComponent(id)}`, { method: 'DELETE' }),

  // Raw stdout from run.log — polled while job is running
  stdout: (id: string): Promise<{ lines: string; available: boolean }> =>
      request(`/api/jobs/${encodeURIComponent(id)}/stdout`),

  // Renders this job's stored geometry_text into a scene, the same shape
  // geometry.scene() returns for the live editor. 404s if the job predates
  // geometry_text being persisted (see repositories/models.py) — callers
  // should treat that as "no preview available", not an error to surface.
  scene: (id: string): Promise<SceneResponse> =>
      request(`/api/jobs/${encodeURIComponent(id)}/scene`),

  csg: (id: string): Promise<CsgGeometry> =>
    request(`/api/jobs/${encodeURIComponent(id)}/csg`),

  raster: (id: string, params: {
  axis: 'x' | 'y' | 'z';
  coord: number;
  h_min: number; h_max: number;
  v_min: number; v_max: number;
  resolution?: number;
}): Promise<RasterResponse> => {
  const q = new URLSearchParams({
    axis: params.axis,
    coord: String(params.coord),
    h_min: String(params.h_min), h_max: String(params.h_max),
    v_min: String(params.v_min), v_max: String(params.v_max),
  });
  if (params.resolution != null) q.set('resolution', String(params.resolution));
  return request(`/api/jobs/${encodeURIComponent(id)}/raster?${q}`);
},
};

// ---------------------------------------------------------------------------
// Backends / profiles
// ---------------------------------------------------------------------------
// CONFIRMED (backends.py + backend_profile.py + profile_registry.py):
// a real, DB-backed CRUD router does exist for this — it just lives in
// its own file (backends.py, router variable name "profiles_router"
// per its own module docstring), separate from jobs.py, and needs its
// own include_router() call in the main app rather than being folded
// into jobs.py.
//
// ⚠️ ONE THING STILL WORTH CHECKING: backends.py's own router is already
// declared with `prefix="/jobs/backends/profiles"` —
//   router = APIRouter(prefix="/jobs/backends/profiles", ...)
// but that file's module docstring tells you to mount it as:
//   app.include_router(profiles_router, prefix="/api/jobs/backends/profiles")
// If main.py actually followed that docstring literally, the live path
// becomes /api/jobs/backends/profiles/jobs/backends/profiles/... (prefix
// applied twice) and every call below 404s. The other three routers in
// this codebase (geometry "/geometry", materials "/materials", jobs
// "/jobs") all bake their own top-level segment into the router itself
// and are presumably mounted with just `prefix="/api"` at the app level
// — if backends.py's router is mounted the SAME way (prefix="/api" only,
// ignoring its own docstring), the resulting path is exactly
// /api/jobs/backends/profiles/... which matches every URL below. Quick
// way to confirm either way: hit GET /api/jobs/backends/profiles/ once
// and see whether it 404s.
//
// Behavior worth knowing about individual routes, straight from
// backends.py:
//  - POST / and PUT /{name} both run config_data through the matching
//    Pydantic model (DockerBackendConfig/LocalBackendConfig/
//    SlurmBackendConfig) server-side — a 422 here means config_data
//    didn't match the shape for the given backend_type, with
//    pydantic's raw .errors() list as the detail body, not a plain string.
//  - The name "default" is reserved: POST / with name="default" → 409
//    (must PUT to edit the seeded default instead of creating it).
//  - DELETE on "default" → 409 as well (registry.delete raises ValueError
//    for it, caught and turned into 409). Disable/hide delete for the
//    "default" row in any profile-management UI rather than letting the
//    request round-trip just to fail.
//  - DELETE succeeds with 204 + empty body — see request()'s doc comment
//    above; this is exactly the case that needed the empty-body fix.

// Actual wire shapes, from backends.py's Pydantic models — cross-check
// against $lib/types' BackendProfile/ProfileCreatePayload/
// ProfileUpdatePayload if anything here looks mismatched:
//   ProfileResponse (= what BackendProfile should match):
//     { name: string, backend_type: string, config_data: dict,
//       description: string | null, created_at: string, updated_at: string }
//     (created_at/updated_at are ISO strings via .isoformat(), not epoch numbers)
//   ProfileCreateRequest (= ProfileCreatePayload):
//     { name: string, backend_type: string, config_data: dict,
//       description?: string | null }
//     name must match ^[a-zA-Z0-9_\-]+$, 1-64 chars, or 422s.
//   ProfileUpdateRequest (= ProfileUpdatePayload):
//     { backend_type: string, config_data: dict, description?: string | null }
//     (no `name` field — it's the path param, not part of the body)
//   `config_data` itself is loosely `dict` server-side too (validated
//   dynamically against whichever of DockerBackendConfig/
//   LocalBackendConfig/SlurmBackendConfig matches backend_type) — same
//   "can't be one flat interface without allowing nonsensical
//   combinations" situation as jobs.submit()'s payload above.
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
// Backend: results.py, router prefix "/results". These are the ONLY
// endpoints in the whole API that go through OpenMCAdapter
// (import_summary/import_tallies/import_mesh/import_spectra). All four
// share these pre-adapter failure modes from results.py, before any of
// the response shapes below even apply:
//   404 — job not found
//   409 — job not COMPLETED yet (job.effective_status())
//   404 — no statepoint.*.h5 found in the resolved output dir
//   422 — statepoint file exists but h5py couldn't open it
//
// Local response types are declared here (not imported from $lib/types)
// because, as of this audit, $lib/types either didn't have matching
// interfaces or they didn't capture the optionality/caveats noted below.
// If $lib/types grows real interfaces for these later: prefer importing
// them, but verify they actually encode the "can be empty/partial" cases
// before deleting these and the comments attached to them — that context
// is exactly what caused the neutron_balance bug to go unnoticed.

interface KEffEstimate {
  mean: number;
  std_dev: number;
}

interface ResultsSummaryResponse {
  job_id: string;
  batches: number;              // 0 if missing from statepoint, not an error
  inactive: number;              // 0 if missing
  particles_per_batch: number;   // 0 if missing
  n_realizations: number;        // falls back to max(1, batches - inactive) if absent from file

  /**
   * ⚠️ Only the estimator keys actually present in the statepoint are
   * included — this is NOT a fixed 4-key object. Fixed-source runs have
   * NO k-eigenvalue estimators at all, so `k_effective` is `{}` (empty),
   * not zeroed. Check for key presence (or a dedicated hasKEff() helper)
   * before reading `.combined`/`.col_abs`/`.abs_tra`/`.col_tra` — don't
   * assume all four (or any) exist.
   */
  k_effective: Partial<Record<'combined' | 'col_abs' | 'abs_tra' | 'col_tra', KEffEstimate>>;

  entropy_history: number[];  // [] if neither entropy key present in file
  keff_history: number[];     // [] if "k_generation" absent

  /**
   * ⚠️⚠️ KNOWN BACKEND BUG — do not trust these key names as-is.
   * adapters/openmc_adapter.py's import_summary() builds this object by
   * zip-ing OpenMC's global_tallies array positionally against
   * ["leakage", "absorption", "fission", "nu_fission"]. The array's real
   * row order is [k_collision, k_absorption, k_tracklength, leakage] —
   * OpenMC's global_tallies structurally only ever contains k-effective
   * estimators plus leakage, never absorption/fission/nu-fission reaction
   * rates. So today:
   *   .leakage    is actually k_collision   (a k-eff estimator, ~1.0)
   *   .absorption is actually k_absorption  (a k-eff estimator, ~1.0)
   *   .fission    is actually k_tracklength (a k-eff estimator, ~1.0)
   *   .nu_fission is actually the REAL leakage value
   * There is currently no code path anywhere that computes a genuine
   * absorption/fission/nu-fission neutron balance — that would require a
   * dedicated filterless scoring tally added at export_tallies-time,
   * which doesn't exist yet. Do not build or trust UI on these key names
   * until the backend actually computes real balance data; if you're
   * looking at "all zeros" here, prefer the .nu_fission key (real
   * leakage) as the one number in this object that's currently honest.
   */
  neutron_balance: Record<string, number>;

  timing: Record<string, number>;  // often {} — arbitrary keys from sp["runtime"], no fixed schema
}

interface MeshResultResponse {
  job_id: string;
  tally_id: number;
  /**
   * ⚠️ Can be `{}` (all four fields absent) if the statepoint's `meshes`
   * HDF5 group is missing or empty — this is a 200 OK, not an error.
   * Guard with `mesh.shape != null` (or similar) before indexing into
   * shape/lower_left/upper_right; `type` comes from the request's
   * mesh_type param, not from the file, so it's present whenever `mesh`
   * is non-empty.
   */
  mesh: {
    type?: string;
    shape?: number[];
    lower_left?: number[];
    upper_right?: number[];
  };
  scores: string[];
  /**
   * ⚠️ Can be `[]` (still 200 OK, not 404) if the tally-200 group exists
   * but its `results` dataset is missing. An empty array here means "no
   * data yet", not necessarily an error — don't treat it the same as the
   * 404 case (mesh not requested / statepoint missing), which the route
   * DOES raise for.
   * Row-major over (nx, ny, nz), one object per voxel, NOT sparse.
   * Keys are dynamic: `${score}_mean` / `${score}_std_dev` for each score
   * in `scores` above — the exact key set varies per job.
   */
  data: Record<string, number>[];
}

interface SpectrumEntry {
  tally_id: number;
  /**
   * ⚠️ Unlike import_tallies (which does NOT trust the HDF5 `name` attr —
   * see TallyResultSet's tallies[].name caveat, if present, elsewhere),
   * import_spectra DOES read `t.attrs.get("name", fallback)` directly,
   * falling back to the raw group key (e.g. "tally 301") only if the
   * attribute truly isn't set. These two importers make different trust
   * decisions about the same kind of HDF5 attribute for reasons that
   * weren't confirmed during this audit — if spectra names ever look
   * wrong, check whether spectra tally groups actually persist `name`
   * where scalar tally groups don't, or whether this is the same
   * never-persisted-attribute bug and it's just gone unnoticed because
   * "tally 301" doesn't look obviously wrong the way a mislabeled pin
   * name would.
   */
  name: string;
  group_boundaries_ev: number[]; // length = n_energy_bins + 1
  group_midpoints_ev: number[];  // length = n_energy_bins, sqrt(lo*hi) per bin
  flux_mean: number[];           // length = n_energy_bins
  flux_std_dev: number[];        // length = n_energy_bins
}

interface SpectraResultResponse {
  job_id: string;
  group_structure: string;  // echoed from results_config, NOT read from the statepoint
  spectra: SpectrumEntry[];
}

export const results = {
  summary: (id: string): Promise<ResultsSummaryResponse> =>
      request(`/api/results/${encodeURIComponent(id)}/summary`),

  /**
   * ⚠️ Tally `name` here is RECONSTRUCTED, not read from the file — OpenMC
   * doesn't persist the XML `name=` attribute on a scalar tally's HDF5
   * group. import_tallies() replays export_tallies()'s exact cell-selection
   * loop over `geometry.cells` (skip void cells, skip non-fissile cells
   * unless all_cells) and zips `tally_id - 101` against that list's index.
   * This only produces correct names if the geometry/materials/scalars_cfg
   * passed to the backend at read-time are identical to what the job was
   * submitted with. If geometry was edited after submission, `name` can
   * silently point at the wrong cell with no error — a bad `cell_name`
   * join key for the 3D viewer, not a crash.
   *
   * ⚠️ `scores[x].rel_err` is hardcoded to 0.0 when mean is 0 (not NaN,
   * not omitted) — different from this same file's own zero-mean
   * convention elsewhere (compare to the frontend's `pct()` helper, which
   * renders '—' for the equivalent case). Don't assume rel_err === 0
   * means "genuinely zero relative error".
   */
  tallies: (id: string): Promise<TallyResultSet> =>
      request(`/api/results/${encodeURIComponent(id)}/tallies`),

  mesh: (id: string): Promise<MeshResultResponse> =>
      request(`/api/results/${encodeURIComponent(id)}/mesh`),

  /**
   * ⚠️ Inconsistent error behavior vs. mesh(): if the statepoint has no
   * spectra data, this endpoint returns 200 with `spectra: []`, computed
   * inside the adapter itself rather than raised as an exception. mesh()
   * 404s for its equivalent "tally group missing" case. Don't copy-paste
   * error handling between these two — check for an empty array here,
   * not a caught 404.
   */
  spectra: (id: string): Promise<SpectraResultResponse> =>
      request(`/api/results/${encodeURIComponent(id)}/spectra`),

  downloadUrl: (id: string): string => `${BASE}/api/results/${encodeURIComponent(id)}/download`,
};