<script lang="ts">
    import { projects, jobsState } from '$lib/stores/index.svelte';
  import { onMount } from 'svelte';
  import * as api from '$lib/api';
  import type { BackendProfile } from '$lib/types';

  interface Props {
    profiles:  BackendProfile[];
    onClose:   () => void;
    onManageBackends: () => void;
  }

  let { profiles, onClose, onManageBackends }: Props = $props();

  // ---------------------------------------------------------------------------
  // Run modes
  // ---------------------------------------------------------------------------

  type RunMode = 'eigenvalue' | 'fixed_source' | 'depletion' | 'r2s';

  const RUN_MODES: Record<RunMode, { label: string; description: string }> = {
    eigenvalue:   { label: 'Eigenvalue (k-eff)', description: 'Criticality calculation. Returns k-effective.' },
    fixed_source: { label: 'Fixed Source',        description: 'Fixed neutron/photon source transport. No k-eff.' },
    depletion:    { label: 'Depletion / Burnup',  description: 'Time-dependent burnup using a depletion chain.' },
    r2s:          { label: 'R2S (activation)',     description: 'Rigorous Two-Step: neutron leg → activation → photon leg.' },
  };

  // ---------------------------------------------------------------------------
  // Geometry list
  // ---------------------------------------------------------------------------

  interface GeometrySummary {
    id: string;
    name: string;
    created_at: string;
    n_surfaces: number;
    n_cells: number;
  }

  let geometries:        GeometrySummary[] = $state([]);
  let geometriesLoading: boolean           = $state(true);
  let geometriesError:   string | null     = $state(null);
  let selectedGeomId:    string | null     = $state(null);

  onMount(async () => {
    try {
      geometries = await api.geometry.list();
      if (geometries.length > 0) selectedGeomId = geometries[0].id;
    } catch (e: unknown) {
      geometriesError = e instanceof Error ? e.message : String(e);
    } finally {
      geometriesLoading = false;
    }
  });

  // ---------------------------------------------------------------------------
  // Form state
  // ---------------------------------------------------------------------------

  let runMode         = $state<RunMode>('eigenvalue');
  let notes           = $state('');
  let selectedProfile = $state<string>(profiles[0]?.name ?? 'default');

  // ---------------------------------------------------------------------------
  // Monte Carlo settings — single leg (eigenvalue / fixed_source / depletion)
  //
  // `inactive` only applies to eigenvalue and depletion (a k-eigenvalue
  // source-convergence concept — job-settings-model.md §2). It is simply
  // never read for fixed_source, and r2s doesn't use this block at all —
  // each of its two legs has independent settings below. This replaces a
  // single particles/batches/seed section that was previously shown
  // unconditionally for every mode, including r2s, where it was ambiguous
  // which leg (if either) it was supposed to describe.
  // ---------------------------------------------------------------------------
  let mcParticles = $state(1000);
  let mcBatches   = $state(100);
  let mcSeed      = $state(1);
  let mcInactive  = $state(20);

  const needsInactive = $derived(runMode === 'eigenvalue' || runMode === 'depletion');

  // ---------------------------------------------------------------------------
  // Source — required for fixed_source (job-settings-model.md §3.2: this was
  // previously impossible to submit at all). Not used for eigenvalue
  // (geometry-driven) or depletion.
  // ---------------------------------------------------------------------------
  let fsParticle     = $state<'neutron' | 'photon'>('neutron');
  let fsSpaceType    = $state<'point' | 'box'>('point');
  let fsSpaceParams  = $state('0, 0, 0');
  let fsEnergyMev    = $state<number | null>(null);   // required if fsParticle === 'photon'

  // ---------------------------------------------------------------------------
  // Depletion-specific
  // ---------------------------------------------------------------------------
  let depPowerW     = $state(1000.0);
  let depTimesteps  = $state('10, 20, 30');
  let depChainFile  = $state('');           // NEW — was only a free-text warning before, now a real required field
  let depIntegrator = $state('predictor');
  let depSubsteps   = $state(1);

  // ---------------------------------------------------------------------------
  // R2S — a pipeline, not a parameterized single run (job-settings-model.md §4).
  // Independent particles/batches/seed per leg — a photon leg typically needs
  // far more particles than the neutron leg, so these are never shared.
  // ---------------------------------------------------------------------------
  let r2sNeutronSpaceType = $state<'point' | 'box'>('box');
  let r2sNeutronParams    = $state('-5, -5, -5, 5, 5, 5');
  let r2sNeutronParticles = $state(10000);
  let r2sNeutronBatches   = $state(50);
  let r2sNeutronSeed      = $state(1);

  let r2sPowerW        = $state(1000.0);
  let r2sTimesteps     = $state('30');
  let r2sCoolingTimes  = $state('0, 3600, 86400');
  let r2sDecayLibrary  = $state('');        // NEW — previously missing entirely

  let r2sPhotonParticles     = $state(500000);
  let r2sPhotonBatches       = $state(50);
  let r2sPhotonSeed          = $state(2);
  let r2sPhotonWeightWindows = $state(false);

  let submitting  = $state(false);
  let submitError = $state<string | null>(null);

  // ---------------------------------------------------------------------------
  // Results config state — NEUTRON leg.
  //
  // Used directly for eigenvalue / fixed_source / depletion (their only
  // leg), and as r2s's neutron_leg. Scores here are the full neutron-context
  // set (job-settings-model.md §5).
  // ---------------------------------------------------------------------------

  // Group 2 — scalar cell tallies
  let scalarsEnabled  = $state(true);
  let scalarsAllCells = $state(false);
  const ALL_SCORES_NEUTRON = ['flux', 'fission', 'absorption', 'heating', 'nu-fission', 'heating-local', 'scatter'] as const;
  type NeutronScore = typeof ALL_SCORES_NEUTRON[number];
  let scalarsScores = $state<Set<NeutronScore>>(new Set(['flux', 'fission', 'absorption', 'heating']));

  // Group 3 — mesh tally
  let meshEnabled  = $state(false);
  let meshType     = $state<'regular' | 'cylindrical'>('regular');
  let meshNx       = $state(20);
  let meshNy       = $state(20);
  let meshNz       = $state(20);
  let meshNr       = $state(20);
  let meshNzCyl    = $state(20);
  let meshScores   = $state<Set<NeutronScore>>(new Set(['flux', 'fission', 'heating-local']));

  // Group 4 — energy spectra (neutron-only — job-settings-model.md §5: not
  // meaningful for a photon leg, since group structures are neutron
  // multigroup library names)
  let spectraEnabled      = $state(false);
  let spectraGroups       = $state<'33' | '69' | '252'>('69');
  let spectraPerMaterial  = $state(true);

  // Group 5 — diagnostics
  let diagStochVol  = $state(false);
  let diagTracks    = $state(false);
  let diagNTracks   = $state(100);

  function toggleScore<S extends string>(set: Set<S>, score: S): Set<S> {
    const next = new Set(set);
    if (next.has(score)) { if (next.size > 1) next.delete(score); }
    else next.add(score);
    return next;
  }

  const SCORE_LABELS: Record<NeutronScore, string> = {
    'flux':          'Flux',
    'fission':       'Fission',
    'absorption':    'Absorption',
    'heating':       'Heating',
    'nu-fission':    'ν-Fission',
    'heating-local': 'Local heat',
    'scatter':       'Scatter',
  };

  const GROUP_STRUCTURE_OPTS = [
    { value: '33',  label: '33-group',  desc: 'CASMO — fast, LWR-appropriate' },
    { value: '69',  label: '69-group',  desc: 'WIMS — standard PWR analysis'  },
    { value: '252', label: '252-group', desc: 'Ultra-fine — high cost'         },
  ] as const;

  // ---------------------------------------------------------------------------
  // Results config state — PHOTON leg (r2s only).
  //
  // NEVER shares scalarsEnabled/meshEnabled/etc. above with the neutron
  // leg — this is the core structural fix from job-settings-model.md §1:
  // r2s must produce a per-leg R2SResultsConfig, not one global config.
  // Fission/nu-fission are not offered at all here (job-settings-model.md
  // §5 — there is no fission reaction for photons to score), and there is
  // no energy-spectra group here either (neutron-only).
  // ---------------------------------------------------------------------------
  const ALL_SCORES_PHOTON = ['flux', 'absorption', 'heating', 'heating-local', 'scatter'] as const;
  type PhotonScore = typeof ALL_SCORES_PHOTON[number];
  const PHOTON_SCORE_LABELS: Record<PhotonScore, string> = {
    'flux':          'Flux',
    'absorption':    'Absorption',
    'heating':       'Heating',
    'heating-local': 'Local heat',
    'scatter':       'Scatter',
  };

  let photonScalarsEnabled  = $state(false);
  let photonScalarsAllCells = $state(false);
  let photonScalarsScores   = $state<Set<PhotonScore>>(new Set(['flux']));

  let photonMeshEnabled  = $state(true);
  let photonMeshType     = $state<'regular' | 'cylindrical'>('regular');
  let photonMeshNx       = $state(20);
  let photonMeshNy       = $state(20);
  let photonMeshNz       = $state(20);
  let photonMeshNr       = $state(20);
  let photonMeshNzCyl    = $state(20);
  let photonMeshScores   = $state<Set<PhotonScore>>(new Set(['flux', 'heating']));

  // Only meaningful on a photon leg — dose-conversion-weighted flux
  // (job-settings-model.md §5's "dose conversion factors" row).
  let photonApplyDoseConversion = $state(true);

  // ---------------------------------------------------------------------------
  // Helpers
  // ---------------------------------------------------------------------------

  function backendTypeColor(t: string): string {
    switch (t) {
      case 'docker': return '#06b6d4';
      case 'local':  return '#22c55e';
      case 'slurm':  return '#a78bfa';
      default:       return '#94a3b8';
    }
  }

  function extractMaterialIds(text: string): string[] {
    const matches = text.match(
      /(?:^|\s)(?:material|pellet_material|gap_material|clad_material|fill_material):\s*([A-Za-z0-9_]+)/gm
    ) ?? [];
    const ids = new Set<string>();
    for (const m of matches) {
      const val = m.split(':')[1]?.trim();
      if (val && val !== 'null') ids.add(val);
    }
    return [...ids];
  }

  function parseNumberList(text: string): number[] {
    return text.split(',').map(s => parseFloat(s.trim())).filter(n => !isNaN(n));
  }

  let selectedProjectId = $state(projects.activeId);

    const selectedProject = $derived(
        projects.list.find(p => p.id === selectedProjectId) ?? projects.list[0]
    );

  async function submit() {
    submitError = null;
    submitting  = true;



    const project = selectedProject;
    if (!project.isValid) {
      submitError = 'Active geometry has validation errors. Fix them first.';
      submitting = false;
      return;
    }

    const materialIds = extractMaterialIds(project.text);
    if (materialIds.length === 0) {
      submitError = 'No material IDs found in geometry. Check your YAML.';
      submitting = false;
      return;
    }

    const profile = profiles.find(p => p.name === selectedProfile);
    if (!profile) {
      submitError = `Profile '${selectedProfile}' not found.`;
      submitting = false;
      return;
    }

    try {
      // Per-mode client-side checks — the backend enforces all of this too
      // (job-settings-model.md §6), but catching it here avoids a round trip
      // for the most common mistakes.
      if (runMode === 'fixed_source') {
        const expectedLen = fsSpaceType === 'point' ? 3 : 6;
        if (parseNumberList(fsSpaceParams).length !== expectedLen) {
          submitError = `Source ${fsSpaceType} needs ${expectedLen} space parameters.`;
          submitting = false;
          return;
        }
        if (fsParticle === 'photon' && fsEnergyMev == null) {
          submitError = 'A photon source has no default spectrum — set an energy (MeV).';
          submitting = false;
          return;
        }
      }
      if (runMode === 'depletion' && !depChainFile.trim()) {
        submitError = 'Depletion requires a depletion chain file reference.';
        submitting = false;
        return;
      }
      if (runMode === 'r2s') {
        const expectedLen = r2sNeutronSpaceType === 'point' ? 3 : 6;
        if (parseNumberList(r2sNeutronParams).length !== expectedLen) {
          submitError = `Neutron leg source (${r2sNeutronSpaceType}) needs ${expectedLen} space parameters.`;
          submitting = false;
          return;
        }
        if (!r2sDecayLibrary.trim()) {
          submitError = 'R2S requires a decay/activation library reference.';
          submitting = false;
          return;
        }
        if (parseNumberList(r2sCoolingTimes).length === 0) {
          submitError = 'R2S requires at least one cooling time.';
          submitting = false;
          return;
        }
      }

      const backend_config = { type: profile.backend_type, ...profile.config_data };

      const body: Record<string, unknown> = {
        geometry_text:  project.text,
        material_ids:   materialIds,
        notes:          notes || undefined,
        run_mode:       runMode,
        backend_config,
      };

      if (runMode === 'r2s') {
        body.r2s = {
          neutron_leg_source: {
            particle:     'neutron',
            space_type:   r2sNeutronSpaceType,
            space_params: parseNumberList(r2sNeutronParams),
          },
          neutron_leg_mc: {
            particles: r2sNeutronParticles,
            batches:   r2sNeutronBatches,
            seed:      r2sNeutronSeed,
            // no `inactive` — r2s legs are always fixed-source-shaped.
          },
          activation: {
            irradiation_schedule: {
              power_W:   r2sPowerW,
              timesteps: parseNumberList(r2sTimesteps),
            },
            cooling_times: parseNumberList(r2sCoolingTimes),
            decay_library: r2sDecayLibrary,
          },
          photon_leg_mc: {
            particles: r2sPhotonParticles,
            batches:   r2sPhotonBatches,
            seed:      r2sPhotonSeed,
          },
          photon_leg_vr: {
            weight_windows_enabled: r2sPhotonWeightWindows,
          },
        };

        body.r2s_results_config = {
          neutron_leg: {
            particle_type: 'neutron',
            scalars: { enabled: scalarsEnabled, scores: [...scalarsScores], all_cells: scalarsAllCells },
            mesh: {
              enabled: meshEnabled, mesh_type: meshType,
              nx: meshNx, ny: meshNy, nz: meshNz, nr: meshNr, nz_cyl: meshNzCyl,
              scores: [...meshScores],
            },
            spectra: { enabled: spectraEnabled, group_structure: spectraGroups, per_material: spectraPerMaterial },
            diagnostics: { stochastic_volumes: diagStochVol, particle_tracks: diagTracks, n_tracks: diagNTracks },
          },
          photon_leg: {
            particle_type: 'photon',
            scalars: { enabled: photonScalarsEnabled, scores: [...photonScalarsScores], all_cells: photonScalarsAllCells },
            mesh: {
              enabled: photonMeshEnabled, mesh_type: photonMeshType,
              nx: photonMeshNx, ny: photonMeshNy, nz: photonMeshNz,
              nr: photonMeshNr, nz_cyl: photonMeshNzCyl,
              scores: [...photonMeshScores],
            },
            // spectra intentionally omitted — not offered on a photon leg.
            apply_dose_conversion: photonApplyDoseConversion,
          },
        };

      } else {
        body.monte_carlo = {
          particles: mcParticles,
          batches:   mcBatches,
          seed:      mcSeed,
          ...(needsInactive ? { inactive: mcInactive } : {}),
        };

        if (runMode === 'fixed_source') {
          body.source = {
            particle:     fsParticle,
            space_type:   fsSpaceType,
            space_params: parseNumberList(fsSpaceParams),
            ...(fsEnergyMev != null ? { energy_mev: fsEnergyMev } : {}),
          };
        }

        if (runMode === 'depletion') {
          body.depletion = {
            power_W:    depPowerW,
            timesteps:  parseNumberList(depTimesteps),
            chain_file: depChainFile,
            integrator: depIntegrator,
            substeps:   depSubsteps,
          };
        }

        body.results_config = {
          particle_type: 'neutron',
          scalars: { enabled: scalarsEnabled, scores: [...scalarsScores], all_cells: scalarsAllCells },
          mesh: {
            enabled: meshEnabled, mesh_type: meshType,
            nx: meshNx, ny: meshNy, nz: meshNz, nr: meshNr, nz_cyl: meshNzCyl,
            scores: [...meshScores],
          },
          spectra: { enabled: spectraEnabled, group_structure: spectraGroups, per_material: spectraPerMaterial },
          diagnostics: { stochastic_volumes: diagStochVol, particle_tracks: diagTracks, n_tracks: diagNTracks },
        };
      }

      const result = await api.jobs.submit(body as Parameters<typeof api.jobs.submit>[0]);

      if ('sweep_id' in result) {
        jobsState.list.unshift(...result.jobs);
        jobsState.selectedJobId = result.jobs[0]?.id ?? null;
      } else {
        jobsState.list.unshift(result);
        jobsState.selectedJobId = result.id;
      }

      onClose();
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e);
      try {
        const parsed = JSON.parse(msg.replace(/^API \d+: /, ''));
        submitError = Array.isArray(parsed) ? parsed[0]?.msg ?? msg : parsed.detail ?? msg;
      } catch {
        submitError = msg;
      }
    } finally {
      submitting = false;
    }
  }

  function onBackdropClick(e: MouseEvent) {
    if (e.target === e.currentTarget) onClose();
  }

  function onKeydown(e: KeyboardEvent) {
    if (e.key === 'Escape') onClose();
  }
