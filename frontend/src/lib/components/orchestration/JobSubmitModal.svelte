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

  const RUN_MODES: Record<RunMode, { label: string; description: string; needsInactive: boolean }> = {
    eigenvalue:   { label: 'Eigenvalue (k-eff)', description: 'Criticality calculation. Returns k-effective.',           needsInactive: true  },
    fixed_source: { label: 'Fixed Source',        description: 'Fixed neutron source transport. No k-eff.',              needsInactive: false },
    depletion:    { label: 'Depletion / Burnup',  description: 'Time-dependent burnup using a depletion chain.',         needsInactive: true  },
    r2s:          { label: 'R2S (activation)',     description: 'Rigorous Two-Step activation analysis.',                needsInactive: false },
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
  let particles       = $state(1000);
  let inactive        = $state(20);
  let batches         = $state(100);
  let seed            = $state(1);
  let notes           = $state('');
  let power_W         = $state(1000.0);
  let timesteps       = $state('10, 20, 30');
  let neutronSrcFile  = $state('');
  let selectedProfile = $state<string>(profiles[0]?.name ?? 'default');

  let submitting  = $state(false);
  let submitError = $state<string | null>(null);

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
      const body: Record<string, unknown> = {
        geometry_text:  project.text,
        material_ids:   materialIds,
        particles,
        batches,
        seed,
        notes:          notes || undefined,
        run_mode:       runMode,
        backend_config: { type: profile.backend_type, ...profile.config_data },
      };

      if (RUN_MODES[runMode].needsInactive) body.inactive = inactive;

      if (runMode === 'depletion') {
        body.power_W   = power_W;
        body.timesteps = timesteps.split(',').map(s => parseFloat(s.trim())).filter(n => !isNaN(n));
      }

      if (runMode === 'r2s') {
        body.neutron_source_file = neutronSrcFile;
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

      <!-- ── Monte Carlo settings ── -->
      <section class="section">
        <div class="section-label">Monte Carlo settings</div>
        <div class="fields-grid">
          <label class="field">
            <span>Particles / batch</span>
            <input type="number" min="1" bind:value={particles} />
          </label>
          {#if RUN_MODES[runMode].needsInactive}
            <label class="field">
              <span>Inactive batches</span>
              <input type="number" min="1" bind:value={inactive} />
            </label>
          {/if}
          <label class="field">
            <span>Total batches</span>
            <input type="number" min="2" bind:value={batches} />
          </label>
          <label class="field">
            <span>Random seed</span>
            <input type="number" min="1" bind:value={seed} />
          </label>
        </div>
      </section>

      <!-- ── Depletion-specific ── -->
      {#if runMode === 'depletion'}
        <section class="section">
          <div class="section-label">Depletion settings</div>
          <div class="fields-grid">
            <label class="field">
              <span>Power (W)</span>
              <input type="number" min="0" step="any" bind:value={power_W} />
            </label>
            <label class="field wide">
              <span>Timesteps (days, comma-separated)</span>
              <input type="text" placeholder="10, 30, 60, 90" bind:value={timesteps} />
            </label>
          </div>
          <p class="note warning">⚠ Depletion requires a depletion chain file configured in the backend.</p>
        </section>
      {/if}

      <!-- ── R2S-specific ── -->
      {#if runMode === 'r2s'}
        <section class="section">
          <div class="section-label">R2S settings</div>
          <div class="fields-grid">
            <label class="field wide">
              <span>Neutron source file path (on host)</span>
              <input type="text" placeholder="/path/to/source.h5" bind:value={neutronSrcFile} />
            </label>
          </div>
          <p class="note warning">⚠ R2S requires additional backend configuration. See documentation.</p>
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

.field select:focus {
    outline: none;
    border-color: var(--color-accent);
}
</style>