</script>

<svelte:window onkeydown={onKeydown} />

<!-- svelte-ignore a11y_click_events_have_key_events -->
<!-- svelte-ignore a11y_no_static_element_interactions -->
<div class="backdrop" onclick={onBackdropClick}>
  <div class="modal" role="dialog" aria-modal="true" aria-label="New simulation job">

    <!-- ── Modal header ── -->
    <div class="modal-header">
      <div class="modal-title-group">
        <svg viewBox="0 0 16 16" fill="currentColor" class="title-icon">
          <path d="M8 0a8 8 0 100 16A8 8 0 008 0zm-.75 4.75a.75.75 0 011.5 0v3.5h2.25a.75.75 0 010 1.5H8a.75.75 0 01-.75-.75v-4.25z"/>
        </svg>
        <span class="modal-title">New simulation job</span>
      </div>
      <button class="close-btn" onclick={onClose} aria-label="Close">
        <svg viewBox="0 0 16 16" fill="currentColor">
          <path d="M3.72 3.72a.75.75 0 011.06 0L8 6.94l3.22-3.22a.75.75 0 111.06 1.06L9.06 8l3.22 3.22a.75.75 0 11-1.06 1.06L8 9.06l-3.22 3.22a.75.75 0 01-1.06-1.06L6.94 8 3.72 4.78a.75.75 0 010-1.06z"/>
        </svg>
      </button>
    </div>

    <div class="modal-body">

        <section class="section">
            <div class="section-label">Geometry project</div>

            <label class="field wide">
                <select bind:value={selectedProjectId}>
                    {#each projects.list as project}
                        <option value={project.id}>
                            {project.name}
                            {#if project.isDirty} • Unsaved{/if}
                        </option>
                    {/each}
                </select>
            </label>
        </section>

      <!-- ── Run type ── -->
      <section class="section">
        <div class="section-label">Run type</div>
        <div class="run-mode-grid">
          {#each Object.entries(RUN_MODES) as [mode, cfg]}
            <button
              class="run-mode-btn"
              class:active={runMode === mode}
              onclick={() => runMode = mode as RunMode}
            >
              <span class="run-mode-label">{cfg.label}</span>
              <span class="run-mode-desc">{cfg.description}</span>
            </button>
          {/each}
        </div>
      </section>

      <!-- ── Monte Carlo settings — single leg (not shown for r2s) ── -->
      {#if runMode !== 'r2s'}
        <section class="section">
          <div class="section-label">Monte Carlo settings</div>
          <div class="fields-grid">
            <label class="field">
              <span>Particles / batch</span>
              <input type="number" min="1" bind:value={mcParticles} />
            </label>
            {#if needsInactive}
              <label class="field">
                <span>Inactive batches</span>
                <input type="number" min="1" bind:value={mcInactive} />
              </label>
            {/if}
            <label class="field">
              <span>Total batches</span>
              <input type="number" min="2" bind:value={mcBatches} />
            </label>
            <label class="field">
              <span>Random seed</span>
              <input type="number" min="1" bind:value={mcSeed} />
            </label>
          </div>
          {#if runMode === 'depletion'}
            <p class="note">Inactive/batches apply <strong>per timestep</strong> — cost scales with {parseNumberList(depTimesteps).length || '…'}× this.</p>
          {/if}
        </section>
      {/if}

      <!-- ── Source — required for fixed_source ── -->
      {#if runMode === 'fixed_source'}
        <section class="section">
          <div class="section-label">Source</div>
          <div class="fields-grid">
            <label class="field">
              <span>Particle</span>
              <select bind:value={fsParticle}>
                <option value="neutron">Neutron</option>
                <option value="photon">Photon</option>
              </select>
            </label>
            <label class="field">
              <span>Shape</span>
              <select bind:value={fsSpaceType}>
                <option value="point">Point</option>
                <option value="box">Box</option>
              </select>
            </label>
            <label class="field wide">
              <span>{fsSpaceType === 'point' ? 'Position (x, y, z)' : 'Bounds (xmin, ymin, zmin, xmax, ymax, zmax)'}</span>
              <input type="text" bind:value={fsSpaceParams} placeholder={fsSpaceType === 'point' ? '0, 0, 0' : '-5, -5, -5, 5, 5, 5'} />
            </label>
            <label class="field">
              <span>Energy (MeV) {#if fsParticle === 'photon'}<span class="optional">required</span>{:else}<span class="optional">optional</span>{/if}</span>
              <input type="number" step="any" min="0" bind:value={fsEnergyMev} placeholder={fsParticle === 'neutron' ? 'default: Watt spectrum' : 'required'} />
            </label>
          </div>
          {#if fsParticle === 'photon' && fsEnergyMev == null}
            <p class="note warning">⚠ A photon source has no default spectrum — energy is required.</p>
          {/if}
        </section>
      {/if}

      <!-- ── Depletion-specific ── -->
      {#if runMode === 'depletion'}
        <section class="section">
          <div class="section-label">Depletion settings</div>
          <div class="fields-grid">
            <label class="field">
              <span>Power (W)</span>
              <input type="number" min="0" step="any" bind:value={depPowerW} />
            </label>
            <label class="field wide">
              <span>Timesteps (days, comma-separated)</span>
              <input type="text" placeholder="10, 30, 60, 90" bind:value={depTimesteps} />
            </label>
            <label class="field wide">
              <span>Depletion chain file</span>
              <input type="text" placeholder="chain_endfb71_pwr.xml" bind:value={depChainFile} />
            </label>
            <label class="field">
              <span>Integrator</span>
              <select bind:value={depIntegrator}>
                <option value="predictor">Predictor</option>
                <option value="cecm">CE/CM</option>
                <option value="celi">CE/LI</option>
              </select>
            </label>
            <label class="field">
              <span>Substeps</span>
              <input type="number" min="1" bind:value={depSubsteps} />
            </label>
          </div>
          {#if !depChainFile.trim()}
            <p class="note warning">⚠ A depletion chain file is required to run.</p>
          {/if}
        </section>
      {/if}

      <!-- ── R2S — neutron leg ── -->
      {#if runMode === 'r2s'}
        <section class="section">
          <div class="section-label">Neutron leg</div>
          <div class="fields-grid">
            <label class="field">
              <span>Shape</span>
              <select bind:value={r2sNeutronSpaceType}>
                <option value="point">Point</option>
                <option value="box">Box</option>
              </select>
            </label>
            <label class="field wide">
              <span>{r2sNeutronSpaceType === 'point' ? 'Position (x, y, z)' : 'Bounds (xmin, ymin, zmin, xmax, ymax, zmax)'}</span>
              <input type="text" bind:value={r2sNeutronParams} />
            </label>
            <label class="field">
              <span>Particles / batch</span>
              <input type="number" min="1" bind:value={r2sNeutronParticles} />
            </label>
            <label class="field">
              <span>Total batches</span>
              <input type="number" min="2" bind:value={r2sNeutronBatches} />
            </label>
            <label class="field">
              <span>Random seed</span>
              <input type="number" min="1" bind:value={r2sNeutronSeed} />
            </label>
          </div>
          <p class="note">No inactive batches — this is a fixed-source leg, not an eigenvalue solve.</p>
        </section>

        <!-- ── R2S — activation (NOT a transport run) ── -->
        <section class="section">
          <div class="section-label">Activation</div>
          <p class="note" style="margin-bottom:6px">Consumes the neutron leg's reaction-rate mesh; produces the photon leg's source. Not an OpenMC transport calculation.</p>
          <div class="fields-grid">
            <label class="field">
              <span>Power (W)</span>
              <input type="number" min="0" step="any" bind:value={r2sPowerW} />
            </label>
            <label class="field wide">
              <span>Irradiation timesteps (days, comma-separated)</span>
              <input type="text" placeholder="30" bind:value={r2sTimesteps} />
            </label>
            <label class="field wide">
              <span>Cooling times (seconds, comma-separated) — one dose result per value</span>
              <input type="text" placeholder="0, 3600, 86400" bind:value={r2sCoolingTimes} />
            </label>
            <label class="field wide">
              <span>Decay/activation library</span>
              <input type="text" placeholder="endf_decay" bind:value={r2sDecayLibrary} />
            </label>
          </div>
          {#if !r2sDecayLibrary.trim()}
            <p class="note warning">⚠ A decay library reference is required to run.</p>
          {/if}
        </section>

        <!-- ── R2S — photon leg ── -->
        <section class="section">
          <div class="section-label">Photon leg</div>
          <p class="note" style="margin-bottom:6px">Source is derived from the activation step — not user-entered.</p>
          <div class="fields-grid">
            <label class="field">
              <span>Particles / batch</span>
              <input type="number" min="1" bind:value={r2sPhotonParticles} />
            </label>
            <label class="field">
              <span>Total batches</span>
              <input type="number" min="2" bind:value={r2sPhotonBatches} />
            </label>
            <label class="field">
              <span>Random seed</span>
              <input type="number" min="1" bind:value={r2sPhotonSeed} />
            </label>
          </div>
          <label class="rc-toggle-label" style="margin-top:8px">
            <input type="checkbox" bind:checked={r2sPhotonWeightWindows} class="rc-checkbox" />
            <span class="rc-field-label" style="color: var(--color-text)">Variance reduction (weight windows)</span>
          </label>
          <p class="note">Strongly recommended — shielding/dose problems are usually unusable without it.</p>
        </section>
      {/if}

      <!-- ── Backend profile ── -->
      <section class="section">
        <div class="section-label-row">
          <span class="section-label">Execution backend</span>
          <button class="manage-link" onclick={() => { onClose(); onManageBackends(); }}>
            Manage backends →
          </button>
        </div>

        {#if profiles.length === 0}
          <p class="note">
            No profiles yet.
            <button class="inline-link" onclick={() => { onClose(); onManageBackends(); }}>
              Create one →
            </button>
          </p>
        {:else}
          <div class="profile-picker">
            {#each profiles as profile (profile.name)}
              <button
                class="profile-pill"
                class:active={selectedProfile === profile.name}
                onclick={() => selectedProfile = profile.name}
              >
                <span class="pill-dot" style="background: {backendTypeColor(profile.backend_type)}"></span>
                <span class="pill-name">{profile.name}</span>
                <span class="pill-type">{profile.backend_type}</span>
              </button>
            {/each}
          </div>
        {/if}
      </section>

      <!-- ── Notes ── -->
      <section class="section">
        <label class="field wide">
          <span class="section-label" style="margin-bottom:0">Notes <span class="optional">(optional)</span></span>
          <input type="text" placeholder="e.g. baseline run, pin cell sweep…" bind:value={notes} />
        </label>
      </section>

      <!-- ── Results capture ── -->
      <section class="section">
        <div class="section-label">Results capture{#if runMode === 'r2s'} — neutron leg{/if}</div>

        <!-- Always-on summary note -->
        <p class="note" style="margin-bottom:2px">
          ✓ Simulation summary always captured{#if runMode === 'r2s'} (neutron leg){/if} — {#if runMode === 'eigenvalue'}k-eff, entropy, batch history, timing.{:else}entropy, batch history, timing.{/if}
        </p>

        <!-- Group 2: Scalar tallies -->
        <div class="rc-group" class:rc-group-off={!scalarsEnabled}>
          <div class="rc-group-header">
            <label class="rc-toggle-label">
              <input type="checkbox" bind:checked={scalarsEnabled} class="rc-checkbox" />
              <span class="rc-group-title">Scalar cell tallies</span>
            </label>
            <span class="rc-cost">tiny cost</span>
          </div>
          {#if scalarsEnabled}
            <div class="rc-group-body">
              <div class="rc-row">
                <span class="rc-field-label">Scores</span>
                <div class="rc-pill-row">
                  {#each ALL_SCORES_NEUTRON as score}
                    <button
                      class="rc-pill"
                      class:rc-pill-on={scalarsScores.has(score)}
                      onclick={() => scalarsScores = toggleScore(scalarsScores, score)}
                      title={scalarsScores.has(score) && scalarsScores.size === 1 ? 'At least one score required' : ''}
                    >{SCORE_LABELS[score]}</button>
                  {/each}
                </div>
              </div>
              <div class="rc-row">
                <span class="rc-field-label">Cell filter</span>
                <div class="rc-pill-row">
                  <button class="rc-pill" class:rc-pill-on={!scalarsAllCells} onclick={() => scalarsAllCells = false}>
                    Fissile only
                  </button>
                  <button class="rc-pill" class:rc-pill-on={scalarsAllCells} onclick={() => scalarsAllCells = true}>
                    All cells
                  </button>
                </div>
              </div>
            </div>
          {/if}
        </div>

        <!-- Group 3: Mesh tally -->
        <div class="rc-group" class:rc-group-off={!meshEnabled}>
          <div class="rc-group-header">
            <label class="rc-toggle-label">
              <input type="checkbox" bind:checked={meshEnabled} class="rc-checkbox" />
              <span class="rc-group-title">3-D mesh tally</span>
            </label>
            <span class="rc-cost">cost ∝ nx·ny·nz</span>
          </div>
          {#if meshEnabled}
            <div class="rc-group-body">
              <div class="rc-row">
                <span class="rc-field-label">Type</span>
                <div class="rc-pill-row">
                  <button class="rc-pill" class:rc-pill-on={meshType === 'regular'}     onclick={() => meshType = 'regular'}>Cartesian</button>
                  <button class="rc-pill" class:rc-pill-on={meshType === 'cylindrical'} onclick={() => meshType = 'cylindrical'}>Cylindrical</button>
                </div>
              </div>

              {#if meshType === 'regular'}
                <div class="rc-row">
                  <span class="rc-field-label">Voxels</span>
                  <div class="rc-dim-row">
                    <label class="rc-dim-field">
                      <span>nx</span>
                      <input type="number" min="1" max="500" bind:value={meshNx} />
                    </label>
                    <label class="rc-dim-field">
                      <span>ny</span>
                      <input type="number" min="1" max="500" bind:value={meshNy} />
                    </label>
                    <label class="rc-dim-field">
                      <span>nz</span>
                      <input type="number" min="1" max="500" bind:value={meshNz} />
                    </label>
                  </div>
                  <span class="rc-dim-note">{(meshNx * meshNy * meshNz).toLocaleString()} voxels</span>
                </div>
              {:else}
                <div class="rc-row">
                  <span class="rc-field-label">Bins</span>
                  <div class="rc-dim-row">
                    <label class="rc-dim-field">
                      <span>nr</span>
                      <input type="number" min="1" max="500" bind:value={meshNr} />
                    </label>
                    <label class="rc-dim-field">
                      <span>nz</span>
                      <input type="number" min="1" max="500" bind:value={meshNzCyl} />
                    </label>
                  </div>
                  <span class="rc-dim-note">{(meshNr * meshNzCyl).toLocaleString()} bins</span>
                </div>
              {/if}

              <div class="rc-row">
                <span class="rc-field-label">Scores</span>
                <div class="rc-pill-row">
                  {#each ALL_SCORES_NEUTRON as score}
                    <button
                      class="rc-pill"
                      class:rc-pill-on={meshScores.has(score)}
                      onclick={() => meshScores = toggleScore(meshScores, score)}
                    >{SCORE_LABELS[score]}</button>
                  {/each}
                </div>
              </div>

              {#if meshNx * meshNy * meshNz > 100_000 || meshNr * meshNzCyl > 10_000}
                <p class="note warning">⚠ Large mesh — statepoint file may exceed 200 MB.</p>
              {/if}
            </div>
          {/if}
        </div>

        <!-- Group 4: Energy spectra (neutron-only) -->
        <div class="rc-group" class:rc-group-off={!spectraEnabled}>
          <div class="rc-group-header">
            <label class="rc-toggle-label">
              <input type="checkbox" bind:checked={spectraEnabled} class="rc-checkbox" />
              <span class="rc-group-title">Energy spectra</span>
            </label>
            <span class="rc-cost">flux vs energy</span>
          </div>
          {#if spectraEnabled}
            <div class="rc-group-body">
              <div class="rc-row">
                <span class="rc-field-label">Group structure</span>
                <div class="rc-structure-grid">
                  {#each GROUP_STRUCTURE_OPTS as opt}
                    <button
                      class="rc-structure-btn"
                      class:rc-structure-on={spectraGroups === opt.value}
                      onclick={() => spectraGroups = opt.value}
                    >
                      <span class="rc-structure-label">{opt.label}</span>
                      <span class="rc-structure-desc">{opt.desc}</span>
                    </button>
                  {/each}
                </div>
              </div>
              <div class="rc-row">
                <span class="rc-field-label">Scope</span>
                <div class="rc-pill-row">
                  <button class="rc-pill" class:rc-pill-on={spectraPerMaterial}  onclick={() => spectraPerMaterial = true}>Per material</button>
                  <button class="rc-pill" class:rc-pill-on={!spectraPerMaterial} onclick={() => spectraPerMaterial = false}>Global</button>
                </div>
              </div>
            </div>
          {/if}
        </div>

        <!-- Group 5: Diagnostics -->
        <div class="rc-group" class:rc-group-off={!diagStochVol && !diagTracks}>
          <div class="rc-group-header">
            <span class="rc-group-title" style="opacity:0.7">Diagnostics</span>
            <span class="rc-cost">optional</span>
          </div>
          <div class="rc-group-body" style="padding-top:0">
            <div class="rc-row" style="flex-wrap:wrap; gap:6px">
              <label class="rc-toggle-label" style="flex:none">
                <input type="checkbox" bind:checked={diagStochVol} class="rc-checkbox" />
                <span class="rc-field-label" style="color: var(--color-text)">Stochastic volumes</span>
              </label>
              <label class="rc-toggle-label" style="flex:none">
                <input type="checkbox" bind:checked={diagTracks} class="rc-checkbox" />
                <span class="rc-field-label" style="color: var(--color-text)">Particle tracks</span>
              </label>
            </div>
            {#if diagTracks}
              <div class="rc-row">
                <label class="field" style="max-width:140px">
                  <span>Track count</span>
                  <input type="number" min="1" max="10000" bind:value={diagNTracks} />
                </label>
                <p class="note warning" style="align-self:flex-end; margin:0">⚠ Large files at high counts.</p>
              </div>
            {/if}
          </div>
        </div>

      </section>

      <!-- ── Results capture — PHOTON leg (r2s only) ──
           Never merged with the neutron-leg block above: different valid
           scores (no fission/nu-fission), no energy spectra, plus a
           dose-conversion option that only makes sense here
           (job-settings-model.md §1, §5). -->
      {#if runMode === 'r2s'}
        <section class="section">
          <div class="section-label">Results capture — photon leg</div>

          <div class="rc-group" class:rc-group-off={!photonScalarsEnabled}>
            <div class="rc-group-header">
              <label class="rc-toggle-label">
                <input type="checkbox" bind:checked={photonScalarsEnabled} class="rc-checkbox" />
                <span class="rc-group-title">Scalar cell tallies</span>
              </label>
              <span class="rc-cost">tiny cost</span>
            </div>
            {#if photonScalarsEnabled}
              <div class="rc-group-body">
                <div class="rc-row">
                  <span class="rc-field-label">Scores</span>
                  <div class="rc-pill-row">
                    {#each ALL_SCORES_PHOTON as score}
                      <button
                        class="rc-pill"
                        class:rc-pill-on={photonScalarsScores.has(score)}
                        onclick={() => photonScalarsScores = toggleScore(photonScalarsScores, score)}
                        title={photonScalarsScores.has(score) && photonScalarsScores.size === 1 ? 'At least one score required' : ''}
                      >{PHOTON_SCORE_LABELS[score]}</button>
                    {/each}
                  </div>
                </div>
                <p class="note">No fission/ν-fission — not meaningful for a photon leg.</p>
                <div class="rc-row">
                  <span class="rc-field-label">Cell filter</span>
                  <div class="rc-pill-row">
                    <button class="rc-pill" class:rc-pill-on={!photonScalarsAllCells} onclick={() => photonScalarsAllCells = false}>
                      Fissile only
                    </button>
                    <button class="rc-pill" class:rc-pill-on={photonScalarsAllCells} onclick={() => photonScalarsAllCells = true}>
                      All cells
                    </button>
                  </div>
                </div>
              </div>
            {/if}
          </div>

          <div class="rc-group" class:rc-group-off={!photonMeshEnabled}>
            <div class="rc-group-header">
              <label class="rc-toggle-label">
                <input type="checkbox" bind:checked={photonMeshEnabled} class="rc-checkbox" />
                <span class="rc-group-title">3-D dose mesh</span>
              </label>
              <span class="rc-cost">cost ∝ nx·ny·nz</span>
            </div>
            {#if photonMeshEnabled}
              <div class="rc-group-body">
                <div class="rc-row">
                  <span class="rc-field-label">Type</span>
                  <div class="rc-pill-row">
                    <button class="rc-pill" class:rc-pill-on={photonMeshType === 'regular'}     onclick={() => photonMeshType = 'regular'}>Cartesian</button>
                    <button class="rc-pill" class:rc-pill-on={photonMeshType === 'cylindrical'} onclick={() => photonMeshType = 'cylindrical'}>Cylindrical</button>
                  </div>
                </div>

                {#if photonMeshType === 'regular'}
                  <div class="rc-row">
                    <span class="rc-field-label">Voxels</span>
                    <div class="rc-dim-row">
                      <label class="rc-dim-field"><span>nx</span><input type="number" min="1" max="500" bind:value={photonMeshNx} /></label>
                      <label class="rc-dim-field"><span>ny</span><input type="number" min="1" max="500" bind:value={photonMeshNy} /></label>
                      <label class="rc-dim-field"><span>nz</span><input type="number" min="1" max="500" bind:value={photonMeshNz} /></label>
                    </div>
                    <span class="rc-dim-note">{(photonMeshNx * photonMeshNy * photonMeshNz).toLocaleString()} voxels</span>
                  </div>
                {:else}
                  <div class="rc-row">
                    <span class="rc-field-label">Bins</span>
                    <div class="rc-dim-row">
                      <label class="rc-dim-field"><span>nr</span><input type="number" min="1" max="500" bind:value={photonMeshNr} /></label>
                      <label class="rc-dim-field"><span>nz</span><input type="number" min="1" max="500" bind:value={photonMeshNzCyl} /></label>
                    </div>
                    <span class="rc-dim-note">{(photonMeshNr * photonMeshNzCyl).toLocaleString()} bins</span>
                  </div>
                {/if}

                <div class="rc-row">
                  <span class="rc-field-label">Scores</span>
                  <div class="rc-pill-row">
                    {#each ALL_SCORES_PHOTON as score}
                      <button
                        class="rc-pill"
                        class:rc-pill-on={photonMeshScores.has(score)}
                        onclick={() => photonMeshScores = toggleScore(photonMeshScores, score)}
                      >{PHOTON_SCORE_LABELS[score]}</button>
                    {/each}
                  </div>
                </div>

                <!-- job-settings-model.md §6.2: photon dose meshes are
                     commonly LARGER than the neutron flux mesh — the same
                     size warning must apply here independently. -->
                {#if photonMeshNx * photonMeshNy * photonMeshNz > 100_000 || photonMeshNr * photonMeshNzCyl > 10_000}
                  <p class="note warning">⚠ Large mesh — statepoint file may exceed 200 MB.</p>
                {/if}
              </div>
            {/if}
          </div>

          <div class="rc-group">
            <div class="rc-group-header">
              <label class="rc-toggle-label">
                <input type="checkbox" bind:checked={photonApplyDoseConversion} class="rc-checkbox" />
                <span class="rc-group-title">Dose conversion</span>
              </label>
              <span class="rc-cost">shutdown dose</span>
            </div>
            {#if photonApplyDoseConversion && !photonScalarsScores.has('flux') && !photonMeshScores.has('flux')}
              <p class="note warning" style="margin:6px 0 0">⚠ Dose conversion wraps a flux score — enable `flux` in scalars or mesh above.</p>
            {/if}
          </div>
        </section>
      {/if}

    </div>

    <!-- ── Footer ── -->
    <div class="modal-footer">
      {#if submitError}
        <p class="submit-error">{submitError}</p>
      {/if}
      <div class="footer-actions">
        <button class="cancel-btn" onclick={onClose}>Cancel</button>
        <button
          class="run-btn"
          disabled={submitting || profiles.length === 0}
          onclick={submit}
        >
          {#if submitting}
            <span class="spinner"></span> Submitting…
          {:else}
            <svg viewBox="0 0 16 16" fill="currentColor" style="width:12px;height:12px;">
              <path d="M3 2.75C3 1.784 3.784 1 4.75 1h6.5c.966 0 1.75.784 1.75 1.75v11.5a.75.75 0 01-1.218.586L8 12.485l-3.782 2.35A.75.75 0 013 14.25V2.75z"/>
            </svg>
            Run · {RUN_MODES[runMode].label}
          {/if}
        </button>
      </div>
    </div>

  </div>
</div>

<style>
  .backdrop {
    position: fixed;
    inset: 0;
    background: rgba(0, 0, 0, 0.55);
    backdrop-filter: blur(2px);
    display: flex;
    align-items: center;
    justify-content: center;
    z-index: 200;
    padding: 20px;
  }

  .modal {
    background: var(--color-bg-panel);
    border: 1px solid var(--color-border);
    border-radius: 10px;
    width: 560px;
    max-width: 100%;
    max-height: calc(100vh - 40px);
    display: flex;
    flex-direction: column;
    overflow: hidden;
    box-shadow: 0 24px 64px rgba(0,0,0,0.5);
  }

  /* ── Header ── */
  .modal-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 14px 16px 12px;
    border-bottom: 1px solid var(--color-border);
    flex-shrink: 0;
  }

  .modal-title-group {
    display: flex;
    align-items: center;
    gap: 8px;
  }

  .title-icon {
    width: 14px;
    height: 14px;
    color: var(--color-accent-hi);
    flex-shrink: 0;
  }

  .modal-title {
    font-size: 13px;
    font-weight: 600;
    color: var(--color-text);
  }

  .close-btn {
    background: transparent;
    border: none;
    color: var(--color-subtext);
    cursor: pointer;
    padding: 3px;
    display: flex;
    align-items: center;
    border-radius: 4px;
    transition: color 0.1s;
  }
  .close-btn svg { width: 14px; height: 14px; }
  .close-btn:hover { color: var(--color-text); }

  /* ── Body ── */
  .modal-body {
    flex: 1;
    overflow-y: auto;
    padding: 6px 0;
    display: flex;
    flex-direction: column;
  }

  .section {
    padding: 12px 16px;
    border-bottom: 1px solid rgba(51,65,85,0.5);
    display: flex;
    flex-direction: column;
    gap: 8px;
  }
  .section:last-child { border-bottom: none; }

  .section-label {
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 0.07em;
    font-weight: 600;
    color: var(--color-subtext);
  }

  .section-label-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
  }

  .manage-link {
    background: transparent;
    border: none;
    font-size: 10px;
    color: var(--color-accent-hi);
    cursor: pointer;
    padding: 0;
    opacity: 0.8;
    transition: opacity 0.1s;
  }
  .manage-link:hover { opacity: 1; }

  /* Run mode */
  .run-mode-grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 6px;
  }

  .run-mode-btn {
    display: flex;
    flex-direction: column;
    gap: 3px;
    padding: 9px 11px;
    border-radius: 6px;
    background: var(--color-bg-raised);
    border: 1px solid var(--color-border);
    cursor: pointer;
    text-align: left;
    transition: border-color 0.1s, background 0.1s;
  }
  .run-mode-btn:hover { background: #334155; }
  .run-mode-btn.active {
    border-color: var(--color-accent);
    background: rgba(6,182,212,0.08);
  }

  .run-mode-label {
    font-size: 11px;
    font-weight: 600;
    color: var(--color-text);
    font-family: var(--font-mono);
  }
  .run-mode-btn.active .run-mode-label { color: var(--color-accent-hi); }

  .run-mode-desc {
    font-size: 9px;
    color: var(--color-subtext);
    line-height: 1.4;
  }

  /* Fields */
  .fields-grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 8px;
  }

  .field {
    display: flex;
    flex-direction: column;
    gap: 4px;
    font-size: 10px;
    color: var(--color-subtext);
  }

  .field.wide { grid-column: 1 / -1; }

  .field input {
    background: var(--color-bg-raised);
    border: 1px solid var(--color-border);
    border-radius: 5px;
    color: var(--color-text);
    font-family: var(--font-mono);
    font-size: 12px;
    padding: 6px 8px;
    transition: border-color 0.1s;
  }
  .field input:focus { outline: none; border-color: var(--color-accent); }

  .note {
    font-size: 10px;
    color: var(--color-subtext);
    line-height: 1.4;
    margin: 0;
  }
  .note.warning { color: #f59e0b; }

  .optional {
    font-size: 9px;
    opacity: 0.6;
    text-transform: none;
    font-weight: 400;
    letter-spacing: 0;
    margin-left: 3px;
  }

  /* Profile picker */
  .profile-picker {
    display: flex;
    flex-wrap: wrap;
    gap: 5px;
  }

  .profile-pill {
    display: flex;
    align-items: center;
    gap: 5px;
    padding: 5px 10px;
    border-radius: 5px;
    border: 1px solid var(--color-border);
    background: var(--color-bg-raised);
    color: var(--color-subtext);
    font-family: var(--font-mono);
    font-size: 11px;
    cursor: pointer;
    transition: border-color 0.1s, color 0.1s;
  }
  .profile-pill:hover { color: var(--color-text); border-color: #475569; }
  .profile-pill.active {
    border-color: var(--color-accent);
    color: var(--color-accent-hi);
    background: rgba(6,182,212,0.08);
  }

  .pill-dot {
    width: 6px;
    height: 6px;
    border-radius: 50%;
    flex-shrink: 0;
    opacity: 0.85;
  }

  .pill-name { font-weight: 600; }

  .pill-type {
    font-size: 9px;
    color: var(--color-subtext);
  }
  .profile-pill.active .pill-type { color: rgba(6,182,212,0.55); }

  .inline-link {
    background: transparent;
    border: none;
    color: var(--color-accent-hi);
    font-size: 10px;
    cursor: pointer;
    padding: 0;
    text-decoration: underline;
  }

  /* ── Footer ── */
  .modal-footer {
    padding: 12px 16px;
    border-top: 1px solid var(--color-border);
    flex-shrink: 0;
    display: flex;
    flex-direction: column;
    gap: 8px;
  }

  .submit-error {
    font-size: 11px;
    color: #f87171;
    line-height: 1.4;
  }

  .footer-actions {
    display: flex;
    justify-content: flex-end;
    gap: 8px;
  }

  .cancel-btn {
    font-size: 12px;
    padding: 7px 16px;
    border-radius: 6px;
    border: 1px solid var(--color-border);
    background: transparent;
    color: var(--color-subtext);
    cursor: pointer;
    transition: color 0.1s;
  }
  .cancel-btn:hover { color: var(--color-text); }

  .run-btn {
    display: flex;
    align-items: center;
    gap: 6px;
    font-size: 12px;
    font-weight: 700;
    padding: 7px 20px;
    border-radius: 6px;
    border: none;
    background: var(--color-accent);
    color: var(--color-bg-deep);
    cursor: pointer;
    letter-spacing: 0.02em;
    transition: background 0.1s;
  }
  .run-btn:disabled { opacity: 0.45; cursor: default; }
  .run-btn:hover:not(:disabled) { background: var(--color-accent-hi); }

  .spinner {
    width: 11px;
    height: 11px;
    border: 2px solid rgba(15,23,42,0.3);
    border-top-color: var(--color-bg-deep);
    border-radius: 50%;
    animation: spin 0.7s linear infinite;
  }
  @keyframes spin { to { transform: rotate(360deg); } }

  .field select {
    background: var(--color-bg-raised);
    border: 1px solid var(--color-border);
    border-radius: 5px;
    color: var(--color-text);
    font-family: var(--font-mono);
    font-size: 12px;
    padding: 6px 8px;
    transition: border-color 0.1s;
}

  /* ── Results capture ── */

  .rc-group {
    border: 1px solid var(--color-border);
    border-radius: 7px;
    overflow: hidden;
    transition: border-color 0.15s;
  }
  .rc-group:not(.rc-group-off) { border-color: rgba(6,182,212,0.3); }

  .rc-group-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 8px 10px;
    background: var(--color-bg-raised);
  }

  .rc-toggle-label {
    display: flex;
    align-items: center;
    gap: 7px;
    cursor: pointer;
  }

  .rc-checkbox {
    appearance: none;
    width: 13px;
    height: 13px;
    border: 1px solid var(--color-border);
    border-radius: 3px;
    background: var(--color-bg-deep);
    cursor: pointer;
    flex-shrink: 0;
    position: relative;
    transition: background 0.1s, border-color 0.1s;
  }
  .rc-checkbox:checked {
    background: var(--color-accent);
    border-color: var(--color-accent);
  }
  .rc-checkbox:checked::after {
    content: '';
    position: absolute;
    left: 2px; top: 0px;
    width: 5px; height: 8px;
    border: 2px solid var(--color-bg-deep);
    border-top: none;
    border-left: none;
    transform: rotate(40deg);
  }

  .rc-group-title {
    font-size: 11px;
    font-weight: 600;
    color: var(--color-text);
    font-family: var(--font-mono);
  }

  .rc-cost {
    font-size: 9px;
    color: var(--color-subtext);
    font-family: var(--font-mono);
    opacity: 0.7;
  }

  .rc-group-body {
    padding: 8px 10px;
    display: flex;
    flex-direction: column;
    gap: 8px;
    border-top: 1px solid var(--color-border);
    background: rgba(15,23,42,0.35);
  }

  .rc-row {
    display: flex;
    align-items: flex-start;
    gap: 8px;
  }

  .rc-field-label {
    font-size: 9px;
    color: var(--color-subtext);
    text-transform: uppercase;
    letter-spacing: 0.06em;
    font-weight: 600;
    min-width: 64px;
    padding-top: 4px;
    flex-shrink: 0;
  }

  .rc-pill-row {
    display: flex;
    flex-wrap: wrap;
    gap: 4px;
  }

  .rc-pill {
    font-size: 10px;
    font-family: var(--font-mono);
    padding: 3px 8px;
    border-radius: 4px;
    border: 1px solid var(--color-border);
    background: var(--color-bg-raised);
    color: var(--color-subtext);
    cursor: pointer;
    transition: border-color 0.1s, color 0.1s, background 0.1s;
    line-height: 1.4;
  }
  .rc-pill:hover { color: var(--color-text); border-color: #475569; }
  .rc-pill.rc-pill-on {
    border-color: var(--color-accent);
    color: var(--color-accent-hi);
    background: rgba(6,182,212,0.1);
  }

  /* Dim structure: when group is off, the whole card fades */
  .rc-group-off { opacity: 0.55; }
  .rc-group-off .rc-group-header { background: transparent; }

  /* Dimension inputs (nx/ny/nz) */
  .rc-dim-row {
    display: flex;
    gap: 6px;
  }

  .rc-dim-field {
    display: flex;
    flex-direction: column;
    gap: 3px;
    font-size: 9px;
    color: var(--color-subtext);
    font-family: var(--font-mono);
  }

  .rc-dim-field input {
    width: 58px;
    background: var(--color-bg-raised);
    border: 1px solid var(--color-border);
    border-radius: 4px;
    color: var(--color-text);
    font-family: var(--font-mono);
    font-size: 12px;
    padding: 4px 6px;
    transition: border-color 0.1s;
  }
  .rc-dim-field input:focus { outline: none; border-color: var(--color-accent); }

  .rc-dim-note {
    font-size: 9px;
    color: var(--color-subtext);
    font-family: var(--font-mono);
    padding-top: 18px;   /* align with input value, below the label */
    opacity: 0.6;
  }

  /* Group structure selector (for spectra) */
  .rc-structure-grid {
    display: flex;
    flex-direction: column;
    gap: 4px;
    flex: 1;
  }

  .rc-structure-btn {
    display: flex;
    align-items: baseline;
    gap: 8px;
    padding: 6px 9px;
    border-radius: 5px;
    border: 1px solid var(--color-border);
    background: var(--color-bg-raised);
    cursor: pointer;
    text-align: left;
    transition: border-color 0.1s, background 0.1s;
  }
  .rc-structure-btn:hover { background: #334155; }
  .rc-structure-btn.rc-structure-on {
    border-color: var(--color-accent);
    background: rgba(6,182,212,0.08);
  }

  .rc-structure-label {
    font-size: 11px;
    font-weight: 600;
    color: var(--color-text);
    font-family: var(--font-mono);
    min-width: 62px;
  }
  .rc-structure-btn.rc-structure-on .rc-structure-label { color: var(--color-accent-hi); }

  .rc-structure-desc {
    font-size: 9px;
    color: var(--color-subtext);
  }
</style>